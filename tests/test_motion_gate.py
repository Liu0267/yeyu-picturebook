from test_pipeline import seed,system


def test_real_motion_verdict_is_required_and_failure_stays_unpublished(system):
    client,store=system
    # Entire media fixture is synthetic. Flags are changed solely to exercise the real-mode review boundary.
    clip=store.all('clip')[0];clip.update(is_mock=False,provenance='model_generated',model_call_id='synthetic_test_provider_call');store.put('clip',clip['clip_id'],clip)
    frames=store.all('frame');reviewer='synthetic_motion_boundary_test'
    body=dict(expected_asset_version=1,reviewer=reviewer,claims_by_frame={f['frame_id']:[dict(claim='合成测试书图案',region=dict(x1=0,y1=0,x2=1,y2=1),observed_by=reviewer,method='vlm_frame_review')] for f in frames})
    assert client.patch('/v1/clips/'+clip['clip_id']+'/review',json=body).json()['error']['code']=='MOTION_REVIEW_REQUIRED'
    body.update(motion_review='failed',motion_notes='测试媒体静止，不能视为角色动作通过。')
    assert client.patch('/v1/clips/'+clip['clip_id']+'/review',json=body).status_code==200
    result=store.get('clip',clip['clip_id']);assert result['motion_status']=='failed' and result['status']=='needs_review'
    assert client.get('/v1/books/book_mock/clips').json()['data']['clips']==[]
