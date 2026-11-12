"""页语 B方交互服务。

职责：会话管理、问答检索、触控识别、语音链路与A端数据的整合出口。
前端只访问本服务（同源）；本服务在服务端经 httpx 消费A端生成服务(8765)。

启动：work/.venv/bin/python -m uvicorn interaction.app:app --host 127.0.0.1 --port 8801
"""

import uuid

from contextlib import asynccontextmanager
from pathlib import Path

import time

from fastapi import FastAPI, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, indexer, qa, sync, speech as speech_module, touch
from .aclient import AClient, AClientError
from .session_store import SessionStore
from .speech import SpeechError


class InteractionError(Exception):
    def __init__(self, code, message, http=400, retryable=False):
        self.code, self.message, self.http, self.retryable = code, message, http, retryable


class TurnBody(BaseModel):
    session_id: str
    seq: int
    turn_id: str | None = None
    input: dict = Field(default_factory=dict)
    snapshot: dict = Field(default_factory=dict)


class TouchBody(BaseModel):
    session_id: str
    seq: int
    snapshot: dict = Field(default_factory=dict)
    x: float
    y: float


class CommandBody(BaseModel):
    session_id: str
    seq: int
    command: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.a = AClient()
    app.state.db = sync.connect()
    app.state.index_ready = False
    # 启动时同步一次（A端不可达不阻塞服务启动）
    try:
        result = sync.sync_once(app.state.db, app.state.a)
        app.state.index_ready = True
        if result['rebuilt']:
            print(f'[sync] 初始索引完成: {result["stats"]}')
    except Exception as exc:
        print(f'[sync] 初始同步失败（服务照常启动，后台将继续重试）: {exc}')
    sync.start_background(app.state.db, app.state.a)
    yield
    app.state.a.close()


