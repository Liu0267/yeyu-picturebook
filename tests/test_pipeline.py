import hashlib
import shutil
import zipfile

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from PIL import Image

from generation.app import create_app
from generation.ingest import ingest
from generation.models import BookManifest, FrameEvidence, Region, RunRecord
from generation.store import Store
from generation.workflow import execute_run, new_run, recover_interrupted, work_once
from scripts.bootstrap_mock import bootstrap


@pytest.fixture(scope='session')
def seed(tmp_path_factory):
    root = tmp_path_factory.mktemp('seed')
    bootstrap(Store(root))
    return root


@pytest.fixture
def system(seed, tmp_path):
    root = tmp_path/'data'
    shutil.copytree(seed, root)
    app = create_app(root)
    with TestClient(app) as client:
        yield client, app.state.store


def approve(client, store, clip=None):
    clip = clip or next(c for c in store.all('clip') if c['status']=='needs_review')
    frames = [f for f in store.all('frame') if f['clip_id']==clip['clip_id']]
    claims = {f['frame_id']:[dict(claim='测试合成书图案。', region=dict(x1=0,y1=0,x2=1,y2=1),
               observed_by='test_fixture_reviewer', method='human_frame_review')] for f in frames}
    return client.patch('/v1/clips/'+clip['clip_id']+'/review', json=dict(expected_asset_version=clip['asset_version'],
                        reviewer='test_fixture_reviewer', claims_by_frame=claims))


def submit(client, key='key1'):
    return client.post('/v1/clips',json=dict(shot_id='book_mock_p001_r1_shot1',mode='mock',idempotency_key=key))


def test_manifest_schema_and_source_lineage(system):
    client, store = system
    data = client.get('/v1/books/book_mock/manifest').json()['data']
    book = BookManifest.model_validate(data)
    assert book.facts[0].source_ref_ids[0] == book.source_refs[0].ref_id
    assert store.asset_path(book.pages[0].image_asset_id).is_file()
    data['facts'][0]['source_ref_ids']=['missing']
    with pytest.raises(ValidationError):
        BookManifest.model_validate(data)


def test_bad_requests_use_same_error_envelope(system):
    client, _ = system
    for response in [client.get('/v1/books/missing/manifest'), client.post('/v1/clips',json={}),
                     client.post('/v1/books',json=dict(title='bad',source_name='../secret.pdf'))]:
        body=response.json()
        assert response.status_code in (400,404,422)
        assert set(body)=={'request_id','status','data','error','warnings'}
        assert body['status']=='error' and body['error']['retryable'] is False


def test_idempotency_and_changed_payload_conflict(system):
    client, store=system
    a,b=submit(client),submit(client)
    assert a.status_code==202 and a.json()['data']==b.json()['data']
    conflict=client.post('/v1/clips',json=dict(shot_id='other',mode='mock',idempotency_key='key1'))
    assert conflict.status_code==409
    assert len([r for r in store.all('run') if r['status']=='queued'])==1


def test_actual_frame_hash_timing_and_range_response(system):
    client, store=system
    clip=store.all('clip')[0]
    video=store.asset_path(clip['asset_id'])
    expected=hashlib.sha256(video.read_bytes()).hexdigest()
    frames=store.all('frame')
    assert len(frames)==3
    for value in frames:
        frame=FrameEvidence.model_validate(value)
        assert frame.video_sha256==expected and frame.t_ms<clip['duration_ms']
        with Image.open(store.asset_path(frame.image_asset_id)) as im:
            assert im.size==(640,360)
    r=client.get('/v1/assets/'+clip['asset_id'],headers={'Range':'bytes=0-99'})
    assert r.status_code==206 and len(r.content)==100
    assert r.content==video.read_bytes()[:100]


def test_publication_requires_actual_frame_review(system):
    client, store=system
    assert client.get('/v1/books/book_mock/clips').json()['data']['clips']==[]
    clip=store.all('clip')[0]
    bad=client.patch('/v1/clips/'+clip['clip_id']+'/review',json=dict(expected_asset_version=1,reviewer='x',claims_by_frame={}))
    assert bad.status_code==422
    assert approve(client,store).status_code==200
    data=client.get('/v1/books/book_mock/clips').json()['data']
    assert len(data['clips'])==1 and len(data['frame_evidence'])==3
    assert data['clips'][0]['is_mock'] is True


def test_page_revision_invalidates_clips_and_frames(system):
    client,store=system
    approve(client,store)
    r=client.patch('/v1/pages/book_mock_p001/review',json=dict(expected_revision=1,reviewer='a'))
    assert r.status_code==200 and r.json()['data']['page']['page_revision']==2
    conflict=client.patch('/v1/pages/book_mock_p001/review',json=dict(expected_revision=1,reviewer='b'))
    assert conflict.status_code==409
    assert all(c['status']=='expired' for c in store.all('clip'))
    assert all(f['status']=='expired' for f in store.all('frame'))
    assert client.get('/v1/books/book_mock/clips').json()['data']['clips']==[]
    assert store.all('history')[0]['revision']==1


def test_queued_job_uses_input_version(system):
    client,store=system
    response=submit(client)
    client.patch('/v1/pages/book_mock_p001/review',json=dict(expected_revision=1,reviewer='editor'))
    result=work_once(store)
    assert result.status=='expired' and result.error.code=='STALE_INPUT'
    assert result.run_id==response.json()['data']['run_id']


