"""Explicit resumable A workflow. Publication always waits for actual-frame review."""
import uuid
from .models import RunRecord,ShotSpec
from .store import digest
from .workflow import new_run,now

STAGES=['video_generation','narration_render','frame_validation']


def create_pipeline(store,shot_id,key,resume=None):
    if set(resume or {})-set(STAGES):raise ValueError('INVALID_RESUME')
    if any((resume or {}).get(STAGES[i]) and not (resume or {}).get(STAGES[i-1]) for i in range(1,len(STAGES))):raise ValueError('INVALID_RESUME')
    with store.connect() as db:
        existing=store.get('pipeline',key,db)
        if existing:
            if existing['shot_id']!=shot_id or any(existing['stages'].get(k)!=v for k,v in (resume or {}).items()):raise ValueError('IDEMPOTENCY_CONFLICT')
            return existing
        shot=ShotSpec.model_validate(store.get('shot',shot_id,db));book=store.get('book',shot.book_id,db)
        page=next(p for p in book['pages'] if p['page_id']==shot.page_id)
        if shot.status!='approved' or page['review_status']!='reviewed' or page['page_revision']!=shot.page_revision:raise ValueError('REVIEW_REQUIRED')
        facts={f['fact_id'] for f in book['facts'] if f['status']=='reviewed' and f['page_id']==shot.page_id}
        if not set(shot.fact_ids)<=facts:raise ValueError('REVIEW_REQUIRED')
        stages={};previous=None
        for stage in STAGES:
            rid=(resume or {}).get(stage)
            if not rid:break
            run=store.get('run',rid,db)
            if not run or run['step']!=stage or run['status'] not in ('needs_review','succeeded'):raise ValueError('INVALID_RESUME')
            if stage=='video_generation':
                if digest(ShotSpec.model_validate(run['payload']['shot']).model_dump())!=digest(shot.model_dump()):raise ValueError('INVALID_RESUME')
            elif not previous or run['payload']['clip_id']!=previous['outputs'][0]:raise ValueError('INVALID_RESUME')
            stages[stage]=rid;previous=run
        record=dict(pipeline_id=key,shot_id=shot_id,book_id=shot.book_id,page_id=shot.page_id,page_revision=shot.page_revision,
                    shot_hash=digest(shot.model_dump()),status='queued',stages=stages,trace=[dict(node='SourceAndStoryboardReview',status='passed',at=now())],created_at=now(),updated_at=now())
        store.put('pipeline',key,record,db)
        return record


def advance_pipeline(store,identifier):
    with store.connect() as db:
        p=store.get('pipeline',identifier,db)
        if p is None:raise ValueError('NOT_FOUND')
        shot=ShotSpec.model_validate(store.get('shot',p['shot_id'],db)).model_dump();book=store.get('book',p['book_id'],db)
        page=next(x for x in book['pages'] if x['page_id']==p['page_id'])
        if page['page_revision']!=p['page_revision'] or digest(shot)!=p['shot_hash']:
            p.update(status='expired',updated_at=now());store.put('pipeline',identifier,p,db);return p
        previous=None
        for stage in STAGES:
            rid=p['stages'].get(stage)
            if rid:
                run=store.get('run',rid,db)
                if run['status'] in ('queued','running','failed','cancelled','expired'):
                    p.update(status=run['status'],current_node=stage,current_run_id=rid);break
                previous=run;continue
            if stage=='video_generation':payload=dict(shot=shot)
            else:
                clip=store.get('clip',previous['outputs'][0],db)
                if not clip or clip['status'] in ('expired','failed'):raise ValueError('STALE_INPUT')
                payload=dict(clip_id=clip['clip_id'],asset_version=clip['asset_version'])
            run=new_run(stage,payload,book['is_mock']);store.put('run',run.run_id,run,db)
            p['stages'][stage]=run.run_id;p.update(status='queued',current_node=stage,current_run_id=run.run_id)
            p['trace'].append(dict(node=stage,status='queued',run_id=run.run_id,at=now()));break
        else:
            narration=store.get('run',p['stages']['narration_render'],db);clip=store.get('clip',narration['outputs'][0],db)
            audit=store.get('run',p['stages']['frame_validation'],db)
            p.update(status='succeeded' if clip['status']=='approved' and audit['status']=='succeeded' else 'needs_review',clip_id=clip['clip_id'],current_node='Publish' if clip['status']=='approved' else 'ActualFrameReview')
        p['updated_at']=now();store.put('pipeline',identifier,p,db);return p
