"""触控识别两级方案。

L1：已知视频对象快速匹配（FrameEvidence claims 的区域框，纯本地<10ms）。
L2：按需识别——截取真实视频帧→本地 Qwen3-VL-4B 定位命名→写回 objects 缓存。
L2 走 MPS 短任务，与 A 端 GPU 长生成错峰（单飞信号量+单次超时）；
置信不足或超时则拒识，不猜。
"""

import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import config

# L2 串行信号量：同一时间只跑一次VLM推理（与A端GPU任务错峰）
_L2_SEM = threading.Semaphore(1)
_L2_LOCK_TS = {'t': 0.0}

ROOT = config.REPO_ROOT
QA_ENV_VLM = ROOT.parent.parent / 'work' / 'models-env' / 'bin' / 'python'
VLM_PROMPT = ('你是绘本视频画面识别助手。用户点击了画面上的一点。'
              '只依据当前帧图片，返回严格JSON，不要Markdown：'
              '{"objects":[{"name":"对象名","description":"可见外观","bbox":[0.0,0.0,1.0,1.0]}]}。'
              'bbox是左上原点的0到1坐标。列出画面中清晰可见的主要对象（最多5个），'
              '把离点击点最近的对象放第一个。看不清楚的对象不要列出。不要编造。')


def _in_bbox(bbox, x, y):
    if not bbox or len(bbox) != 4:
        return False
    x1, y1, x2, y2 = bbox
    return x1 <= x <= x2 and y1 <= y <= y2


def _area(bbox):
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def resolve_touch(db, x, y, clip_id, asset_version, t_ms, window_ms=None):
    """L1：返回命中的对象dict（含bbox_parsed），无命中返回None（由app层决定是否进L2）。"""
    from . import indexer
    candidates = indexer.objects_for_touch(db, clip_id, asset_version, t_ms, window_ms)
    hits = []
    for o in candidates:
        try:
            bbox = json.loads(o['bbox']) if o['bbox'] else None
        except ValueError:
            bbox = None
        if bbox and _in_bbox(bbox, x, y):
            hits.append((o, bbox))
    if not hits:
        return None
    if len(hits) > 1:  # 多命中取最小面积（最精确目标）
        hits.sort(key=lambda hb: _area(hb[1]))
    obj, bbox = hits[0]
    return {**obj, 'bbox_parsed': bbox}


def extract_frame(video_url, t_ms, out_dir):
    """从视频URL按时间截帧（ffmpeg直读HTTP Range流）。返回帧jpg路径或None。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = out_dir / f"frame_{clip_safe(clip_id=None)}_{int(time.time()*1000)}.jpg"
    try:
        subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'error', '-ss', f'{t_ms / 1000:.3f}',
             '-i', video_url, '-frames:v', '1', '-q:v', '3', '-y', str(frame)],
            check=True, timeout=20, capture_output=True)
    except (subprocess.SubprocessError, OSError):
        return None
    return frame if frame.is_file() and frame.stat().st_size > 1000 else None


def clip_safe(clip_id=None):
    import re as _re
    return _re.sub(r'[^a-zA-Z0-9_]', '', clip_id or 'f')


def vlm_locate(frame_path, x, y, a_base, video_asset_id, db, clip_id, asset_version, t_ms):
    """L2：VLM定位点击点附近对象。返回dict(name,description,bbox)或None。失败/超时拒识。"""
    if not QA_ENV_VLM.is_file():
        return None
    with _L2_SEM:  # 错峰：同一时间一次VLM推理
        with tempfile.TemporaryDirectory() as td:
            script = (
                "import json, sys\n"
                "import mlx_vlm\n"  # noqa: F841 models-env有mlx-vlm
                "from mlx_vlm import generate, load\n"
                f"model, processor = load({str(ROOT.parent.parent / 'work' / 'models' / 'Qwen3-VL-4B-Instruct-4bit')!r})\n"
                f"out = generate(model, processor, prompt={VLM_PROMPT!r},"
                f" image={str(frame_path)!r}, max_tokens=400)\n"
                "print(out.text if hasattr(out,'text') else out)\n"
            )
            try:
                proc = subprocess.run(
                    [str(QA_ENV_VLM), '-c', script],
                    capture_output=True, timeout=60,
                    env={'HF_HUB_OFFLINE': '1', 'PATH': '/usr/bin:/bin:/usr/local/bin', 'TMPDIR': td})
            except subprocess.TimeoutExpired:
                return None
            if proc.returncode != 0:
                return None
        # 解析VLM输出里的JSON（宽容解析：取第一个 {...}）
        try:
            text = proc.stdout.decode()
            start = text.find('{')
            end = text.rfind('}')
            payload = json.loads(text[start:end + 1])
            objects = payload.get('objects') or []
        except (ValueError, AttributeError):
            return None
    # 找点击点命中的bbox（或最近的）
    best = None
    for o in objects:
        try:
            bbox = [float(v) for v in (o.get('bbox') or [])]
        except (TypeError, ValueError):
            continue
        if len(bbox) != 4:
            continue
        if _in_bbox(bbox, x, y):
            return dict(name=o.get('name'), description=o.get('description'), bbox=bbox)
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        d = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
        if best is None or d < best[0]:
            best = (d, dict(name=o.get('name'), description=o.get('description'), bbox=bbox))
    if best and best[0] < 0.18:  # 点击点距最近对象中心足够近才接受
        return best[1]
    return None


def cache_l2_result(db, clip_id, asset_version, t_ms, name, description, bbox, session_id):
    """L2结果写回对象缓存（session作用域+版本键），下次L1直接命中。"""
    import uuid as _uuid
    obj_id = f"l2_{_uuid.uuid4().hex[:12]}"
    page_row = db.execute('SELECT page_id, book_id FROM clips WHERE clip_id=? AND asset_version=?',
                          (clip_id, asset_version)).fetchone()
    db.execute('INSERT INTO objects(object_id,book_id,page_id,clip_id,asset_version,t_ms,name,'
               'description,bbox,source,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
               (obj_id, page_row['book_id'] if page_row else None,
                page_row['page_id'] if page_row else None,
                clip_id, asset_version, t_ms, name, description,
                json.dumps(bbox), 'vlm_on_demand', 'needs_review'))
    return obj_id
