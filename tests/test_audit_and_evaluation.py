import numpy as np
import pytest
from PIL import Image
from generation.evaluation import evaluate_ocr,edit_distance
from generation.text_rows import chinese_rows
from generation.workflow import work_once
from test_pipeline import seed,system,approve


def test_cer_counts_deletions_insertions_and_excludes_pinyin():
    result=evaluate_ocr([dict(sample_id='test',page_id='synthetic',method='test',reference_text='猫在树旁。',recognized_text='mao 猫在旁！')])
    assert result['reference_characters']==4 and result['edit_errors']==1 and result['micro_cer']==.25
    assert edit_distance('猫','两只猫')==2
    with pytest.raises(ValueError):evaluate_ocr([dict(sample_id='x',page_id='x',method='x',reference_text='',recognized_text='猫')])


def test_row_filter_keeps_tall_text_band_and_original_coordinates(tmp_path):
    pixels=np.full((120,240,3),255,np.uint8);pixels[15:21,20:220]=0;pixels[40:65,20:220]=0
    path=tmp_path/'synthetic_rows.png';Image.fromarray(pixels).save(path)
    rows=chinese_rows(path,[0,0,1,1],tmp_path/'out')
    assert len(rows)==1 and .2<rows[0]['source_region'][1]<.4
    assert rows[0]['source_region'][3]>.5


def test_vlm_finding_cannot_be_published_without_actual_review_resolution(system,monkeypatch):
    client,store=system;clip=store.all('clip')[0];approve(client,store,clip)
    def fake(kind,output,**kwargs):
        parsed=dict(visible_objects=[],explicit_text='',visual_summary='合成书',uncertainties=[],potential_conflicts=['测试注入：误把书识别为猫'])
        return dict(parsed=parsed,raw_text='RAW_SYNTHETIC_AUDIT',elapsed_ms=1,peak_memory_bytes=20,model_id='test')
    monkeypatch.setattr('generation.frame_audit.run_local',fake)
    r=client.post('/v1/clips/'+clip['clip_id']+'/validate',json=dict(expected_asset_version=1));assert r.status_code==202
    run=work_once(store);assert run.status=='needs_review'
    assert store.get('clip',clip['clip_id'])['status']=='needs_review'
    audit=store.get('clip_audit',run.outputs[0]);assert audit['observations'][0]['raw_text']=='RAW_SYNTHETIC_AUDIT'
    frames=store.all('frame');reviewer='assistant_test_actual_synthetic_frame_review'
    body=dict(expected_asset_version=1,reviewer=reviewer,claims_by_frame={f['frame_id']:[dict(claim='实际合成书',region=dict(x1=0,y1=0,x2=1,y2=1),observed_by=reviewer,method='vlm_frame_review')] for f in frames})
    assert client.patch('/v1/clips/'+clip['clip_id']+'/review',json=body).json()['error']['code']=='QUALITY_FINDING_UNRESOLVED'
    assert store.get('clip_audit',audit['audit_id'])['status']=='needs_review'
    body['audit_resolution_notes']={f['finding_id']:'对照实际合成测试帧，该模型判断为误报。' for f in audit['findings']}
    assert client.patch('/v1/clips/'+clip['clip_id']+'/review',json=body).status_code==200
    assert store.get('run',run.run_id)['status']=='succeeded'
    assert all(f['status']=='resolved_by_reviewer' for f in store.get('clip_audit',audit['audit_id'])['findings'])