def test_new_clip_version_expires_old_frame_evidence(system):
    client,store=system
    old=store.all('clip')[0]
    approve(client,store)
    submit(client,'new-version')
    result=work_once(store)
    assert result.status=='needs_review'
    clips=store.all('clip')
    assert sorted(c['asset_version'] for c in clips)==[1,2]
    assert store.get('clip',old['clip_id'])['status']=='expired'
    assert all(f['status']=='expired' for f in store.all('frame') if f['clip_id']==old['clip_id'])


def test_unconfigured_real_provider_never_claims_success(system):
    client,store=system
    book=store.get('book','book_mock');book['is_mock']=False
    store.put('book','book_mock',book)
    r=client.post('/v1/clips',json=dict(shot_id='book_mock_p001_r1_shot1',mode='real',idempotency_key='real'))
    assert r.status_code==202
    result=work_once(store)
    assert result.status=='failed' and result.error.code=='PROVIDER_NOT_CONFIGURED'
    assert result.outputs==[] and result.usage['external_calls']==0
    assert client.post('/v1/runs/'+result.run_id+'/retry').status_code==409


def test_cancel_and_bounded_restart_recovery(system):
    client,store=system
    queued=submit(client).json()['data']['run_id']
    assert client.post('/v1/runs/'+queued+'/cancel').status_code==200
    assert work_once(store) is None
    interrupted=new_run('ingest',dict(book_id='new',source_name='x.png',title='x'))
    interrupted.status='running';interrupted.attempts=2
    store.put('run',interrupted.run_id,interrupted)
    recover_interrupted(store)
    recovered=RunRecord.model_validate(store.get('run',interrupted.run_id))
    assert recovered.status=='failed' and recovered.error.retryable
    assert client.post('/v1/runs/'+interrupted.run_id+'/retry').status_code==409


def test_epub_spine_order_entities_and_duplicate_detection(tmp_path):
    src=tmp_path/'test.epub'
    image=tmp_path/'a.png';Image.new('RGB',(700,900),'white').save(image)
    with zipfile.ZipFile(src,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        z.writestr('OEBPS/book.opf','<package><manifest><item id="b" href="b.xhtml"/><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        z.writestr('OEBPS/a.xhtml','<html><body><img src="a.png"/><p>第一&nbsp;页</p></body></html>')
        z.writestr('OEBPS/b.xhtml','<html><body><img src="a.png"/><p>第二页</p></body></html>')
        z.write(image,'OEBPS/a.png')
    book=ingest(Store(tmp_path/'data'),src,'book_test','test')
    assert len(book.pages)==2
    assert 'a.xhtml' in book.pages[0].source_locator
    assert book.pages[0].ocr_lines[0].raw_text=='第一\xa0页'
    assert 'exact_duplicate_image' in book.pages[1].quality_flags


def test_import_queue_and_asset_scope(system):
    client,store=system
    inbox=store.root/'inbox';inbox.mkdir()
    Image.new('RGB',(720,900),'blue').save(inbox/'sample.png')
    response=client.post('/v1/books',json=dict(title='新测试',source_name='sample.png'))
    assert response.status_code==202
    result=work_once(store)
    assert result.status=='needs_review'
    bid=response.json()['data']['book_id']
    book=client.get('/v1/books/'+bid+'/manifest').json()['data']
    assert book['book_id']==bid and book['is_mock'] is False
    assert len(book['pages'])==1
    assert client.get('/v1/assets/unknown').status_code==404


def test_ocr_corrections_keep_raw_text_and_downgrade_facts(system):
    client,store=system
    book=store.get('book','book_mock')
    book['pages'][0]['ocr_lines']=[dict(line_id='line1',raw_text='错字',confidence=.3,region=dict(x1=0,y1=0,x2=1,y2=1),suggested_text=None,confirmed_text=None)]
    store.put('book','book_mock',book)
    r=client.patch('/v1/pages/book_mock_p001/review',json=dict(expected_revision=1,reviewer='editor',corrections={'line1':'正字'}))
    assert r.status_code==200
    new=store.get('book','book_mock')
    assert new['pages'][0]['ocr_lines'][0]['raw_text']=='错字'
    assert new['pages'][0]['ocr_lines'][0]['confirmed_text']=='正字'
    assert new['facts'][0]['status']=='draft'
    assert store.all('review')[0]['patch']['reviewer']=='editor'


def test_regions_reject_invalid_coordinates():
    for bbox in [dict(x1=.9,y1=0,x2=.1,y2=1),dict(x1=0,y1=0,x2=1.2,y2=1),dict(x1=float('nan'),y1=0,x2=1,y2=1)]:
        with pytest.raises(ValidationError):
            Region(**bbox)


def test_source_change_after_enqueue_is_rejected(system):
    client,store=system
    inbox=store.root/'inbox';inbox.mkdir()
    target=inbox/'sample.png'
    Image.new('RGB',(720,900),'blue').save(target)
    client.post('/v1/books',json=dict(title='new',source_name='sample.png'))
    Image.new('RGB',(720,900),'red').save(target)
    result=work_once(store)
    assert result.status=='failed' and result.error.code=='STALE_INPUT'
    assert len(store.all('book'))==1