def create_app():
    app = FastAPI(
        title='页语 · B方交互服务',
        version='0.1.0',
        description='智能伴读交互服务。前端唯一入口；A端生成数据经服务端代理与索引。',
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
        allow_methods=['GET', 'POST', 'PATCH', 'OPTIONS'],
        allow_headers=['Content-Type', 'X-Reading-Session'],
        expose_headers=['X-Request-ID', 'Content-Range'],
    )

    def env(request, data=None, status='ok', error=None, warnings=None):
        return dict(request_id=getattr(request.state, 'request_id', 'req_' + uuid.uuid4().hex),
                    status=status, data=data, error=error, warnings=warnings or [])

    @app.middleware('http')
    async def request_id(request: Request, call_next):
        request.state.request_id = 'req_' + uuid.uuid4().hex
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        return response

    @app.exception_handler(AClientError)
    async def a_error(request: Request, exc: AClientError):
        return JSONResponse(status_code=exc.http,
                            content=env(request, error=dict(code=exc.code, message=exc.message,
                                                            retryable=exc.retryable), status='error'))

    @app.exception_handler(InteractionError)
    async def i_error(request: Request, exc: InteractionError):
        return JSONResponse(status_code=exc.http,
                            content=env(request, error=dict(code=exc.code, message=exc.message,
                                                            retryable=exc.retryable), status='error'))

    app.state.sessions = SessionStore()

    # ---------- 健康与状态 ----------

    @app.get('/v1/health')
    def health(request: Request):
        a = app.state.a.health()
        return env(request, dict(version='0.1.0', a_side=bool(a),
                                 a_version=(a or {}).get('version'),
                                 index_ready=app.state.index_ready,
                                 demo_mode=config.DEMO_MODE, allow_mock=config.ALLOW_MOCK))

    @app.get('/v1/index/status')
    def index_status(request: Request):
        db = app.state.db
        counts = {t: db.execute(f'SELECT COUNT(*) c FROM {t}').fetchone()['c']
                  for t in ('books', 'pages', 'facts', 'entities', 'events', 'clips', 'frames',
                            'objects', 'line_texts')}
        last = db.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
        return env(request, dict(**counts, last_sync=last['value'] if last else None,
                                 allow_mock=config.ALLOW_MOCK))

    # ---------- 书目与清单（本地索引优先） ----------

    @app.get('/v1/books')
    def books(request: Request):
        rows = []
        for r in app.state.db.execute('SELECT * FROM books ORDER BY is_mock, book_id'):
            if r['is_mock'] and not config.ALLOW_MOCK:
                continue
            rows.append(dict(book_id=r['book_id'], title=r['title'], is_mock=bool(r['is_mock']),
                             page_count=r['page_count'], revision=r['revision'],
                             rights_note='演示数据' if r['is_mock'] else '开发调试样本 · 非组委会授权绘本'))
        if not app.state.index_ready:
            return JSONResponse(status_code=503,
                                content=env(request, error=dict(code='INDEX_NOT_READY',
                                            message='索引尚未建立，请稍后重试。', retryable=True),
                                            status='error'))
        return env(request, rows,
                   warnings=['样书仅用于内部开发调试，正式评测须使用组委会授权绘本。'] if rows else None)

    @app.get('/v1/books/{book_id}/clips')
    def clips(book_id: str, request: Request, include_pending: bool = False):
        rows = indexer.visible_clips(app.state.db, book_id, include_pending)
        frames = {}
        for fr in app.state.db.execute(
                "SELECT * FROM frames WHERE book_id=? AND status='reviewed'", (book_id,)):
            frames.setdefault((fr['clip_id'], fr['asset_version']), []).append(dict(fr))
        data = []
        for c in rows:
            if include_pending and c['status'] not in ('approved', 'needs_review', 'failed'):
                continue
            if not include_pending and c['status'] != 'approved':
                continue
            item = {k: c[k] for k in ('clip_id', 'asset_version', 'book_id', 'page_id',
                                      'duration_ms', 'status', 'is_mock', 'asset_id',
                                      'audio_asset_id', 'subtitle_asset_id', 'motion_status')}
            item['is_mock'] = bool(item['is_mock'])
            fe = frames.get((c['clip_id'], c['asset_version']), [])
            item['frame_evidence'] = [dict(f_id=f['frame_id'], t_ms=f['t_ms'],
                                           image_asset_id=f['image_asset_id'],
                                           claims=json_loads_safe(f['claims'])) for f in fe]
            data.append(item)
        return env(request, dict(clips=data, frame_evidence=[f for c in data for f in c['frame_evidence']]),
                   warnings=(['MOCK测试素材，不代表AI视频生成。']
                             if any(c['is_mock'] for c in data) else None))

    @app.get('/v1/books/{book_id}/manifest')
    def manifest(book_id: str, request: Request):
        # 校对页需要完整manifest结构（OCR行/区域/版本），直接代理A端
        data = app.state.a.get(f'/v1/books/{book_id}/manifest')
        return env(request, data)

    # ---------- 检索调试接口（问答QA的底层入口） ----------

    @app.get('/v1/search')
    def search(request: Request, q: str, book_id: str = None,
               clip_id: str = None, t_ms: int = None, window_ms: int = None):
        db = app.state.db
        return env(request, dict(
            facts=indexer.search_facts(db, q, book_id),
            ocr=indexer.search_ocr(db, q, book_id),
            frames=indexer.search_frames(db, q, book_id, clip_id, t_ms, window_ms),
            events=indexer.search_events(db, q, book_id),
        ))

    # ---------- 会话与交互（B端手册核心接口） ----------

    def require_session(request, body=None):
        sid = request.headers.get('x-reading-session')
        s = app.state.sessions.get(sid) if sid else None
        if not s:
            raise InteractionError('SESSION_NOT_FOUND', '找不到会话，请刷新页面。', 404)
        if body is not None and body.get('session_id') != sid:
            raise InteractionError('SESSION_MISMATCH', '会话标识不一致。', 404)
        return s

    def check_seq(s, body):
        seq = body.get('seq')
        if not isinstance(seq, int) or seq <= s.seq:
            raise InteractionError('STALE_SEQ', '该操作已被更新的操作替代。', 409)
        s.seq = seq

    def db_conn():
        return app.state.db

    @app.post('/v1/sessions')
    def create_session(request: Request):
        try:
            s = app.state.sessions.create()
        except OverflowError:
            raise InteractionError('SESSION_FULL', '会话已满，请稍后再试。', 503)
        return env(request, dict(id=s.id), status='accepted')

    @app.post('/v1/turns')
    def turns(body: TurnBody, request: Request):
        s = require_session(request, body.model_dump())
        check_seq(s, body.model_dump())
        db = db_conn()
        text = (body.input or {}).get('text', '')
        if not isinstance(text, str) or not text.strip() or len(text) > 160:
            raise InteractionError('BAD_QUESTION', '请输入 1–160 字的问题。', 400)
        snap = body.snapshot or {}
        clip_id, version = snap.get('clip_id'), snap.get('asset_version')
        if clip_id and version is not None:
            row = db.execute('SELECT 1 FROM clips WHERE clip_id=? AND asset_version=?',
                             (clip_id, version)).fetchone()
            if not row:
                raise InteractionError('CLIP_CHANGED', '片段已变更，请刷新书页。', 409)
        s.last_snapshot = snap
        t0 = time.perf_counter()
        result = qa.answer(db, text, snap.get('book_id'), snap, s.entity)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if result['kind'] == 'command':
            return env(request, dict(kind='command', command=result['command'],
                                     turn_id=body.turn_id, seq=s.seq, status='command'))
        answer_id = 'ans_' + uuid.uuid4().hex
        record = dict(answer_id=answer_id, turn_id=body.turn_id, question=text,
                      answer_text=result['answer_text'], status=result['status'],
                      evidence=result['evidence'], method=result.get('method'),
                      elapsed_ms=elapsed_ms, snapshot=snap, seq=s.seq, kind='answer')
        s.push_history(record)
        return env(request, record)

    @app.post('/v1/audio')
    async def audio_turn(request: Request, audio: UploadFile, session_id: str, seq: int,
                         turn_id: str | None = None, book_id: str | None = None,
                         clip_id: str | None = None, asset_version: int | None = None,
                         media_time_ms: int | None = None):
        """按住说话：录音上传 → 本地ASR → 意图/问答 → 回答（含ASR文本与耗时）。"""
        s = require_session(request, {'session_id': session_id})
        check_seq(s, {'seq': seq})
        db = db_conn()
        data = await audio.read()
        suffix = '.' + (audio.filename or '').split('.')[-1].lstrip('.') if '.' in (audio.filename or '') else '.webm'
        try:
            asr = speech_module.transcribe(data, suffix if suffix in ('.webm', '.m4a', '.mp3', '.wav', '.mp4') else '.webm')
        except SpeechError as exc:
            raise InteractionError(exc.code, exc.message, exc.http)
        text = asr['text']
        if not text:
            raise InteractionError('ASR_EMPTY', '没有听清，请重试或输入文字。', 422)
        snap = dict(book_id=book_id, clip_id=clip_id, asset_version=asset_version,
                    media_time_ms=media_time_ms or 0)
        if clip_id and asset_version is not None:
            row = db.execute('SELECT 1 FROM clips WHERE clip_id=? AND asset_version=?',
                             (clip_id, asset_version)).fetchone()
            if not row:
                raise InteractionError('CLIP_CHANGED', '片段已变更，请刷新书页。', 409)
        s.last_snapshot = snap
        t0 = time.perf_counter()
        result = qa.answer(db, text, book_id, snap, s.entity)
        qa_ms = int((time.perf_counter() - t0) * 1000)
        if result['kind'] == 'command':
            return env(request, dict(kind='command', command=result['command'], turn_id=turn_id,
                                     seq=s.seq, status='command', asr_text=text,
                                     asr_elapsed_ms=asr['elapsed_ms']))
        answer_id = 'ans_' + uuid.uuid4().hex
        record = dict(answer_id=answer_id, turn_id=turn_id, question=text,
                      answer_text=result['answer_text'], status=result['status'],
                      evidence=result['evidence'], method=result.get('method'),
                      elapsed_ms=qa_ms, asr_elapsed_ms=asr['elapsed_ms'],
                      snapshot=snap, seq=s.seq, kind='answer',
                      input_kind='audio')
        s.push_history(record)
        return env(request, record)

    @app.post('/v1/touches')
    def touches(body: TouchBody, request: Request):
        s = require_session(request, body.model_dump())
        check_seq(s, body.model_dump())
        db = db_conn()
        x, y = body.x, body.y
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise InteractionError('BAD_COORD', '点选坐标无效。', 400)
        snap = body.snapshot or {}
        clip_id, version = snap.get('clip_id'), snap.get('asset_version')
        t_ms = snap.get('media_time_ms', 0)
        obj = touch.resolve_touch(db, x, y, clip_id, version, t_ms)
        if not obj and clip_id and version is not None:
            # L2：按需截帧→VLM定位（MPS短任务，与A端GPU错峰）；失败拒识
            row = db.execute('SELECT asset_id, book_id FROM clips WHERE clip_id=? AND asset_version=?',
                             (clip_id, version)).fetchone()
            if row and row['asset_id']:
                frame = touch.extract_frame(
                    f"http://127.0.0.1:8765/v1/assets/{row['asset_id']}", t_ms,
                    config.FRAME_CACHE_DIR)
                if frame:
                    named = touch.vlm_locate(frame, x, y, config.A_BASE_URL, row['asset_id'],
                                             db, clip_id, version, t_ms)
                    if named and named.get('name'):
                        obj_id = touch.cache_l2_result(db, clip_id, version, t_ms,
                                                       named['name'], named.get('description'),
                                                       named.get('bbox'), s.id)
                        s.entity = dict(name=_full_entity_name(db, named['name'], row['book_id']),
                                        description=named.get('description'), object_id=obj_id,
                                        clip_id=clip_id, asset_version=version,
                                        t_ms=t_ms, bbox=named.get('bbox'))
                        return env(request, dict(
                            object=dict(name=named['name'], description=named.get('description'),
                                        bbox=named.get('bbox'), source='vlm_on_demand', t_ms=t_ms),
                            evidence=dict(id=obj_id, source='frame', clip_id=clip_id,
                                          version=version, ms=t_ms),
                            note='本对象由视觉模型按需识别，已写入缓存待人工复核。'))
        if not obj:
            s.entity = None
            return env(request, dict(object=None, evidence=None,
                                     hint='这里没有可确认的对象，请点一下轮廓清楚的位置。'))
        s.entity = dict(name=_full_entity_name(db, obj.get('name'), snap.get('book_id')),
                        description=obj.get('description'),
                        object_id=obj['object_id'], clip_id=clip_id, asset_version=version,
                        t_ms=obj.get('t_ms', t_ms), bbox=obj.get('bbox_parsed'))
        return env(request, dict(
            object=dict(name=obj.get('name'), description=obj.get('description'),
                        bbox=obj.get('bbox_parsed'), source=obj.get('source'),
                        t_ms=obj.get('t_ms', t_ms)),
            evidence=dict(id=obj['object_id'], source='frame', clip_id=clip_id,
                          version=version, ms=obj.get('t_ms', t_ms))))

    @app.post('/v1/sessions/{session_id}/commands')
    def commands(session_id: str, body: CommandBody, request: Request):
        s = require_session(request, body.model_dump())
        if session_id != s.id:
            raise InteractionError('SESSION_MISMATCH', '会话标识不一致。', 404)
        check_seq(s, body.model_dump())
        allowed = ('pause', 'resume', 'cancel', 'page', 'clear',
                   'page_prev', 'page_next', 'replay')
        if body.command not in allowed:
            raise InteractionError('BAD_COMMAND', '不支持此命令', 400)
        if body.command in ('page', 'page_prev', 'page_next', 'clear'):
            s.entity = None
        if body.command == 'clear':
            s.history = []
        return env(request, dict(command=body.command))

    @app.get('/v1/sessions/{session_id}/history')
    def history(session_id: str, request: Request):
        s = require_session(request)
        if session_id != s.id:
            raise InteractionError('SESSION_MISMATCH', '会话标识不一致。', 404)
        return env(request, list(reversed(s.history)))

    @app.get('/v1/answers/{answer_id}')
    def answers(answer_id: str, request: Request):
        s = require_session(request)
        for h in s.history:
            if h['answer_id'] == answer_id:
                return env(request, h)
        raise InteractionError('NOT_FOUND', '找不到回答。', 404)

    # ---------- 管理页支持：文件上传 + A端写接口转发 ----------

    ALLOWED_SUFFIX = {'.pdf', '.epub', '.png', '.jpg', '.jpeg'}

    @app.post('/v1/upload')
    async def upload_book(request: Request, file: UploadFile, title: str):
        """管理页上传入口：保存到A端 data/inbox（A端不直接收浏览器文件），再建书。"""
        filename = (file.filename or 'book.pdf').replace('/', '_').replace('\\', '_')
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIX:
            raise InteractionError('UNSUPPORTED_FORMAT', '支持 PDF/EPUB/PNG/JPEG。', 422)
        if not title.strip():
            raise InteractionError('BAD_TITLE', '请填写书名。', 400)
        inbox = config.REPO_ROOT / 'data' / 'inbox'
        inbox.mkdir(parents=True, exist_ok=True)
        data = await file.read()
        if len(data) > 60 * 1024 * 1024:
            raise InteractionError('FILE_TOO_LARGE', '文件超过60MB限制。', 413)
        target = inbox / filename
        target.write_bytes(data)
        created = app.state.a.post('/v1/books', json=dict(title=title.strip()[:200], source_name=filename))
        return env(request, dict(book_id=created['book_id'], run_id=created['run_id'],
                                 file=filename), status='accepted')

    @app.api_route('/aapi/{path:path}', methods=['GET', 'POST', 'PATCH'])
    async def aapi_proxy(request: Request, path: str):
        """管理页专用A端透传（GET/POST/PATCH）。响应Envelope原样转发。"""
        import httpx as _httpx
        body = await request.body()
        try:
            resp = app.state.a._client.request(
                request.method, f"{app.state.a.base_url}/{path}", content=body,
                headers={'Content-Type': request.headers.get('content-type', 'application/json')})
            payload = resp.json()
            return JSONResponse(status_code=resp.status_code, content=payload)
        except _httpx.HTTPError:
            raise InteractionError('A_UNREACHABLE', 'A端服务不可达。', 502)

    # ---------- 媒体代理（支持Range，供视频播放） ----------

    @app.get('/media/{asset_id}')
    def media(asset_id: str, request: Request):
        range_header = request.headers.get('range')
        upstream = app.state.a.stream_asset(asset_id, range_header)
        passthrough = ('content-type', 'content-length', 'content-range', 'accept-ranges', 'etag')
        headers = {k: v for k, v in upstream.headers.items() if k.lower() in passthrough}
        return StreamingResponse(upstream.iter_bytes(), status_code=upstream.status_code,
                                 headers=headers, background=None)

    # ---------- 前端静态托管（vite build 产物；开发态走5173代理） ----------

    @app.get('/')
    def index():
        if config.WEB_DIST.joinpath('index.html').is_file():
            return FileResponse(config.WEB_DIST / 'index.html')
        return JSONResponse(dict(hint='前端未构建。开发请运行 cd web && npm install && npm run dev；'
                                       '演示请运行 npm run build 后重启本服务。'))

    assets_dir = config.WEB_DIST / 'assets'
    if assets_dir.is_dir():
        app.mount('/assets', StaticFiles(directory=assets_dir), name='web-assets')

    # SPA history fallback：前端路由（/read/xx、/review/xx 等）统一回index.html
    @app.get('/{full_path:path}')
    def spa_fallback(full_path: str, request: Request):
        if config.WEB_DIST.joinpath('index.html').is_file():
            return FileResponse(config.WEB_DIST / 'index.html')
        return JSONResponse(dict(hint=f'未找到 {full_path}；前端未构建。'), status_code=404)

    return app


def json_loads_safe(text):
    import json
    try:
        return json.loads(text or '[]')
    except ValueError:
        return []


def _full_entity_name(db, fragment, book_id):
    """由claim匹配出的名称片段还原完整实体名（如"猫"→"穿靴子的猫"）。"""
    if not fragment:
        return None
    row = db.execute(
        "SELECT entity_id, name FROM entities WHERE book_id=? AND (name=? OR name LIKE ?)"
        " ORDER BY LENGTH(name) LIMIT 1",
        (book_id, fragment, f'%{fragment}')).fetchone()
    return row['name'] if row else fragment


app = create_app()
