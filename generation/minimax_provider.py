"""MiniMax 视频供应商：异步创建 → 轮询 → 下载，带断点恢复与重复计费防护。

与 AgnesVideoProvider 同一套边界约定（generation/providers.py 的 VideoProvider 协议）：
  * 状态写盘（state_minimax.json），同一 run_id 可恢复；
  * 提交结果无法确认时进入 SUBMISSION_UNCERTAIN，**禁止自动重提**；
  * 下载失败只重下，不重建任务；
  * usage 记录外部调用次数与输出秒数，供预算对账。

字段名说明：MiniMax v2 的响应包装（顶层 / task / data）仍以首次真实任务为准，
本模块用 _pick 兼容三种常见包装；首次接入请先跑 4 秒任务并用 MINIMAX_DUMP=1 打印原始响应。
"""
import base64
import hashlib
import io
import json
import os
import re
import time
from pathlib import Path

import httpx
from PIL import Image

from .providers import ProviderError, ProviderNotConfigured, VideoResult

BASE = os.getenv('MINIMAX_BASE_URL', 'https://api.minimax.cn').rstrip('/')
PRICE = {'480P': 0.16, '768P': 0.50, '2K': 0.80}
ALLOWED = {('MiniMax-H3-Lite', '480P'), ('MiniMax-H3-Lite', '768P'),
           ('MiniMax-H3', '768P'), ('MiniMax-H3', '2K')}


def _pick(body, *path):
    for holder in (body, body.get('task') or {}, body.get('data') or {}):
        cur = holder
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                cur = None
                break
            cur = cur[key]
        if cur is not None:
            return cur
    return None


