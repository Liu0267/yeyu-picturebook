import json
import hashlib
import os
import time
import uuid
import copy
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .ingest import ingest
from .media import extract_frames, make_mock, probe,render_narration
from .models import BookManifest, ClipAsset, ErrorInfo, RunRecord, ShotSpec,PageObservation,NarrationAsset
from .local_models import understand_page,synthesize
from .providers import ProviderError, ProviderNotConfigured, UnconfiguredVideoProvider, configured_video_provider
from .store import Store, digest


def now():
    return datetime.now(timezone.utc).isoformat()


def new_run(step, payload, is_mock=False):
    return RunRecord(run_id='run_' + uuid.uuid4().hex, step=step, input_hash=digest(payload),
                     status='queued', created_at=now(), updated_at=now(), payload=payload, is_mock=is_mock)


def invalidate(store, clips, db):
    for clip in clips:
        clip['status'] = 'expired'
        store.put('clip', clip['clip_id'], clip, db)
        for frame in store.all('frame', db):
            if frame['clip_id'] == clip['clip_id']:
                frame['status'] = 'expired'
                store.put('frame', frame['frame_id'], frame, db)

    invalidate_compilations(store,{c['clip_id'] for c in clips},db)


def invalidate_compilations(store,invalidated,db):
    for compilation in store.all('compilation',db):
        if any(ch['clip_id'] in invalidated for ch in compilation['chapters']):
            compilation['status']='expired'
            store.put('compilation',compilation['compilation_id'],compilation,db)
            for frame in store.all('compilation_frame',db):
                if frame['compilation_id']==compilation['compilation_id']:
                    frame['status']='expired';store.put('compilation_frame',frame['frame_id'],frame,db)


def grounded_shots(book):
    """Deterministic source-bound draft; does not impersonate VLM/LLM generation."""
    shots = []
    for page in book.pages:
        facts = [f for f in book.facts if f.page_id == page.page_id and f.status == 'reviewed']
        if not facts:
            continue
        narration = '。'.join(f.claim.rstrip('。') for f in facts) + '。'
        ref_ids = {r for f in facts for r in f.source_ref_ids}
        entities = [e.entity_id for e in book.entities if set(e.source_ref_ids) & ref_ids]
        shots.append(ShotSpec(shot_id=f'{page.page_id}_r{page.page_revision}_shot1', book_id=book.book_id,
                              manifest_revision=book.revision, page_id=page.page_id,
                              page_revision=page.page_revision, fact_ids=[f.fact_id for f in facts], entity_ids=entities,
                              reference_asset_id=page.image_asset_id, reference_region=dict(x1=0,y1=0,x2=1,y2=1),
                              narration=narration, target_duration_ms=6000,
                              creative_additions=['轻微镜头运动为演绎，实际动作须复核'],
                              prompt='依据当前原页作参考，保留原插图人物、色彩和笔触。仅表现：' + narration,
                              negative_prompt='不得新增人物、改动数量、替换关键物品或颠倒因果；不重绘文字，不合成新对话。'))
    return shots


