import json
from generation.workflow import work_once
from test_pipeline import seed,system,approve


def two_clips(client,store):
    old=store.all('clip')[0];approve(client,store,old)
    old=store.get('clip',old['clip_id']);second=dict(old,clip_id='clip_second_synthetic')
    store.put('clip',second['clip_id'],second)
    return old,second


def test_compilation_requires_current_approved_sources(system):
    client,store=system;a=store.all('clip')[0]
    body=dict(items=[dict(clip_id=a['clip_id'],asset_version=1),dict(clip_id='absent',asset_version=1)],idempotency_key='missing')
    assert client.post('/v1/books/book_mock/compilations',json=body).status_code==409
    body['items'][1]=body['items'][0]
    assert client.post('/v1/books/book_mock/compilations',json=body).status_code==422


def test_actual_continuous_media_timestamps_and_source_invalidation(system):
    client,store=system;a,b=two_clips(client,store)
    body=dict(items=[dict(clip_id=x['clip_id'],asset_version=1) for x in [a,b]],idempotency_key='sequence')
    response=client.post('/v1/books/book_mock/compilations',json=body);assert response.status_code==202
    assert client.post('/v1/books/book_mock/compilations',json=body).json()['data']==response.json()['data']
    result=work_once(store);assert result.status=='needs_review',result.error
    record=store.get('compilation',result.outputs[0]);assert abs(record['duration_ms']-6000)<60
    assert [c['start_ms'] for c in record['chapters']]==[0,3000]
    frames=store.all('compilation_frame');assert len(frames)==6
    assert {f['source_clip_id'] for f in frames}=={a['clip_id'],b['clip_id']}
    assert all(0<=f['source_t_ms']<3000 for f in frames)
    assert client.get('/v1/books/book_mock/compilations').json()['data']['compilations']==[]
    reviewer='synthetic_test_reviewer'
    claims={f['frame_id']:[dict(claim='测试合成书',region=dict(x1=0,y1=0,x2=1,y2=1),observed_by=reviewer,method='vlm_frame_review')] for f in frames}
    r=client.patch('/v1/compilations/'+record['compilation_id']+'/review',json=dict(expected_asset_version=1,reviewer=reviewer,claims_by_frame=claims));assert r.status_code==200
    assert len(client.get('/v1/books/book_mock/compilations').json()['data']['compilations'])==1
    assert client.get('/v1/assets/'+record['asset_id'],headers={'Range':'bytes=0-31'}).status_code==206
    client.patch('/v1/pages/book_mock_p001/review',json=dict(expected_revision=1,reviewer='editor'))
    assert store.get('compilation',record['compilation_id'])['status']=='expired'
    assert all(f['status']=='expired' for f in store.all('compilation_frame'))
