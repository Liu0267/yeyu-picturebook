import os
import hashlib
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException

from .models import (BookCreate, BookManifest, ClipCreate, ClipReview, Envelope,NarrationCreate,
                     PageReview, ShotReview, ShotSpec)
from .store import Store, digest
from .workflow import grounded_shots, invalidate, new_run, now


class APIError(Exception):
    def __init__(self, code, message, http=400, retryable=False):
        self.code, self.message, self.http, self.retryable = code, message, http, retryable


def create_app(data_dir=None):
    store = Store(data_dir or os.getenv('YU_DATA_DIR', Path(__file__).resolve().parents[1]/'data'))
    app = FastAPI(title='页语 · A方生成服务', version='0.4.0', description='本地开发接口。MOCK 与真实素材分开；所有时间单位为毫秒。')
    app.state.store = store
    app.add_middleware(CORSMiddleware, allow_origins=os.getenv('YU_CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(','),
                       allow_methods=['GET','POST','PATCH','OPTIONS'], allow_headers=['Content-Type'], expose_headers=['ETag','X-Request-ID'])

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'req_' + uuid.uuid4().hex
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        return response

    def envelope(request, data=None, accepted=False, warnings=None):
        return Envelope(request_id=request.state.request_id, status='accepted' if accepted else 'ok',
                        data=data, warnings=warnings or []).model_dump(mode='json')

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return JSONResponse(status_code=exc.http, content=dict(request_id=request.state.request_id, status='error', data=None,
                            error=dict(code=exc.code, message=exc.message, retryable=exc.retryable), warnings=[]))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return await api_error(request, APIError('VALIDATION_ERROR', '请求字段不符合 OpenAPI 合同。', 422))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return await api_error(request, APIError('HTTP_ERROR', str(exc.detail), exc.status_code))

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return await api_error(request, APIError('INTERNAL_ERROR', '本地服务处理失败。', 500))

    def require(kind, key, db=None):
        value = store.get(kind, key, db)
        if value is None:
            raise APIError('NOT_FOUND', '对象不存在。', 404)
        return value

    @app.get('/v1/health', response_model=Envelope)
    def health(request: Request):
        return envelope(request, dict(version='0.4.0', video_provider=os.getenv('YU_VIDEO_PROVIDER','unconfigured'),
                        local_vlm_configured=bool(os.getenv('YU_VLM_MODEL_DIR')),local_tts_configured=bool(os.getenv('YU_TTS_MODEL_DIR'))))

    @app.get('/v1/books', response_model=Envelope)
    def books(request: Request):
        return envelope(request, [dict(book_id=b['book_id'], title=b['title'], is_mock=b['is_mock'],
                                      page_count=len(b['pages']), revision=b['revision']) for b in store.all('book')])

    @app.post('/v1/books', response_model=Envelope, status_code=202)
    def create_book(body: BookCreate, request: Request):
        if Path(body.source_name).name != body.source_name or body.source_name in ('.', '..'):
            raise APIError('INVALID_SOURCE', '仅允许 data/inbox 下的文件名。')
        inbox = (store.root/'inbox').resolve()
        path = (inbox/body.source_name).resolve()
        if not path.is_relative_to(inbox) or not path.is_file():
            raise APIError('SOURCE_NOT_FOUND', '请先将输入文件放入 data/inbox。', 404)
        if path.suffix.lower() not in ('.pdf','.epub','.png','.jpg','.jpeg'):
            raise APIError('UNSUPPORTED_FORMAT', '支持 PDF/EPUB/PNG/JPEG。', 422)
        book_id = 'book_' + uuid.uuid4().hex
        source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        run = new_run('ingest', dict(book_id=book_id, title=body.title, source_name=body.source_name, source_sha256=source_sha256))
        store.put('run', run.run_id, run)
        return envelope(request, dict(book_id=book_id, run_id=run.run_id), True)

    @app.get('/v1/books/{book_id}/manifest', response_model=Envelope)
    def manifest(book_id: str, request: Request):
        return envelope(request, require('book', book_id))

    @app.post('/v1/pages/{page_id}/understand',response_model=Envelope,status_code=202)
    def understand(page_id:str,request:Request,source_ref_id:str|None=None):
        found=[(b,p) for b in store.all('book') for p in b['pages'] if p['page_id']==page_id]
        if not found:raise APIError('NOT_FOUND','书页不存在。',404)
        book,page=found[0]
        payload=dict(book_id=book['book_id'],page_id=page_id,page_revision=page['page_revision'])
        if source_ref_id:
            ref=next((r for r in book['source_refs'] if r['ref_id']==source_ref_id and r['page_id']==page_id and r['kind']=='original_image'),None)
            if not ref:raise APIError('BAD_REFERENCE','插图来源必须是当前页的original_image区域。',422)
            payload.update(source_ref_id=source_ref_id,source_region=ref['region'])
        run=new_run('page_understanding',payload,book['is_mock'])
        store.put('run',run.run_id,run)
        return envelope(request,dict(run_id=run.run_id),True)

    @app.get('/v1/pages/{page_id}/understandings',response_model=Envelope)
    def understandings(page_id:str,request:Request):
        return envelope(request,[r for r in store.all('understanding') if r['page_id']==page_id],
                        warnings=['模型观察为待审核草稿，不自动写入原书事实。'])

    @app.post('/v1/clips/{clip_id}/narration',response_model=Envelope,status_code=202)
    def narrate(clip_id:str,body:NarrationCreate,request:Request):
        clip=require('clip',clip_id)
        if clip['asset_version']!=body.expected_asset_version or clip['status'] in ('expired','failed'):
            raise APIError('VERSION_CONFLICT','片段已过期或版本改变。',409)
        run=new_run('narration_render',dict(clip_id=clip_id,asset_version=clip['asset_version']),clip['is_mock'])
        store.put('run',run.run_id,run)
        return envelope(request,dict(run_id=run.run_id),True)

    @app.post('/v1/shots/{shot_id}/narration',response_model=Envelope,status_code=202)
    def shot_narration(shot_id:str,request:Request):
        shot=require('shot',shot_id)
        book=require('book',shot['book_id'])
        page=next(p for p in book['pages'] if p['page_id']==shot['page_id'])
        if shot['status']!='approved' or page['page_revision']!=shot['page_revision']:
            raise APIError('REVIEW_REQUIRED','分镜未审核或已过期。',409)
        run=new_run('narration',dict(shot=shot),book['is_mock'])
        store.put('run',run.run_id,run)
        return envelope(request,dict(run_id=run.run_id),True)

    @app.get('/v1/books/{book_id}/narrations',response_model=Envelope)
    def narrations(book_id:str,request:Request):
        require('book',book_id)
        return envelope(request,[r for r in store.all('narration') if r['book_id']==book_id],warnings=['音频需人工试听审核。'])

    @app.patch('/v1/pages/{page_id}/review', response_model=Envelope)
    def review_page(page_id: str, body: PageReview, request: Request):
        with store.connect() as db:
            found = [(b,p) for b in store.all('book', db) for p in b['pages'] if p['page_id']==page_id]
            if not found:
                raise APIError('NOT_FOUND', '书页不存在。', 404)
            book, page = found[0]
            if page['page_revision'] != body.expected_revision:
                raise APIError('VERSION_CONFLICT', '书页已修改，请重新读取版本。', 409)
            lines = {l['line_id']: l for l in page['ocr_lines']}
            facts = {f['fact_id']: f for f in book['facts'] if f['page_id']==page_id}
            if body.reading_order is not None and (len(body.reading_order)!=len(lines) or set(body.reading_order)!=set(lines)):
                raise APIError('BAD_REFERENCE','阅读顺序须完整且不重复地包含当前页所有行ID。',422)
            if not set(body.corrections) <= lines.keys() or not set(body.reviewed_fact_ids) <= facts.keys():
                raise APIError('BAD_REFERENCE', '校对引用必须属于当前页。', 422)
            store.put('history', f"{book['book_id']}_r{book['revision']}", book, db)
            for key, value in body.corrections.items():
                lines[key]['confirmed_text'] = value
            if body.reading_order is not None:page['ocr_lines']=[lines[k] for k in body.reading_order]
            if body.corrections or body.reading_order is not None:
                for fact in facts.values():
                    fact.update(status='draft', reviewed_by=None)
            for key in body.reviewed_fact_ids:
                if facts[key]['kind']=='inference':
                    raise APIError('INFERENCE_CANNOT_BE_FACT', '推断须继续标为草稿。', 422)
                facts[key].update(status='reviewed', reviewed_by=body.reviewer)
            page['page_revision'] += 1
            page['review_status'] = 'reviewed'
            book['revision'] += 1
            BookManifest.model_validate(book)
            store.put('book', book['book_id'], book, db)
            clips = [c for c in store.all('clip', db) if c['page_id']==page_id and c['status']!='expired']
            invalidate(store, clips, db)
            for shot in store.all('shot', db):
                if shot['page_id']==page_id:
                    shot['status']='needs_review'
                    store.put('shot', shot['shot_id'], shot, db)
            for observation in store.all('understanding',db):
                if observation['page_id']==page_id:
                    observation['status']='expired'
                    store.put('understanding',observation['understanding_id'],observation,db)
            for narration in store.all('narration',db):
                if narration['page_id']==page_id:
                    narration['status']='expired'
                    store.put('narration',narration['narration_id'],narration,db)
            audit = dict(page_id=page_id, reviewer=body.reviewer, at=now(), patch=body.model_dump(),
                         new_revision=page['page_revision'])
            store.put('review', uuid.uuid4().hex, audit, db)
        return envelope(request, dict(page=page, invalidated_clip_ids=[c['clip_id'] for c in clips]),
                        warnings=['旧片段与帧证据已过期；请按新页版本重新制作分镜。'])

    @app.get('/v1/books/{book_id}/storyboard', response_model=Envelope)
    def storyboard(book_id: str, request: Request):
        require('book', book_id)
        return envelope(request, [s for s in store.all('shot') if s['book_id']==book_id])

    @app.post('/v1/books/{book_id}/storyboard', response_model=Envelope)
    def build_storyboard(book_id: str, request: Request):
        book = BookManifest.model_validate(require('book', book_id))
        shots = grounded_shots(book)
        for shot in shots:
            store.put('shot', shot.shot_id, shot)
        return envelope(request, [s.model_dump() for s in shots], warnings=['规则式分镜草稿，待审核；未调用VLM/LLM。'])

    @app.post('/v1/clips', response_model=Envelope, status_code=202)
    def create_clip(body: ClipCreate, request: Request):
        config = body.model_dump(exclude={'idempotency_key'})
        with store.connect() as db:
            previous = db.execute('SELECT hash,run_id FROM requests WHERE key=?', (body.idempotency_key,)).fetchone()
            if previous:
                if previous[0] != digest(config):
                    raise APIError('IDEMPOTENCY_CONFLICT', '同一幂等键用于不同请求。', 409)
                return envelope(request, dict(run_id=previous[1]), True)
            shot = ShotSpec.model_validate(require('shot', body.shot_id, db))
            book = BookManifest.model_validate(require('book', shot.book_id, db))
            page = next(p for p in book.pages if p.page_id==shot.page_id)
            if page.page_revision != shot.page_revision:
                raise APIError('STALE_INPUT', '分镜页版本已过期。', 409)
            if (body.mode=='mock') != book.is_mock:
                raise APIError('MOCK_SCOPE_MISMATCH', 'Mock生成仅用于合成Mock书，真实绘本使用real模式。', 422)
            if shot.status!='approved' or page.review_status!='reviewed':
                raise APIError('REVIEW_REQUIRED', '请先审核书页和分镜。', 409)
            run = new_run('video_generation', dict(shot=shot.model_dump()), book.is_mock)
            store.put('run', run.run_id, run, db)
            db.execute('INSERT INTO requests VALUES(?,?,?)', (body.idempotency_key, digest(config), run.run_id))
        return envelope(request, dict(run_id=run.run_id), True)

    @app.patch('/v1/shots/{shot_id}/review', response_model=Envelope)
    def review_shot(shot_id: str, body: ShotReview, request: Request):
        with store.connect() as db:
            shot = require('shot', shot_id, db)
            book = require('book', shot['book_id'], db)
            page = next(p for p in book['pages'] if p['page_id']==shot['page_id'])
            if body.expected_page_revision!=page['page_revision'] or shot['page_revision']!=page['page_revision']:
                raise APIError('VERSION_CONFLICT', '分镜原页已变化，请重建分镜。', 409)
            facts = {f['fact_id'] for f in book['facts'] if f['page_id']==page['page_id'] and f['status']=='reviewed'}
            if page['review_status']!='reviewed' or not set(shot['fact_ids'])<=facts:
                raise APIError('REVIEW_REQUIRED', '分镜所依赖的书页及事实尚未通过审核。', 409)
            shot['status']='approved'
            store.put('shot', shot_id, shot, db)
            store.put('review', uuid.uuid4().hex, dict(shot_id=shot_id, reviewer=body.reviewer, at=now()), db)
        return envelope(request, shot)

    @app.get('/v1/runs/{run_id}', response_model=Envelope)
    def get_run(run_id: str, request: Request):
        return envelope(request, require('run', run_id))

    @app.post('/v1/runs/{run_id}/cancel', response_model=Envelope)
    def cancel(run_id: str, request: Request):
        with store.connect() as db:
            run = require('run', run_id, db)
            if run['status']!='queued':
                raise APIError('CANNOT_CANCEL', '当前仅支持取消未启动任务。', 409)
            run.update(status='cancelled', updated_at=now())
            store.put('run', run_id, run, db)
        return envelope(request, run)

    @app.post('/v1/runs/{run_id}/retry', response_model=Envelope, status_code=202)
    def retry(run_id: str, request: Request):
        with store.connect() as db:
            run = require('run', run_id, db)
            if run['status']!='failed' or not run['error']['retryable'] or run['attempts']>=run['max_attempts']:
                raise APIError('RETRY_NOT_ALLOWED', '此任务不可重试或已达次数上限。', 409)
            run.update(status='queued', error=None, updated_at=now())
            store.put('run', run_id, run, db)
        return envelope(request, dict(run_id=run_id), True)

    @app.get('/v1/books/{book_id}/clips', response_model=Envelope)
    def clips(book_id: str, request: Request, include_pending: bool=False):
        book = require('book', book_id)
        records = [c for c in store.all('clip') if c['book_id']==book_id and
                   (c['status']=='approved' or include_pending)]
        frames = [f for f in store.all('frame') if any(c['clip_id']==f['clip_id'] and c['asset_version']==f['asset_version'] for c in records)
                  and (f['status']=='reviewed' or include_pending)]
        return envelope(request, dict(clips=records, frame_evidence=frames),
                        warnings=['MOCK测试素材，不代表AI视频生成。'] if book['is_mock'] else [])

    @app.patch('/v1/clips/{clip_id}/review', response_model=Envelope)
    def review_clip(clip_id: str, body: ClipReview, request: Request):
        with store.connect() as db:
            clip = require('clip', clip_id, db)
            if clip['asset_version']!=body.expected_asset_version or clip['status']!='needs_review':
                raise APIError('VERSION_CONFLICT', '片段不可审核或版本已改变。', 409)
            if not clip['is_mock'] and (body.motion_review=='not_assessed' or not body.motion_notes.strip()):
                raise APIError('MOTION_REVIEW_REQUIRED','真实片段须记录实际可感知动作及时间范围；仅有MP4或像素变化不算动作通过。',422)
            book = require('book', clip['book_id'], db)
            page = next(p for p in book['pages'] if p['page_id']==clip['page_id'])
            if page['page_revision']!=clip['page_revision']:
                raise APIError('STALE_INPUT', '片段对应的原页已改变。', 409)
            frames = [f for f in store.all('frame', db) if f['clip_id']==clip_id and f['asset_version']==clip['asset_version']]
            if not frames or set(body.claims_by_frame)!={f['frame_id'] for f in frames}:
                raise APIError('FRAME_REVIEW_REQUIRED', '请核对全部实际抽帧后提供各帧观察。', 422)
            try:
                store.asset_path(clip['asset_id'],verify=True,db=db)
                for frame in frames:
                    store.asset_path(frame['image_asset_id'],verify=True,db=db)
                    if frame['video_sha256']!=store.get('asset',clip['asset_id'],db)['sha256']:raise ValueError('STALE_FRAME')
            except (ValueError,FileNotFoundError):raise APIError('ASSET_INTEGRITY_FAILED','视频或证据帧已损坏或不匹配，不能发布。',409)
            audits=[a for a in store.all('clip_audit',db) if a['clip_id']==clip_id and a['asset_version']==clip['asset_version'] and a['status']=='needs_review']
            findings=[f for a in audits for f in a['findings'] if f['status']=='open']
            if any(not body.audit_resolution_notes.get(f['finding_id'],'').strip() for f in findings):
                raise APIError('QUALITY_FINDING_UNRESOLVED','实际帧审核须逐条说明模型冲突或结构错误的复核结果。',422)
            for audit in audits:
                audit.update(status='reviewed',reviewed_by=body.reviewer)
                for finding in audit['findings']:
                    if finding['status']=='open':finding.update(status='resolved_by_reviewer',resolution_note=body.audit_resolution_notes[finding['finding_id']])
                store.put('clip_audit',audit['audit_id'],audit,db)
                audit_run=require('run',audit['run_id'],db)
                audit_run.update(status='succeeded',updated_at=now());store.put('run',audit_run['run_id'],audit_run,db)
            for frame in frames:
                claims = body.claims_by_frame[frame['frame_id']]
                if not claims or any(c.observed_by!=body.reviewer for c in claims):
                    raise APIError('FRAME_REVIEW_REQUIRED', '每帧需有审核人实际观察记录。', 422)
                frame.update(visual_claims=[c.model_dump() for c in claims], status='reviewed')
                store.put('frame', frame['frame_id'], frame, db)
            motion='not_required' if clip['is_mock'] else body.motion_review
            clip.update(status='approved' if motion!='failed' else 'needs_review', reviewed_by=body.reviewer,motion_status=motion)
            store.put('clip', clip_id, clip, db)
            run = require('run', clip['run_id'], db)
            run.update(status='succeeded' if clip['status']=='approved' else 'needs_review', updated_at=now())
            store.put('run', run['run_id'], run, db)
            store.put('review', uuid.uuid4().hex, dict(clip_id=clip_id, at=now(), **body.model_dump()), db)
        return envelope(request, clip)

    @app.get('/v1/assets/{asset_id}')
    def asset(asset_id: str, request: Request):
        record = require('asset', asset_id)
        try:
            path = store.asset_path(asset_id,verify=True)
        except (ValueError, FileNotFoundError):
            raise APIError('ASSET_UNAVAILABLE', '资产文件不存在或完整性校验失败。', 404)
        return FileResponse(path, media_type=record['media_type'], headers={'ETag': '"'+record['sha256']+'"'})

    from .routes_content import register_content_routes
    register_content_routes(app,store,require,envelope,APIError)
    from .routes_compilation import register_compilation_routes
    register_compilation_routes(app,store,require,envelope,APIError)
    return app


app = create_app()