def execute_run(store, run, provider=None):
    started = time.monotonic()
    run.status = 'running'
    run.attempts += 1
    run.updated_at = now()
    run.trace.append(dict(node=run.step, status='running', at=now()))
    store.put('run', run.run_id, run)
    try:
        if run.step == 'ingest':
            source = store.root / 'inbox' / run.payload['source_name']
            if hashlib.sha256(source.read_bytes()).hexdigest() != run.payload['source_sha256']:
                raise ValueError('STALE_INPUT')
            book = ingest(store, source, run.payload['book_id'], run.payload['title'], os.getenv('YU_VISION_EXECUTABLE'))
            run.outputs = [book.book_id]
            run.status = 'needs_review'
        elif run.step=='frame_validation':
            from .frame_audit import audit_clip
            clip=store.get('clip',run.payload['clip_id'])
            if clip['asset_version']!=run.payload['asset_version'] or clip['status'] in ('expired','failed'):raise ValueError('STALE_INPUT')
            output=store.root/'runs'/run.run_id;output.mkdir(parents=True,exist_ok=True)
            audit=audit_clip(store,clip,output)
            current=store.get('clip',clip['clip_id'])
            if current['status'] in ('expired','failed'):raise ValueError('STALE_INPUT')
            audit.update(audit_id='audit_'+run.run_id,run_id=run.run_id)
            store.put('clip_audit',audit['audit_id'],audit)
            current['status']='needs_review';current['reviewed_by']=None
            with store.connect() as db:
                store.put('clip',current['clip_id'],current,db)
                invalidate_compilations(store,{current['clip_id']},db)
            run.outputs=[audit['audit_id']];run.usage=audit['usage'];run.model_version=audit['model_id'];run.status='needs_review'
        elif run.step=='compilation':
            from .compilation import render_compilation
            record,frames=render_compilation(store,run)
            run.outputs=[record.compilation_id,*(f['frame_id'] for f in frames)]
            run.status='needs_review';run.model_version='ffmpeg-continuous-reading-v1'
            run.usage=dict(external_calls=0,source_clips=len(record.chapters),actual_duration_ms=record.duration_ms)
        elif run.step=='page_preprocess':
            from .quality import rectify,inspect_page
            book=store.get('book',run.payload['book_id'])
            page=next(p for p in book['pages'] if p['page_id']==run.payload['page_id'])
            if page['page_revision']!=run.payload['page_revision']:raise ValueError('STALE_INPUT')
            output=store.root/'runs'/run.run_id;output.mkdir(parents=True,exist_ok=True)
            target=output/'rectified.png'
            geometry=rectify(store.asset_path(run.payload['source_asset_id']),target,run.payload['rotation_clockwise'],run.payload.get('corners_after_rotation'))
            record=dict(preprocessing_id='preprocessing_'+run.run_id,page_id=page['page_id'],page_revision=page['page_revision'],
                        source_asset_id=page['image_asset_id'],image_asset_id=store.asset(target,'image/png',book['is_mock']),geometry=geometry,
                        quality=inspect_page(target),status='needs_review',run_id=run.run_id)
            executable=os.getenv('YU_VISION_EXECUTABLE')
            if executable:
                import subprocess
                result=subprocess.run([executable,str(target)],capture_output=True,text=True,check=True,timeout=120)
                (output/'ocr_raw.jsonl').write_text(result.stdout)
                record['derived_ocr']={k:v for k,v in json.loads(result.stdout.strip()).items() if k!='path'}
                record['ocr_coordinate_space']='rectified_image; use inverse geometry for original pixel mapping'
                region=run.payload.get('chinese_text_region')
                if region:
                    from .text_rows import chinese_rows
                    rows=chinese_rows(target,[region[k] for k in ('x1','y1','x2','y2')],output/'chinese_rows')
                    record['chinese_rows']=[dict(image_asset_id=store.asset(row['image'],'image/png',book['is_mock']),source_region=row['source_region']) for row in rows]
                    if rows:
                        result=subprocess.run([executable,*(r['image'] for r in rows)],capture_output=True,text=True,check=True,timeout=120)
                        record['row_ocr']=[{k:v for k,v in json.loads(line).items() if k!='path'} for line in result.stdout.splitlines()]
                    record['row_ocr_coordinate_space']='Each OCR bbox is normalized within its row crop; source_region maps that crop to rectified_image. Inverse geometry maps pixels to the original.'
            current=store.get('book',book['book_id'])
            if next(p for p in current['pages'] if p['page_id']==page['page_id'])['page_revision']!=page['page_revision']:record['status']='expired'
            store.put('preprocessing',record['preprocessing_id'],record)
            run.outputs=[record['preprocessing_id']];run.status='expired' if record['status']=='expired' else 'needs_review'
            run.usage=dict(external_calls=0)
        elif run.step=='page_understanding':
            book=store.get('book',run.payload['book_id'])
            page=next(p for p in book['pages'] if p['page_id']==run.payload['page_id'])
            if page['page_revision']!=run.payload['page_revision']:
                raise ValueError('STALE_INPUT')
            output=store.root/'runs'/run.run_id
            image=store.asset_path(page['image_asset_id'],verify=True)
            region=run.payload.get('source_region')
            if region:
                from PIL import Image
                output.mkdir(parents=True,exist_ok=True)
                with Image.open(image) as source:
                    crop=source.crop((round(region['x1']*source.width),round(region['y1']*source.height),round(region['x2']*source.width),round(region['y2']*source.height)))
                    image=output/'source_crop.jpg';crop.convert('RGB').save(image,quality=94)
            result=understand_page(image,output)
            schema_status='passed'
            try:PageObservation.model_validate(result.get('parsed'))
            except ValueError:schema_status='needs_correction'
            parsed=copy.deepcopy(result.get('parsed'))
            if region and schema_status=='passed':
                for obj in parsed['visible_objects']:
                    x1,y1,x2,y2=obj['bbox']
                    obj['bbox']=[region['x1']+(region['x2']-region['x1'])*x1,region['y1']+(region['y2']-region['y1'])*y1,
                                 region['x1']+(region['x2']-region['x1'])*x2,region['y1']+(region['y2']-region['y1'])*y2]
            identifier='understanding_'+run.run_id
            observation=dict(understanding_id=identifier,book_id=book['book_id'],page_id=page['page_id'],
                             page_revision=page['page_revision'],model_id=result['model_id'],raw_text=result['raw_text'],
                             parsed=parsed,schema_status=schema_status,status='needs_review',is_mock=book['is_mock'],
                             coordinate_space='original_page_normalized',source_ref_id=run.payload.get('source_ref_id'),source_region=region)
            current=store.get('book',book['book_id'])
            if next(p for p in current['pages'] if p['page_id']==page['page_id'])['page_revision']!=page['page_revision']:
                observation['status']='expired'
            store.put('understanding',identifier,observation)
            run.model_version=result['model_id'];run.outputs=[identifier]
            run.usage=dict(local_inference_ms=result['elapsed_ms'],peak_memory_bytes=result['peak_memory_bytes'],external_calls=0)
            run.status='expired' if observation['status']=='expired' else 'needs_review'
        elif run.step=='narration':
            shot=ShotSpec.model_validate(run.payload['shot'])
            book=store.get('book',shot.book_id)
            if next(p for p in book['pages'] if p['page_id']==shot.page_id)['page_revision']!=shot.page_revision:
                raise ValueError('STALE_INPUT')
            output=store.root/'runs'/run.run_id
            result=synthesize(shot.narration,output)
            narration=NarrationAsset(narration_id='narration_'+run.run_id,book_id=shot.book_id,page_id=shot.page_id,
                                     page_revision=shot.page_revision,shot_id=shot.shot_id,run_id=run.run_id,model_id=result['model_id'],
                                     audio_asset_id=store.asset(output/result['audio_file'],'audio/wav',book['is_mock']),
                                     text=shot.narration,speaker=result['speaker'],duration_ms=result['duration_ms'],sample_rate=result['sample_rate'],is_mock=book['is_mock'])
            current=store.get('book',shot.book_id)
            if next(p for p in current['pages'] if p['page_id']==shot.page_id)['page_revision']!=shot.page_revision:
                narration.status='expired'
            store.put('narration',narration.narration_id,narration)
            run.model_version=result['model_id'];run.outputs=[narration.narration_id]
            run.status='expired' if narration.status=='expired' else 'needs_review'
            run.usage=dict(local_inference_ms=result['elapsed_ms'],peak_memory_bytes=result['peak_memory_bytes'],external_calls=0)
        elif run.step=='narration_render':
            old=store.get('clip',run.payload['clip_id'])
            if old['asset_version']!=run.payload['asset_version'] or old['status'] in ('expired','failed'):
                raise ValueError('STALE_INPUT')
            shot=ShotSpec.model_validate(store.get('shot',old['shot_id']))
            book=store.get('book',old['book_id'])
            if next(p for p in book['pages'] if p['page_id']==old['page_id'])['page_revision']!=old['page_revision']:
                raise ValueError('STALE_INPUT')
            output=store.root/'runs'/run.run_id;output.mkdir(parents=True,exist_ok=True)
            result=synthesize(shot.narration,output)
            audio=output/result['audio_file']
            source_id=old.get('source_video_asset_id') or old['asset_id']
            video,vtt,timing=render_narration(store.asset_path(source_id),audio,shot.narration,output)
            duration,_=probe(video)
            new=dict(old,clip_id='clip_'+uuid.uuid4().hex,asset_version=old['asset_version']+1,status='needs_review',
                     asset_id=store.asset(video,'video/mp4',old['is_mock']),audio_asset_id=store.asset(audio,'audio/wav',old['is_mock']),
                     subtitle_asset_id=store.asset(vtt,'text/vtt',old['is_mock']),source_video_asset_id=source_id,
                     duration_ms=duration,run_id=run.run_id,reviewed_by=None)
            clip=ClipAsset.model_validate(new)
            with store.connect() as db:
                current=store.get('clip',old['clip_id'],db)
                current_book=store.get('book',old['book_id'],db)
                if current['status'] in ('expired','failed') or next(p for p in current_book['pages'] if p['page_id']==old['page_id'])['page_revision']!=old['page_revision']:
                    raise ValueError('STALE_INPUT')
                invalidate(store,[current],db)
                store.put('clip',clip.clip_id,clip,db)
            run.outputs=[clip.clip_id]
            frames=extract_frames(store,clip)
            run.outputs.extend(f.frame_id for f in frames)
            run.model_version=result['model_id'];run.status='needs_review'
            run.usage=dict(local_inference_ms=result['elapsed_ms'],peak_memory_bytes=result['peak_memory_bytes'],external_calls=0,**timing)
        elif run.step == 'video_generation':
            shot = ShotSpec.model_validate(run.payload['shot'])
            book = BookManifest.model_validate(store.get('book', shot.book_id))
            page = next(p for p in book.pages if p.page_id == shot.page_id)
            if page.page_revision != shot.page_revision:
                run.status = 'expired'
                run.error = ErrorInfo(code='STALE_INPUT', message='书页版本已改变，重新审核分镜后提交。')
            else:
                output = store.root / 'runs' / run.run_id
                output.mkdir(parents=True, exist_ok=True)
                call_id = None
                if run.is_mock:
                    video = make_mock(output)
                    run.model_version = 'synthetic-fixture-v1'
                    run.usage = dict(external_calls=0, cost=0, currency='CNY')
                else:
                    provider = provider or configured_video_provider()
                    if isinstance(provider, UnconfiguredVideoProvider):
                        raise ProviderNotConfigured('尚未配置视频平台和模型；外部调用次数为0。')
                    call_shot=shot.model_dump()
                    if not book.external_upload_allowed and getattr(provider,'requires_external_upload',True):
                        consent=store.get('upload_consent',run.run_id)
                        if not consent or consent.get('input_hash')!=run.input_hash:
                            raise ProviderError('EXTERNAL_UPLOAD_NOT_ENABLED','当前任务的参考图片外传尚未获准，未调用视频模型。')
                        call_shot['_consent_reference_sha256']=consent['reference_sha256']
                        call_shot['_consent_max_submissions']=consent.get('max_submissions',1)
                    if shot.end_keyframe_asset_id:
                        if getattr(provider,'requires_external_upload',True):raise ProviderError('END_KEYFRAME_LOCAL_ONLY','新增结束关键帧仅启用本机供应商，未外传。')
                        call_shot['_end_reference_path']=str(store.asset_path(shot.end_keyframe_asset_id,verify=True))
                    result = provider.generate(call_shot, store.asset_path(shot.reference_asset_id,verify=True), output, run.run_id)
                    video, call_id = result.path, result.call_id
                    run.model_version, run.usage = result.model_version, result.usage
                duration, _ = probe(video)
                asset_id = store.asset(video, 'video/mp4', run.is_mock)
                with store.connect() as db:
                    current = store.get('book', shot.book_id, db)
                    current_page = next(p for p in current['pages'] if p['page_id'] == shot.page_id)
                    if current_page['page_revision'] != shot.page_revision:
                        raise ValueError('STALE_INPUT')
                    old = [c for c in store.all('clip', db) if c['shot_id'] == shot.shot_id]
                    version = max((c['asset_version'] for c in old), default=0) + 1
                    invalidate(store, old, db)
                    clip = ClipAsset(clip_id='clip_'+uuid.uuid4().hex, book_id=shot.book_id, page_id=shot.page_id,
                                      shot_id=shot.shot_id, manifest_revision=shot.manifest_revision,
                                      page_revision=shot.page_revision, asset_version=version, duration_ms=duration,
                                      status='needs_review', asset_id=asset_id, is_mock=run.is_mock,
                                      provenance='synthetic_mock' if run.is_mock else 'model_generated',
                                      run_id=run.run_id, model_call_id=call_id)
                    store.put('clip', clip.clip_id, clip, db)
                run.outputs = [clip.clip_id]
                frames = extract_frames(store, clip)
                run.outputs = [clip.clip_id, *(f.frame_id for f in frames)]
                run.status = 'needs_review'
        else:
            raise ValueError('unsupported workflow step')
    except ProviderError as exc:
        run.status = 'failed'
        run.error = ErrorInfo(code=exc.code,message=str(exc),retryable=exc.retryable)
        receipt_path=store.root/'runs'/run.run_id/'provider_state.json'
        if run.step=='video_generation' and receipt_path.is_file():
            receipt=json.loads(receipt_path.read_text())
            run.model_version=receipt.get('model')
            run.usage=dict(external_calls=0 if receipt.get('provider')=='local_wan' else receipt.get('submit_attempts',1),accepted_tasks=int(bool(receipt.get('video_id'))),
                           cost=None,provider_error_code=receipt.get('last_error',{}).get('code'))
    except ProviderNotConfigured as exc:
        run.status = 'failed'
        run.error = ErrorInfo(code='PROVIDER_NOT_CONFIGURED', message=str(exc))
        run.usage = dict(external_calls=0, cost=None)
    except Exception as exc:
        run.status = 'failed'
        code = 'STALE_INPUT' if str(exc) == 'STALE_INPUT' else 'ASSET_INTEGRITY_FAILED' if str(exc)=='ASSET_HASH_MISMATCH' else 'STEP_FAILED'
        # Do not persist absolute paths, remote URLs or potential credentials in public error text.
        run.error = ErrorInfo(code=code, message=f'{run.step} 失败（{type(exc).__name__}），请查看本地输入和配置。', retryable=False)
        if run.outputs:
            for cid in run.outputs:
                clip = store.get('clip', cid)
                if clip:
                    clip['status'] = 'failed'
                    store.put('clip', cid, clip)
    run.updated_at = now()
    run.trace.append(dict(node=run.step, status=run.status, at=now(), elapsed_ms=round((time.monotonic()-started)*1000)))
    store.put('run', run.run_id, run)
    return run


