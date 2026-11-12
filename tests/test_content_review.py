import copy
from generation.workflow import work_once
from test_pipeline import seed,system,approve


def test_preprocess_is_separate_and_geometry_is_validated(system):
    client,store=system;before=copy.deepcopy(store.get('book','book_mock'));pid='book_mock_p001'
    r=client.post('/v1/pages/'+pid+'/preprocess',json=dict(expected_page_revision=1,corners_after_rotation=[[0,0],[1,1],[1,0],[0,1]]))
    assert r.status_code==422 and store.all('preprocessing')==[]
    r=client.post('/v1/pages/'+pid+'/preprocess',json=dict(expected_page_revision=1,rotation_clockwise=90));assert r.status_code==202
    run=work_once(store);assert run.status=='needs_review'
    record=store.get('preprocessing',run.outputs[0]);assert record['geometry']['original_unchanged']
    assert record['image_asset_id']!=before['pages'][0]['image_asset_id']
    assert store.get('book','book_mock')==before


def test_content_edit_checks_references_and_expires_dependent_media(system):
    client,store=system;book=store.get('book','book_mock');clip=store.all('clip')[0];approve(client,store,clip)
    body={k:copy.deepcopy(book[k]) for k in ['source_refs','facts','entities','events']}
    body.update(expected_revision=1,reviewer='editor',reviewed_page_ids=['book_mock_p001'])
    body['facts'][0].update(claim='校对后可见一本合成书。',reviewed_by='editor')
    broken=copy.deepcopy(body);broken['facts'][0]['source_ref_ids']=['missing']
    assert client.patch('/v1/books/book_mock/content',json=broken).status_code==422
    assert store.get('clip',clip['clip_id'])['status']=='approved'
    response=client.patch('/v1/books/book_mock/content',json=body);assert response.status_code==200
    assert store.get('clip',clip['clip_id'])['status']=='expired'
    assert all(f['status']=='expired' for f in store.all('frame'))
    assert store.get('book','book_mock')['revision']==2
    assert store.get('history','book_mock_r1')['facts'][0]['claim']!=body['facts'][0]['claim']
    assert client.patch('/v1/books/book_mock/content',json=body).status_code==409


def test_reviewed_model_correction_preserves_raw_and_never_mutates_facts(system):
    client,store=system;facts=copy.deepcopy(store.get('book','book_mock')['facts'])
    record=dict(understanding_id='observation_test',page_id='book_mock_p001',page_revision=1,status='needs_review',raw_text='wrong model text',parsed={'visible_objects':[]})
    store.put('understanding','observation_test',record)
    body=dict(expected_page_revision=1,reviewer='assistant_test',decision='corrected',notes='对照实际合成页修订',corrected_observation=dict(visible_objects=[],explicit_text='',visual_summary='合成书',uncertainties=['测试样本']))
    assert client.patch('/v1/understandings/observation_test/review',json=body).status_code==200
    result=store.get('understanding','observation_test');assert result['raw_text']=='wrong model text'
    assert result['reviewed_parsed']['visual_summary']=='合成书' and result['status']=='reviewed'
    assert store.get('book','book_mock')['facts']==facts


def test_new_reviewed_fact_requires_current_reviewer(system):
    client,store=system;b=store.get('book','book_mock');body={k:copy.deepcopy(b[k]) for k in ['source_refs','facts','entities','events']}
    body.update(expected_revision=1,reviewer='new_editor');body['facts'][0]['claim']='changed'
    assert client.patch('/v1/books/book_mock/content',json=body).json()['error']['code']=='REVIEWER_MISMATCH'


def test_preprocessing_ocr_response_uses_assets_not_server_paths(system,monkeypatch):
    import json
    from types import SimpleNamespace
    client,store=system
    monkeypatch.setenv('YU_VISION_EXECUTABLE','synthetic-vision-executable')
    def fake(args,**kwargs):
        return SimpleNamespace(stdout='\n'.join(json.dumps(dict(path='/private/test/internal/source.png',engine='synthetic-test-ocr',lines=[],error=None)) for _ in args[1:]))
    monkeypatch.setattr('generation.workflow.subprocess.run',fake)
    r=client.post('/v1/pages/book_mock_p001/preprocess',json=dict(expected_page_revision=1,chinese_text_region=dict(x1=0,y1=0,x2=1,y2=1)));assert r.status_code==202
    run=work_once(store);assert run.status=='needs_review',run.error
    record=store.get('preprocessing',run.outputs[0]);assert record['image_asset_id'].startswith('asset_')
    response=client.get('/v1/pages/book_mock_p001/preprocessings')
    assert '/private/test/' not in response.text and str(store.root) not in response.text
    assert all(row['image_asset_id'].startswith('asset_') for row in record.get('chinese_rows',[]))


def test_entity_event_candidates_preserve_draft_boundary(system):
    client,store=system;book=store.get('book','book_mock');before=copy.deepcopy(book['facts'])
    o=dict(understanding_id='candidate_test',book_id='book_mock',page_id='book_mock_p001',page_revision=1,status='needs_review',schema_status='passed',parsed=dict(visible_objects=[dict(name=book['entities'][0]['name'],description='测试可见物',bbox=[0,0,1,1])],explicit_text='',visual_summary='候选事件',uncertainties=[]))
    store.put('understanding',o['understanding_id'],o)
    r=client.get('/v1/books/book_mock/understanding-candidates');assert r.status_code==200
    item=r.json()['data'][0];assert item['status']=='needs_review' and item['event_candidate']=='候选事件'
    assert item['entity_candidates'][0]['suggested_entity_ids']==[book['entities'][0]['entity_id']]
    assert store.get('book','book_mock')['facts']==before
