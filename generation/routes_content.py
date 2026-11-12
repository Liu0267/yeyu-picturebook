"""Reviewable preprocessing and source-grounded content edits for A/B integration."""
import uuid
from .models import BookManifest,BookContentReview,ObservationReview,PreprocessCreate,NarrationCreate,PipelineCreate
from .quality import inspect_page
from .workflow import invalidate,new_run,now


def register_content_routes(app,store,require,envelope,APIError):
    from fastapi import Request
    def find_page(page_id,db=None):
        for b in store.all('book',db):
            for p in b['pages']:
                if p['page_id']==page_id:return b,p
        raise APIError('NOT_FOUND','书页不存在。',404)

    @app.post('/v1/pipelines',status_code=202)
    def create_pipeline_route(body:PipelineCreate,request:Request):
        from .pipeline import create_pipeline
        require('shot',body.shot_id)
        try:record=create_pipeline(store,body.shot_id,body.idempotency_key,body.resume_run_ids)
        except ValueError as exc:raise APIError(str(exc),'流水线输入、审核状态或恢复引用不匹配。',409)
        return envelope(request,record,True)

    @app.get('/v1/pipelines/{pipeline_id}')
    def pipeline_state(pipeline_id:str,request:Request):
        return envelope(request,require('pipeline',pipeline_id))

    @app.post('/v1/pipelines/{pipeline_id}/advance')
    def advance_pipeline_route(pipeline_id:str,request:Request):
        from .pipeline import advance_pipeline
        require('pipeline',pipeline_id)
        try:record=advance_pipeline(store,pipeline_id)
        except ValueError as exc:raise APIError(str(exc),'流水线来源已改变，请核查节点。',409)
        return envelope(request,record)

    @app.get('/v1/pages/{page_id}/ocr-suggestions')
    def ocr_suggestions(page_id:str,request:Request):
        find_page(page_id)
        return envelope(request,[r for r in store.all('ocr_suggestion') if r['page_id']==page_id],warnings=['建议不等于确认文字；采纳须通过版本化页面校对接口。'])

    @app.post('/v1/clips/{clip_id}/validate',status_code=202)
    def validate_clip(clip_id:str,body:NarrationCreate,request:Request):
        clip=require('clip',clip_id)
        if clip['asset_version']!=body.expected_asset_version or clip['status'] in ('expired','failed'):raise APIError('VERSION_CONFLICT','片段已变化。',409)
        run=new_run('frame_validation',dict(clip_id=clip_id,asset_version=clip['asset_version']),clip['is_mock'])
        store.put('run',run.run_id,run)
        return envelope(request,dict(run_id=run.run_id),True)

    @app.get('/v1/clips/{clip_id}/audits')
    def audits(clip_id:str,request:Request):
        require('clip',clip_id)
        return envelope(request,[a for a in store.all('clip_audit') if a['clip_id']==clip_id])

    @app.get('/v1/books/{book_id}/understanding-candidates')
    def candidates(book_id:str,request:Request):
        book=require('book',book_id);pages={p['page_id']:p for p in book['pages']};result=[]
        for o in store.all('understanding'):
            if o['book_id']!=book_id or o['status'] in ('expired','rejected') or o['page_revision']!=pages[o['page_id']]['page_revision']:continue
            parsed=o.get('reviewed_parsed') or (o.get('parsed') if o['schema_status']=='passed' else None)
            if not parsed:continue
            objects=[]
            for obj in parsed['visible_objects']:
                links=[e['entity_id'] for e in book['entities'] if e['name']==obj['name'] or (len(obj['name'])>=2 and e['name'].endswith(obj['name']))]
                objects.append(dict(**obj,suggested_entity_ids=links))
            result.append(dict(understanding_id=o['understanding_id'],page_id=o['page_id'],page_revision=o['page_revision'],
                               source_image_asset_id=pages[o['page_id']]['image_asset_id'],entity_candidates=objects,
                               event_candidate=parsed['visual_summary'],status='needs_review'))
        return envelope(request,result,warnings=['角色匹配和事件摘要为待审核候选，不进入已确认事实；全书实体ID由版本化内容审核维护。'])

    @app.get('/v1/pages/{page_id}/reading-order')
    def reading_order(page_id:str,request:Request):
        from .layout import suggest_reading_order
        book,page=find_page(page_id)
        return envelope(request,dict(page_id=page_id,page_revision=page['page_revision'],**suggest_reading_order(page['ocr_lines'])))

    @app.get('/v1/pages/{page_id}/quality')
    def quality(page_id:str,request:Request):
        book,page=find_page(page_id)
        report=inspect_page(store.asset_path(page['image_asset_id']))
        report.update(page_id=page_id,page_revision=page['page_revision'])
        store.put('page_quality',page_id,report)
        return envelope(request,report,warnings=['质量启发式仅用于提示复核，白纸和画风可能误报；不代表OCR准确率。'])

    @app.post('/v1/pages/{page_id}/preprocess',status_code=202)
    def preprocess(page_id:str,body:PreprocessCreate,request:Request):
        book,page=find_page(page_id)
        if page['page_revision']!=body.expected_page_revision:raise APIError('VERSION_CONFLICT','书页版本已变化。',409)
        run=new_run('page_preprocess',dict(book_id=book['book_id'],page_id=page_id,page_revision=page['page_revision'],
                    source_asset_id=page['image_asset_id'],**body.model_dump(exclude={'expected_page_revision'})),book['is_mock'])
        store.put('run',run.run_id,run)
        return envelope(request,dict(run_id=run.run_id),True,warnings=['生成独立副本和坐标变换，原始书页及已发布事实保持不变。'])

    @app.get('/v1/pages/{page_id}/preprocessings')
    def preprocessings(page_id:str,request:Request):
        find_page(page_id)
        return envelope(request,[r for r in store.all('preprocessing') if r['page_id']==page_id])

    @app.patch('/v1/understandings/{understanding_id}/review')
    def review_observation(understanding_id:str,body:ObservationReview,request:Request):
        with store.connect() as db:
            record=require('understanding',understanding_id,db);book,page=find_page(record['page_id'],db)
            if body.expected_page_revision!=page['page_revision'] or record['page_revision']!=page['page_revision'] or record['status']=='expired':
                raise APIError('VERSION_CONFLICT','观察对应的书页已变化。',409)
            record.update(status='reviewed' if body.decision=='corrected' else 'rejected',reviewed_by=body.reviewer,
                          reviewed_parsed=body.corrected_observation.model_dump() if body.corrected_observation else None,review_notes=body.notes)
            store.put('understanding',understanding_id,record,db)
            store.put('review',uuid.uuid4().hex,dict(understanding_id=understanding_id,at=now(),**body.model_dump()),db)
        return envelope(request,record,warnings=['保留原始模型输出；修订观察不会自动写入原书事实。'])

    @app.patch('/v1/books/{book_id}/content')
    def review_content(book_id:str,body:BookContentReview,request:Request):
        with store.connect() as db:
            old=require('book',book_id,db)
            if old['revision']!=body.expected_revision:raise APIError('VERSION_CONFLICT','书籍内容版本已变化。',409)
            new=dict(old,**body.model_dump(include={'source_refs','facts','entities','events'}))
            pages={p['page_id'] for p in old['pages']}
            if not set(body.reviewed_page_ids)<=pages:raise APIError('BAD_REFERENCE','审核页必须属于本书。',422)
            previous={f['fact_id']:f for f in old['facts']}
            for f in new['facts']:
                if f!=previous.get(f['fact_id']) and f['status']=='reviewed' and f['reviewed_by']!=body.reviewer:
                    raise APIError('REVIEWER_MISMATCH','新增或修改的已确认事实必须由本次审核人署名。',422)
            try:BookManifest.model_validate(new)
            except ValueError:raise APIError('BAD_REFERENCE','事实、角色、事件及来源引用不一致。',422)
            affected=set(body.reviewed_page_ids)
            refs={r['ref_id']:r for b in (old,new) for r in b['source_refs']}
            facts={f['fact_id']:f for b in (old,new) for f in b['facts']}
            for group,key in [('source_refs','ref_id'),('facts','fact_id'),('entities','entity_id'),('events','event_id')]:
                before={v[key]:v for v in old[group]};after={v[key]:v for v in new[group]}
                for identifier in before.keys()|after.keys():
                    if before.get(identifier)==after.get(identifier):continue
                    for value in [before.get(identifier),after.get(identifier)]:
                        if not value:continue
                        if 'page_id' in value:affected.add(value['page_id'])
                        affected.update(refs[r]['page_id'] for r in value.get('source_ref_ids',[]) if r in refs)
                        affected.update(facts[f]['page_id'] for f in value.get('fact_ids',[]) if f in facts)
            if not affected:return envelope(request,dict(book=old,invalidated_clip_ids=[]))
            store.put('history',book_id+'_r'+str(old['revision']),old,db)
            new['revision']+=1
            # Copy page dicts to keep the before snapshot intact.
            new['pages']=[dict(p) for p in old['pages']]
            for page in new['pages']:
                if page['page_id'] in affected:
                    page['page_revision']+=1
                    page['review_status']='reviewed' if page['page_id'] in body.reviewed_page_ids else 'needs_review'
            store.put('book',book_id,BookManifest.model_validate(new),db)
            clips=[c for c in store.all('clip',db) if c['page_id'] in affected and c['status']!='expired'];invalidate(store,clips,db)
            for kind in ['shot','understanding','narration','preprocessing']:
                for item in store.all(kind,db):
                    if item['page_id'] in affected:
                        item['status']='needs_review' if kind=='shot' else 'expired'
                        key={'shot':'shot_id','understanding':'understanding_id','narration':'narration_id','preprocessing':'preprocessing_id'}[kind]
                        store.put(kind,item[key],item,db)
            store.put('review',uuid.uuid4().hex,dict(book_id=book_id,at=now(),reviewer=body.reviewer,affected_page_ids=sorted(affected),new_revision=new['revision']),db)
        return envelope(request,dict(book=new,invalidated_clip_ids=[c['clip_id'] for c in clips]),warnings=['受影响页的旧视频和证据已过期；新内容须按页审核后制作分镜。'])
