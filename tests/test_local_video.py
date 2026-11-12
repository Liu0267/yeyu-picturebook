"""Provider routing and upload boundaries; media here are synthetic test fixtures."""
import pytest
from types import SimpleNamespace
from PIL import Image
from generation.providers import ProviderError,VideoResult,configured_video_provider
from generation.wan_local import LocalWanVideoProvider
from generation.workflow import execute_run,new_run
from test_pipeline import seed,system


def test_local_routing_does_not_require_an_agnes_key(monkeypatch,tmp_path):
    monkeypatch.setenv('YU_VIDEO_PROVIDER','wan_local')
    monkeypatch.setenv('YU_WAN_EXECUTABLE',str(tmp_path/'sd-cli'))
    monkeypatch.setenv('YU_WAN_MODEL_DIR',str(tmp_path/'weights'))
    provider=configured_video_provider()
    assert isinstance(provider,LocalWanVideoProvider)
    assert provider.requires_external_upload is False


def test_unconfigured_local_video_never_invokes_subprocess(tmp_path,monkeypatch):
    monkeypatch.setattr('generation.wan_local.subprocess.run',lambda *a,**k:pytest.fail('must not run'))
    with pytest.raises(ProviderError) as error:
        LocalWanVideoProvider(tmp_path/'missing',tmp_path/'missing_weights').generate({},tmp_path/'source',tmp_path/'out','run')
    assert error.value.code=='LOCAL_VIDEO_NOT_CONFIGURED'


def test_local_provider_can_run_with_external_upload_disabled(system):
    client,store=system
    book=store.get('book','book_mock');book.update(is_mock=False,external_upload_allowed=False)
    store.put('book',book['book_id'],book)
    shot=store.get('shot','book_mock_p001_r1_shot1')
    fixture=store.asset_path(store.all('clip')[0]['asset_id'])
    class LocalFixture:
        requires_external_upload=False
        def generate(self,shot,reference,output,run_id):
            return VideoResult(fixture,run_id,'synthetic-unit-test-provider',dict(external_calls=0))
    result=execute_run(store,new_run('video_generation',dict(shot=shot),False),LocalFixture())
    assert result.status=='needs_review' and result.usage['external_calls']==0
    assert store.all('upload_consent')==[]


def test_unknown_provider_keeps_external_upload_gate(system):
    client,store=system
    book=store.get('book','book_mock');book.update(is_mock=False,external_upload_allowed=False)
    store.put('book',book['book_id'],book)
    class UnknownFixture:
        def generate(self,*args):pytest.fail('must not send without consent')
    shot=store.get('shot','book_mock_p001_r1_shot1')
    result=execute_run(store,new_run('video_generation',dict(shot=shot),False),UnknownFixture())
    assert result.status=='failed' and result.error.code=='EXTERNAL_UPLOAD_NOT_ENABLED'


def test_changed_local_input_cannot_overwrite_existing_run_evidence(tmp_path,monkeypatch):
    executable=tmp_path/'sd-cli';executable.write_text('synthetic-test-executable')
    weights=tmp_path/'weights';weights.mkdir()
    for name in ['Wan2.2-TI2V-5B-Q4_K_S.gguf','umt5-xxl-encoder-Q4_K_M.gguf','taew2_2.safetensors']:
        (weights/name).write_text('synthetic-test-weight')
    source=tmp_path/'source.png';Image.new('RGB',(64,64),'yellow').save(source)
    shot=dict(reference_region=dict(x1=0,y1=0,x2=1,y2=1),target_duration_ms=4000,prompt='test',negative_prompt='test')
    monkeypatch.setattr('generation.wan_local.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=1))
    provider=LocalWanVideoProvider(executable,weights);output=tmp_path/'run'
    with pytest.raises(ProviderError):provider.generate(shot,source,output,'run')
    before={name:(output/name).read_bytes() for name in ['reference.jpg','input.png','provider_state.json']}
    Image.new('RGB',(64,64),'blue').save(source)
    with pytest.raises(ProviderError) as error:provider.generate(shot,source,output,'run')
    assert error.value.code=='PROVIDER_INPUT_CHANGED'
    assert all((output/name).read_bytes()==data for name,data in before.items())


def test_changed_end_pose_preserves_prior_generation_evidence(tmp_path,monkeypatch):
    executable=tmp_path/'sd-cli';executable.write_text('synthetic-executable')
    weights=tmp_path/'weights';weights.mkdir()
    for name in ['Wan2.2-TI2V-5B-Q4_K_S.gguf','umt5-xxl-encoder-Q4_K_M.gguf','taew2_2.safetensors']:(weights/name).write_text('synthetic-weight')
    source=tmp_path/'source.png';end=tmp_path/'end.png';Image.new('RGB',(64,64),'yellow').save(source);Image.new('RGB',(64,64),'blue').save(end)
    shot=dict(reference_region=dict(x1=0,y1=0,x2=1,y2=1),target_duration_ms=2000,prompt='test',negative_prompt='test',_end_reference_path=str(end))
    monkeypatch.setattr('generation.wan_local.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=1))
    provider=LocalWanVideoProvider(executable,weights);out=tmp_path/'run'
    with pytest.raises(ProviderError):provider.generate(shot,source,out,'run')
    before={name:(out/name).read_bytes() for name in ['input.png','end_input.png','provider_state.json']}
    Image.new('RGB',(64,64),'green').save(end)
    with pytest.raises(ProviderError) as error:provider.generate(shot,source,out,'run')
    assert error.value.code=='PROVIDER_INPUT_CHANGED'
    assert all((out/name).read_bytes()==data for name,data in before.items())
