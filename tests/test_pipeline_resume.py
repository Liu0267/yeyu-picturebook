import copy
from generation.pipeline import create_pipeline,advance_pipeline
from generation.workflow import work_once
from test_pipeline import seed,system


def test_pipeline_advance_is_idempotent_and_rejects_changed_source(system):
    client,store=system
    p=create_pipeline(store,'book_mock_p001_r1_shot1','pipeline_test')
    a=advance_pipeline(store,p['pipeline_id']);b=advance_pipeline(store,p['pipeline_id'])
    assert a['stages']==b['stages'] and len(a['stages'])==1
    rid=a['stages']['video_generation'];assert store.get('run',rid)['status']=='queued'
    client.patch('/v1/pages/book_mock_p001/review',json=dict(expected_revision=1,reviewer='editor'))
    assert advance_pipeline(store,p['pipeline_id'])['status']=='expired'
    assert client.post('/v1/pipelines',json=dict(shot_id='book_mock_p001_r1_shot1',idempotency_key='other',resume_run_ids={'narration_render':rid})).status_code==409


def test_failed_pipeline_never_automatically_creates_another_run(system,monkeypatch):
    client,store=system;p=create_pipeline(store,'book_mock_p001_r1_shot1','pipeline_failure');first=advance_pipeline(store,p['pipeline_id'])
    run=store.get('run',first['current_run_id']);run.update(status='failed',error=dict(code='TEST_FAILURE',message='test',retryable=False));store.put('run',run['run_id'],run)
    count=len(store.all('run'));result=advance_pipeline(store,p['pipeline_id'])
    assert result['status']=='failed' and len(store.all('run'))==count


def test_resume_rejects_unrelated_model_run(system):
    client,store=system
    response=client.post('/v1/pipelines',json=dict(shot_id='book_mock_p001_r1_shot1',idempotency_key='resume_bad',resume_run_ids={'video_generation':'does_not_exist'}))
    assert response.status_code==409 and store.get('pipeline','resume_bad') is None
