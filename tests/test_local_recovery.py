import hashlib,json
from pathlib import Path
from generation.workflow import recover_interrupted,new_run
from test_pipeline import seed,system


def test_completed_local_artifact_can_resume_without_new_submission(system):
    client,store=system;run=new_run('video_generation',dict(shot=store.all('shot')[0]));run.status='running';run.attempts=1;store.put('run',run.run_id,run)
    out=store.root/'runs'/run.run_id;out.mkdir();source=store.asset_path(store.all('clip')[0]['asset_id']);video=out/'generated.mp4';video.write_bytes(source.read_bytes())
    receipt=dict(provider='local_wan',status='completed',output_sha256=hashlib.sha256(video.read_bytes()).hexdigest())
    (out/'provider_state.json').write_text(json.dumps(receipt));recover_interrupted(store)
    result=store.get('run',run.run_id);assert result['status']=='queued'
    assert result['trace'][-1]['new_model_submission'] is False
    assert (out/'provider_state.json').read_text()==json.dumps(receipt)


def test_corrupt_or_incomplete_video_is_not_automatically_recomputed(system):
    client,store=system;run=new_run('video_generation',{});run.status='running';run.attempts=1;store.put('run',run.run_id,run)
    out=store.root/'runs'/run.run_id;out.mkdir();(out/'generated.mp4').write_bytes(b'corrupt synthetic test data')
    (out/'provider_state.json').write_text(json.dumps(dict(provider='local_wan',status='completed',output_sha256='mismatched')))
    recover_interrupted(store);result=store.get('run',run.run_id)
    assert result['status']=='failed' and result['error']['retryable'] is False


def test_changed_asset_bytes_are_not_served_with_a_stale_etag(system):
    client,store=system;clip=store.all('clip')[0];asset=clip['asset_id'];path=store.asset_path(asset)
    path.write_bytes(b'changed synthetic media')
    response=client.get('/v1/assets/'+asset)
    assert response.status_code==404 and response.json()['error']['code']=='ASSET_UNAVAILABLE'