def recover_interrupted(store):
    """Never silently resubmit possibly billed external requests."""
    for record in store.all('run'):
        run = RunRecord.model_validate(record)
        if run.status == 'running':
            receipt_path=store.root/'runs'/run.run_id/'provider_state.json'
            if run.step=='video_generation' and receipt_path.is_file():
                receipt=json.loads(receipt_path.read_text());video=receipt_path.parent/'generated.mp4'
                if receipt.get('provider')=='local_wan' and receipt.get('status')=='completed' and video.is_file() and hashlib.sha256(video.read_bytes()).hexdigest()==receipt.get('output_sha256') and run.attempts<run.max_attempts:
                    try:probe(video)
                    except (ValueError,OSError,subprocess.SubprocessError):pass
                    else:
                        run.status='queued';run.error=None;run.updated_at=now()
                        run.trace.append(dict(node='RecoverCompletedLocalOutput',status='queued',at=now(),new_model_submission=False))
                        store.put('run',run.run_id,run);continue
            run.status = 'failed'
            local=run.step in ('ingest','page_preprocess','page_understanding','narration','narration_render','compilation','frame_validation')
            run.error = ErrorInfo(code='INTERRUPTED_REVIEW_REQUIRED', message='本地任务中断，可按次数上限重试。' if local else '工作进程中断；先核查供应商任务再重试。', retryable=run.is_mock or local)
            run.updated_at = now()
            store.put('run', run.run_id, run)


def work_once(store, provider=None):
    with store.connect() as db:
        for record in store.all('run', db):
            if record['status'] == 'queued':
                run = RunRecord.model_validate(record)
                # Reserve inside transaction. Only one worker is supported; CLI also takes a process lock.
                record['status'] = 'running'
                store.put('run', run.run_id, record, db)
                break
        else:
            return None
    return execute_run(store, run, provider)


def main():
    import argparse
    import fcntl
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    store = Store(os.getenv('YU_DATA_DIR', Path(__file__).resolve().parents[1]/'data'))
    lock = open(store.root/'worker.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Only one local worker may run.')
    recover_interrupted(store)
    while True:
        result = work_once(store)
        if result:
            print(json.dumps(dict(run_id=result.run_id, status=result.status), ensure_ascii=False), flush=True)
        if args.once:
            break
        if result is None:
            time.sleep(0.5)


if __name__ == '__main__':
    main()

