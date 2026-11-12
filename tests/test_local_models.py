import json
import math
import struct
import wave
import subprocess
from generation.workflow import work_once

# Reuse the isolated fixture builder from the existing contract tests.
from test_pipeline import seed,system


def test_vlm_output_stays_draft_and_preserves_book_facts(system,monkeypatch):
    client,store=system
    before=store.get('book','book_mock')['facts']
    def fake(image,output):
        return dict(model_id='test-vlm',raw_text='{"visible_objects":[]}',parsed=dict(visible_objects=[],explicit_text='',visual_summary='测试',uncertainties=[]),elapsed_ms=1,peak_memory_bytes=1024)
    monkeypatch.setattr('generation.workflow.understand_page',fake)
    response=client.post('/v1/pages/book_mock_p001/understand')
    assert response.status_code==202
    result=work_once(store)
    assert result.status=='needs_review'
    observations=client.get('/v1/pages/book_mock_p001/understandings').json()['data']
    assert observations[0]['schema_status']=='passed' and observations[0]['status']=='needs_review'
    assert store.get('book','book_mock')['facts']==before


def test_bad_vlm_json_is_kept_for_correction(system,monkeypatch):
    client,store=system
    monkeypatch.setattr('generation.workflow.understand_page',lambda image,output:dict(model_id='test-vlm',raw_text='bad json',elapsed_ms=1,peak_memory_bytes=100))
    client.post('/v1/pages/book_mock_p001/understand')
    result=work_once(store)
    assert result.status=='needs_review'
    assert store.get('understanding',result.outputs[0])['schema_status']=='needs_correction'


def test_narration_uses_waveform_duration_and_versions_media(system,monkeypatch):
    client,store=system
    old=store.all('clip')[0]
    def fake(text,output):
        output.mkdir(parents=True,exist_ok=True)
        with wave.open(str(output/'narration.wav'),'wb') as f:
            f.setparams((1,2,24000,0,'NONE','not compressed'))
            f.writeframes(b''.join(struct.pack('<h',int(1000*math.sin(2*math.pi*440*i/24000))) for i in range(24000)))
        return dict(model_id='test-tts',audio_file='narration.wav',elapsed_ms=1,peak_memory_bytes=100)
    monkeypatch.setattr('generation.workflow.synthesize',fake)
    response=client.post('/v1/clips/'+old['clip_id']+'/narration',json=dict(expected_asset_version=1))
    assert response.status_code==202
    result=work_once(store)
    assert result.status=='needs_review'
    clip=store.get('clip',result.outputs[0])
    assert clip['asset_version']==2 and clip['is_mock']
    assert store.get('asset',clip['audio_asset_id'])['is_mock'] is True
    assert clip['source_video_asset_id']==old['asset_id']
    assert store.get('clip',old['clip_id'])['status']=='expired'
    assert result.usage['subtitle_start_ms']==200 and result.usage['subtitle_end_ms']==1200
    content=store.asset_path(clip['subtitle_asset_id']).read_text()
    assert '00:00:00.200 --> 00:00:01.200' in content
    probe=subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(store.asset_path(clip['asset_id']))],capture_output=True,text=True,check=True)
    assert {s['codec_type'] for s in json.loads(probe.stdout)['streams']}=={'video','audio','subtitle'}


def test_standalone_narration_is_available_before_video(system,monkeypatch):
    client,store=system
    def fake(text,output):
        output.mkdir(parents=True,exist_ok=True)
        with wave.open(str(output/'narration.wav'),'wb') as f:
            f.setparams((1,2,24000,0,'NONE','not compressed'));f.writeframes(b'\x00\x00'*24000)
        return dict(model_id='test-tts',audio_file='narration.wav',text=text,speaker='Vivian',duration_ms=1000,sample_rate=24000,elapsed_ms=1,peak_memory_bytes=10)
    monkeypatch.setattr('generation.workflow.synthesize',fake)
    response=client.post('/v1/shots/book_mock_p001_r1_shot1/narration')
    assert response.status_code==202
    result=work_once(store)
    assert result.status=='needs_review'
    data=client.get('/v1/books/book_mock/narrations').json()['data']
    assert len(data)==1 and data[0]['duration_ms']==1000
    assert len(store.all('clip'))==1  # standalone TTS never creates a video
    client.patch('/v1/pages/book_mock_p001/review',json=dict(expected_revision=1,reviewer='editor'))
    assert store.get('narration',result.outputs[0])['status']=='expired'


def test_unreviewed_shot_does_not_start_tts(system):
    client,store=system
    shot=store.get('shot','book_mock_p001_r1_shot1');shot['status']='needs_review'
    store.put('shot',shot['shot_id'],shot)
    assert client.post('/v1/shots/'+shot['shot_id']+'/narration').status_code==409


def test_illustration_crop_boxes_map_back_to_original_page(system,monkeypatch):
    client,store=system
    def fake(image,output):
        assert image.name=='source_crop.jpg'
        return dict(model_id='test-vlm',raw_text='raw crop-space output',parsed=dict(visible_objects=[dict(name='书',description='矩形',bbox=[0,0,1,1])],explicit_text='',visual_summary='书',uncertainties=[]),elapsed_ms=1,peak_memory_bytes=100)
    monkeypatch.setattr('generation.workflow.understand_page',fake)
    assert client.post('/v1/pages/book_mock_p001/understand?source_ref_id=missing').status_code==422
    assert client.post('/v1/pages/book_mock_p001/understand?source_ref_id=mock_ref1').status_code==202
    result=work_once(store)
    observation=store.get('understanding',result.outputs[0])
    assert observation['parsed']['visible_objects'][0]['bbox']==[.3,.24,.7,.76]
    assert observation['coordinate_space']=='original_page_normalized'
