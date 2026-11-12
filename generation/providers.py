"""Video provider boundary. Secrets stay in process/private config, never in receipts."""
import base64
import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from PIL import Image


class ProviderNotConfigured(RuntimeError):
    pass


class ProviderError(RuntimeError):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass
class VideoResult:
    path: Path
    call_id: str
    model_version: str
    usage: dict


class VideoProvider(Protocol):
    def generate(self, shot: dict, reference_path: Path, output_dir: Path, run_id: str) -> VideoResult: ...


class UnconfiguredVideoProvider:
    def generate(self, shot, reference_path, output_dir, run_id):
        raise ProviderNotConfigured('等待平台、模型、鉴权配置与实际 API 合同；尚未向任何视频服务提交素材。')


class AgnesVideoProvider:
    """Agnes keyframe API: bounded queue retries; known tasks only resume polling."""
    def __init__(self, api_key, model='agnes-video-2.5-flash', transport=None, poll_seconds=2, timeout_seconds=1200,
                 max_submit_attempts=3, busy_delays=(30,60), trust_env=False):
        if not api_key:
            raise ProviderNotConfigured('未配置 AGNES_API_KEY 或 YU_AGNES_KEY_FILE。')
        self.api_key, self.model = api_key, model
        self.transport = transport
        self.poll_seconds, self.timeout_seconds = poll_seconds, timeout_seconds
        self.max_submit_attempts,self.busy_delays,self.trust_env=max_submit_attempts,busy_delays,trust_env

    def _error_record(self,response,payload,body):
        error=payload.get('error') if isinstance(payload.get('error'),dict) else {}
        detail=payload.get('detail','')
        code=error.get('code') or payload.get('code') or ''
        message=error.get('message') or payload.get('message') or detail or response.text[:1000]
        message=str(message).replace(self.api_key,'[redacted]').replace(body.get('first_frame','\x00'),'[inline_image]')
        message=re.sub(r'data:[^\s"\x27]+','[inline_media]',message)
        return dict(http_status=response.status_code,code=str(code),message=message[:1500],
                    request_id=response.headers.get('x-request-id'),retry_after=response.headers.get('retry-after'),
                    content_type=response.headers.get('content-type'),body_sha256=hashlib.sha256(response.content).hexdigest())

    def _request_body(self, shot, reference_path):
        with Image.open(reference_path) as image:
            image = image.convert('RGB')
            r = shot['reference_region']
            crop = image.crop((round(r['x1']*image.width), round(r['y1']*image.height),
                               round(r['x2']*image.width), round(r['y2']*image.height)))
            crop.thumbnail((1440,1440))
            buffer = io.BytesIO()
            crop.save(buffer, format='JPEG', quality=93)
        reference = buffer.getvalue()
        if shot.get('_consent_reference_sha256') and hashlib.sha256(reference).hexdigest()!=shot['_consent_reference_sha256']:
            raise ProviderError('UPLOAD_CONSENT_MISMATCH','参考裁片与获准发送的图片哈希不一致，未上传。')
        seconds = min(12,max(4,round(shot['target_duration_ms']/1000)))
        prompt = shot['prompt'] + '\nConstraints: ' + shot['negative_prompt']
        # Inline reference support still requires a successful live acceptance; never publish to an unrelated file host.
        ratios={'16:9':16/9,'4:3':4/3,'1:1':1,'3:4':3/4,'9:16':9/16}
        ratio=min(ratios,key=lambda name:abs(ratios[name]-crop.width/crop.height))
        body = dict(model=self.model, prompt=prompt, mode='keyframe', seconds=str(seconds), size='720P',
                    aspect_ratio=ratio, n=1, seed=42,
                    first_frame='data:image/jpeg;base64,'+base64.b64encode(reference).decode())
        return body, reference

    def generate(self, shot, reference_path, output_dir, run_id):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True,exist_ok=True)
        body, reference = self._request_body(shot,reference_path)
        (output_dir/'reference.jpg').write_bytes(reference)
        safe_input = {**body, 'first_frame': {'sha256':hashlib.sha256(reference).hexdigest(),'bytes':len(reference)}}
        payload_hash = hashlib.sha256(json.dumps(safe_input,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        state_path = output_dir/'provider_state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else dict(run_id=run_id, model=self.model, input_hash=payload_hash,
                                                                                request=safe_input, polls=0)
        if state['input_hash']!=payload_hash:
            raise ProviderError('PROVIDER_INPUT_CHANGED','该运行的供应商输入已改变，不能复用原任务。')
        def save():
            temp=state_path.with_suffix('.tmp')
            temp.write_text(json.dumps(state,ensure_ascii=False,indent=2))
            temp.replace(state_path)
        headers={'Authorization':'Bearer '+self.api_key}
        submission_limit=min(self.max_submit_attempts,shot.get('_consent_max_submissions',self.max_submit_attempts))
        video=output_dir/'generated.mp4'
        if state.get('status')=='downloaded' and video.is_file():
            return VideoResult(video,state['video_id'],self.model,state.get('usage',{}))
        with httpx.Client(timeout=90, transport=self.transport,trust_env=self.trust_env) as client:
            if not state.get('video_id'):
                while not state.get('video_id'):
                    if state.get('submission_started') and state['status']!='queue_rejected':
                        raise ProviderError('SUBMISSION_UNCERTAIN','请求可能已受理；先核查平台，禁止自动重复提交。')
                    if state.get('submit_attempts',0)>=submission_limit:
                        raise ProviderError('AGNES_QUEUE_FULL','明确的队列拒绝已达到本轮重试上限，未创建可查询任务。')
                    wait=max(0,state.get('next_submit_at',0)-time.time())
                    while wait>0:
                        time.sleep(min(60,wait))
                        wait=max(0,state['next_submit_at']-time.time())
                    state.update(submission_started=True,status='submitting',submitted_at=time.time(),
                                 submit_attempts=state.get('submit_attempts',0)+1,transport='environment_proxy' if self.trust_env else 'direct')
                    save()
                    try:
                        response=client.post('https://apihub.agnes-ai.com/v1/videos',headers=headers,json=body)
                    except httpx.HTTPError:
                        state['status']='submission_uncertain';save()
                        raise ProviderError('SUBMISSION_UNCERTAIN','未确认提交结果，请先核查平台任务。')
                    state['create_http_status']=response.status_code
                    try:result=response.json()
                    except ValueError:result={}
                    if not isinstance(result,dict):result={}
                    nested=result.get('data') if isinstance(result.get('data'),dict) else {}
                    video_id=result.get('video_id') or nested.get('video_id')
                    if isinstance(video_id,str) and video_id:
                        state.update(video_id=video_id,task_id=result.get('task_id') or result.get('id'),status=result.get('status','queued'))
                        save();break
                    error=self._error_record(response,result,body)
                    state['last_error']=error
                    state.setdefault('submission_responses',[]).append(error)
                    task_id=result.get('task_id') or result.get('id') or nested.get('task_id') or nested.get('id')
                    if task_id:
                        state.update(task_id=task_id,status='submission_uncertain')
                        save()
                        raise ProviderError('SUBMISSION_UNCERTAIN','响应含任务标识但缺少video_id；保留标识待核查，禁止自动重投。')
                    code=error['code'].strip().lower().replace(' ','_')
                    message=error['message'].lower()
                    queue_rejected=(code in ('video_queue_full','video_query_full') or
                                    'video queue is full' in message or 'video query is full' in message)
                    if queue_rejected and response.status_code in (200,429,503):
                        attempt=state['submit_attempts']
                        delay=self.busy_delays[min(attempt-1,len(self.busy_delays)-1)]
                        state.update(status='queue_rejected',next_submit_at=time.time()+delay)
                        save();continue
                    state['status']='submission_uncertain' if response.status_code>=500 or response.is_success else 'rejected'
                    save()
                    if response.is_success:
                        raise ProviderError('MISSING_VIDEO_ID','响应缺少video_id，禁止自动重新提交。')
                    if response.status_code>=500:
                        raise ProviderError('AGNES_SERVICE_UNAVAILABLE',f'Agnes 返回 HTTP {response.status_code} 且未确认任务ID；请先核查平台，未自动重投。')
                    raise ProviderError('AGNES_REQUEST_REJECTED',f'Agnes 拒绝请求（HTTP {response.status_code}），参见本地供应商记录。')
            deadline=time.monotonic()+self.timeout_seconds
            while time.monotonic()<deadline:
                try:
                    response=client.get('https://apihub.agnes-ai.com/agnesapi',headers=headers,
                                        params={'video_id':state['video_id'],'model_name':self.model})
                    response.raise_for_status()
                    result=response.json()
                except (httpx.HTTPError,ValueError):
                    state['poll_errors']=state.get('poll_errors',0)+1
                    save()
                    time.sleep(self.poll_seconds)
                    continue
                state.update(status=result.get('status','unknown'), progress=result.get('progress'), polls=state['polls']+1,
                             last_poll_at=time.time())
                if result.get('usage') is not None:state['usage']=result['usage']
                save()
                if state['status']=='failed':
                    raise ProviderError('AGNES_GENERATION_FAILED','供应商任务失败；保留远端ID，不自动重投。')
                if state['status']=='completed':
                    url=result.get('url')
                    if not isinstance(url,str) or not url.startswith('https://'):
                        raise ProviderError('INVALID_VIDEO_URL','供应商未返回有效HTTPS媒体地址。',True)
                    # Use a separate unauthenticated client: never send Agnes credentials to the media CDN.
                    try:
                        with httpx.Client(timeout=120,transport=self.transport,follow_redirects=True,trust_env=self.trust_env) as downloader:
                            with downloader.stream('GET',url) as media:
                                media.raise_for_status()
                                partial=video.with_suffix('.part')
                                with partial.open('wb') as file:
                                    for chunk in media.iter_bytes():file.write(chunk)
                                partial.replace(video)
                    except httpx.HTTPError:
                        raise ProviderError('VIDEO_DOWNLOAD_FAILED','视频已完成，下载失败；重试只恢复查询和下载。',True)
                    usage=dict(external_calls=state.get('submit_attempts',1),accepted_tasks=1,output_seconds=int(body['seconds']),cost=None)
                    usage.update(state.get('usage',{}))
                    state.update(status='downloaded',output_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
                                 completed_at=time.time(),usage=usage)
                    save()
                    return VideoResult(video,state['video_id'],self.model,usage)
                time.sleep(self.poll_seconds)
        raise ProviderError('AGNES_POLL_TIMEOUT','等待远端结果超时；可使用同一run_id恢复查询。',True)


def configured_video_provider():
    if os.getenv('YU_VIDEO_PROVIDER')=='wan_local':
        from .wan_local import LocalWanVideoProvider
        executable=os.getenv('YU_WAN_EXECUTABLE','')
        model_dir=os.getenv('YU_WAN_MODEL_DIR','')
        if not executable or not model_dir:
            raise ProviderNotConfigured('未配置 YU_WAN_EXECUTABLE 或 YU_WAN_MODEL_DIR。')
        return LocalWanVideoProvider(executable,model_dir,decoder=os.getenv("YU_WAN_DECODER","tae"))
    if os.getenv('YU_VIDEO_PROVIDER','unconfigured')!='agnes':
        return UnconfiguredVideoProvider()
    key=os.getenv('AGNES_API_KEY','')
    if not key and os.getenv('YU_AGNES_KEY_FILE'):
        key=Path(os.environ['YU_AGNES_KEY_FILE']).read_text().strip()
    return AgnesVideoProvider(key,model=os.getenv('YU_AGNES_MODEL','agnes-video-2.5-flash'))