class MiniMaxVideoProvider:
    """shot: {prompt, seconds(4-15), reference_region?, ...}；输出 generated.mp4。"""

    def __init__(self, api_key, model='MiniMax-H3-Lite', resolution='480P', ratio='16:9',
                 timeout_seconds=2400, poll_seconds=8, transport=None, trust_env=False):
        if not api_key:
            raise ProviderNotConfigured('未配置 MINIMAX_API_KEY 或 YU_MINIMAX_KEY_FILE。')
        if (model, resolution) not in ALLOWED:
            raise ProviderNotConfigured(f'{model}+{resolution} 不是合法组合：{sorted(ALLOWED)}')
        self.api_key, self.model, self.resolution, self.ratio = api_key, model, resolution, ratio
        self.timeout_seconds, self.poll_seconds = timeout_seconds, poll_seconds
        self.transport, self.trust_env = transport, trust_env

    # ---- 请求构造 ----
    def _first_frame(self, reference_path, region):
        with Image.open(reference_path) as image:
            image = image.convert('RGB')
            if region:
                image = image.crop((round(region['x1'] * image.width), round(region['y1'] * image.height),
                                    round(region['x2'] * image.width), round(region['y2'] * image.height)))
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()

    def _body(self, shot, reference_path):
        seconds = int(shot.get('seconds', 6))
        if not 4 <= seconds <= 15:
            raise ProviderError('BAD_DURATION', f'MiniMax 单段需 4—15 秒，收到 {seconds}。')
        body = {'model': self.model, 'prompt': str(shot.get('prompt', '')).strip(),
                'duration': seconds, 'resolution': self.resolution.upper(),
                'ratio': self.ratio, 'generate_audio': False}
        if not body['prompt']:
            raise ProviderError('EMPTY_PROMPT', 'shot.prompt 为空。')
        if reference_path and Path(reference_path).is_file():
            body['first_frame_image'] = self._first_frame(reference_path, shot.get('reference_region'))
        return body

    def _error(self, response, payload, body):
        message = str(_pick(payload, 'base_resp', 'status_msg') or response.text[:800])
        for secret in (self.api_key, body.get('first_frame_image', '\x00')):
            if secret:
                message = message.replace(secret, '[redacted]')
        message = re.sub(r'data:[^\s"\']+', '[inline_media]', message)
        return dict(http_status=response.status_code, message=message[:1200],
                    body_sha256=hashlib.sha256(response.content).hexdigest())

    # ---- 主流程 ----
    def generate(self, shot, reference_path, output_dir, run_id):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        state_path = output_dir / 'state_minimax.json'
        state = json.loads(state_path.read_text()) if state_path.is_file() else {}
        body = self._body(shot, reference_path)
        video = output_dir / 'generated.mp4'

        def save():
            temp = state_path.with_suffix('.tmp')
            temp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
            temp.replace(state_path)

        if state.get('status') == 'downloaded' and video.is_file():
            return VideoResult(video, state['task_id'], self.model, state.get('usage', {}))

        state['request_sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        headers = {'Authorization': 'Bearer ' + self.api_key, 'Content-Type': 'application/json'}
        with httpx.Client(timeout=90, transport=self.transport, trust_env=self.trust_env) as client:
            if not state.get('task_id'):
                if state.get('submission_started'):
                    raise ProviderError('SUBMISSION_UNCERTAIN', '提交可能已被受理；先核查平台任务，禁止自动重投。')
                state.update(submission_started=True, status='submitting', submitted_at=time.time())
                save()
                try:
                    response = client.post(f'{BASE}/v2/video_generation', headers=headers, json=body)
                except httpx.HTTPError:
                    state['status'] = 'submission_uncertain'
                    save()
                    raise ProviderError('SUBMISSION_UNCERTAIN', '未确认提交结果，请先核查平台任务。')
                payload = response.json() if response.content else {}
                if os.getenv('MINIMAX_DUMP'):
                    (output_dir / 'minimax_submit_raw.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
                task_id = _pick(payload, 'task_id')
                if not task_id:
                    state['last_error'] = self._error(response, payload, body)
                    state['status'] = 'submission_uncertain' if response.status_code >= 500 else 'rejected'
                    save()
                    raise ProviderError('SUBMISSION_UNCERTAIN', '响应无 task_id；已保留证据，禁止自动重投。')
                state.update(task_id=task_id, status='submitted', submit_http_status=response.status_code)
                save()

            deadline = time.monotonic() + self.timeout_seconds
            while time.monotonic() < deadline:
                try:
                    response = client.get(f'{BASE}/v2/query/video_generation', headers=headers,
                                          params={'task_id': state['task_id']})
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    state['poll_errors'] = state.get('poll_errors', 0) + 1
                    save()
                    time.sleep(self.poll_seconds)
                    continue
                status = str(_pick(payload, 'status') or 'unknown')
                state.update(status=status, polls=state.get('polls', 0) + 1, last_poll_at=time.time())
                save()
                if status in ('Fail', 'Failed', 'failed', 'canceled'):
                    raise ProviderError('MINIMAX_GENERATION_FAILED', '供应商任务失败；保留远端 ID，不自动重投。')
                file_id = _pick(payload, 'file_id')
                if status in ('Success', 'success') and file_id:
                    url = self._download(client, headers, file_id, video)
                    usage = dict(external_calls=state.get('submits', 1), accepted_tasks=1,
                                 output_seconds=int(body['duration']),
                                 cost=round(int(body['duration']) * PRICE.get(self.resolution, 0), 2))
                    state.update(status='downloaded', file_id=file_id, download_url_sha256=hashlib.sha256(url.encode()).hexdigest(),
                                 output_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
                                 completed_at=time.time(), usage=usage)
                    save()
                    return VideoResult(video, state['task_id'], self.model, usage)
                time.sleep(self.poll_seconds)
        raise ProviderError('MINIMAX_POLL_TIMEOUT', '等待远端结果超时；可用同一 run_id 恢复查询。', True)

    def _download(self, client, headers, file_id, video):
        response = client.get(f'{BASE}/v1/files/retrieve', headers=headers, params={'file_id': file_id})
        response.raise_for_status()
        url = _pick(response.json(), 'download_url')
        if not isinstance(url, str) or not url.startswith('https://'):
            raise ProviderError('INVALID_VIDEO_URL', '供应商未返回有效 HTTPS 媒体地址。', True)
        # 媒体 CDN 不带鉴权头，避免把密钥发给第三方域名
        try:
            with httpx.Client(timeout=300, transport=self.transport, follow_redirects=True, trust_env=self.trust_env) as media_client:
                with media_client.stream('GET', url) as media:
                    media.raise_for_status()
                    partial = video.with_suffix('.part')
                    with partial.open('wb') as file:
                        for chunk in media.iter_bytes():
                            file.write(chunk)
                    partial.replace(video)
        except httpx.HTTPError:
            raise ProviderError('VIDEO_DOWNLOAD_FAILED', '视频已完成，下载失败；重试只恢复查询和下载。', True)
        return url


def configured_minimax_provider():
    key = os.getenv('MINIMAX_API_KEY', '')
    if not key and os.getenv('YU_MINIMAX_KEY_FILE'):
        key = Path(os.environ['YU_MINIMAX_KEY_FILE']).read_text().strip()
    return MiniMaxVideoProvider(
        key,
        model=os.getenv('YU_MINIMAX_MODEL', 'MiniMax-H3-Lite'),
        resolution=os.getenv('YU_MINIMAX_RESOLUTION', '480P'),
        ratio=os.getenv('YU_MINIMAX_RATIO', '16:9'),
    )
