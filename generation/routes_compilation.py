import uuid
from fastapi import Request
from .models import CompilationCreate,CompilationReview
from .compilation import checked_clips
from .store import digest
from .workflow import new_run,now


def register_compilation_routes(app,store,require,envelope,APIError):
    @app.post('/v1/books/{book_id}/compilations',status_code=202)
    def create_compilation(book_id:str,body:CompilationCreate,request:Request):
        book=require('book',book_id)
        payload=dict(book_id=book_id,items=[i.model_dump() for i in body.items]);key='compilation:'+body.idempotency_key
        with store.connect() as db:
            previous=db.execute('SELECT hash,run_id FROM requests WHERE key=?',(key,)).fetchone()
            if previous:
                if previous[0]!=digest(payload):raise APIError('IDEMPOTENCY_CONFLICT','同一幂等键用于不同连续视频。',409)
                return envelope(request,dict(run_id=previous[1]),True)
            try:checked_clips(store,payload['items'],book_id,db)
            except ValueError:raise APIError('COMPILATION_INPUT_INVALID','须按原书页序提供本书当前已审核片段及版本。',409)
            run=new_run('compilation',payload,book['is_mock']);store.put('run',run.run_id,run,db)
            db.execute('INSERT INTO requests VALUES(?,?,?)',(key,digest(payload),run.run_id))
        return envelope(request,dict(run_id=run.run_id),True)

    @app.get('/v1/books/{book_id}/compilations')
    def compilations(book_id:str,request:Request,include_pending:bool=False):
        require('book',book_id)
        records=[r for r in store.all('compilation') if r['book_id']==book_id and (include_pending or r['status']=='approved')]
        frames=[f for f in store.all('compilation_frame') if any(r['compilation_id']==f['compilation_id'] for r in records) and (include_pending or f['status']=='reviewed')]
        return envelope(request,dict(compilations=records,frame_evidence=frames))

    @app.patch('/v1/compilations/{compilation_id}/review')
    def review(compilation_id:str,body:CompilationReview,request:Request):
        with store.connect() as db:
            record=require('compilation',compilation_id,db)
            if record['asset_version']!=body.expected_asset_version or record['status']!='needs_review':raise APIError('VERSION_CONFLICT','连续视频已变化。',409)
            try:checked_clips(store,record['chapters'],record['book_id'],db)
            except ValueError:raise APIError('STALE_INPUT','来源片段已变化。',409)
            frames=[f for f in store.all('compilation_frame',db) if f['compilation_id']==compilation_id]
            if set(body.claims_by_frame)!={f['frame_id'] for f in frames}:raise APIError('FRAME_REVIEW_REQUIRED','须核对全部连续视频证据帧。',422)
            for frame in frames:
                claims=body.claims_by_frame[frame['frame_id']]
                if not claims or any(c.observed_by!=body.reviewer for c in claims):raise APIError('FRAME_REVIEW_REQUIRED','每帧须记录审核者的实际观察。',422)
                frame.update(status='reviewed',visual_claims=[c.model_dump() for c in claims]);store.put('compilation_frame',frame['frame_id'],frame,db)
            record.update(status='approved',reviewed_by=body.reviewer);store.put('compilation',compilation_id,record,db)
            run=require('run',record['run_id'],db);run.update(status='succeeded',updated_at=now());store.put('run',run['run_id'],run,db)
            store.put('review',uuid.uuid4().hex,dict(compilation_id=compilation_id,at=now(),**body.model_dump()),db)
        return envelope(request,record)
