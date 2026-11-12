import json
import httpx
import pytest
from PIL import Image
from generation.providers import AgnesVideoProvider,ProviderError


@pytest.fixture
def sample(tmp_path):
    image=tmp_path/'source.png'
    Image.new('RGB',(400,600),'yellow').save(image)
    shot=dict(reference_region=dict(x1=.1,y1=.2,x2=.9,y2=.9),target_duration_ms=6000,prompt='One cat blinks.',negative_prompt='No extra cats.')
    return image,shot,tmp_path/'run'


def test_agnes_submit_poll_download_and_auth_isolation(sample):
    image,shot,output=sample
    seen=[]
    def handler(request):
        seen.append((request.method,str(request.url),request.headers.get('authorization')))
        if request.method=='POST':
            body=json.loads(request.content)
            assert body['model']=='agnes-video-2.5-flash' and body['seconds']=='6'
            assert body['mode']=='keyframe' and body['first_frame'].startswith('data:image/jpeg;base64,')
            return httpx.Response(200,json={'video_id':'video_test','id':'task_test','status':'queued'})
        if request.url.host=='apihub.agnes-ai.com':
            assert request.url.params['model_name']=='agnes-video-2.5-flash'
            return httpx.Response(200,json={'status':'completed','progress':100,'url':'https://media.example/test.mp4'})
        assert 'authorization' not in request.headers
        return httpx.Response(200,content=b'fake-test-media-not-for-publication')
    p=AgnesVideoProvider('private-test-key',transport=httpx.MockTransport(handler),poll_seconds=0)
    result=p.generate(shot,image,output,'run1')
    assert result.call_id=='video_test' and result.path.exists()
    assert 'private-test-key' not in (output/'provider_state.json').read_text()
    assert len(seen)==3
    p.generate(shot,image,output,'run1')
    assert len(seen)==3  # completed run reuses downloaded output


def test_timeout_resumes_same_remote_job_without_post(sample):
    image,shot,output=sample
    calls=[]
    def handler(request):
        calls.append(request.method)
        if request.method=='POST':return httpx.Response(200,json={'video_id':'same_job'})
        if request.url.host=='apihub.agnes-ai.com':return httpx.Response(200,json={'status':'completed','url':'https://media.example/video.mp4'})
        return httpx.Response(200,content=b'test')
    transport=httpx.MockTransport(handler)
    with pytest.raises(ProviderError) as error:
        AgnesVideoProvider('x',transport=transport,timeout_seconds=0).generate(shot,image,output,'run')
    assert error.value.code=='AGNES_POLL_TIMEOUT' and error.value.retryable
    result=AgnesVideoProvider('x',transport=transport,poll_seconds=0).generate(shot,image,output,'run')
    assert result.call_id=='same_job' and calls.count('POST')==1


def test_ambiguous_submission_never_reposts(sample):
    image,shot,output=sample
    count=[]
    def handler(request):
        count.append(1)
        raise httpx.ReadTimeout('simulated ambiguous request',request=request)
    p=AgnesVideoProvider('x',transport=httpx.MockTransport(handler))
    for _ in range(2):
        with pytest.raises(ProviderError) as error:p.generate(shot,image,output,'run')
        assert error.value.code=='SUBMISSION_UNCERTAIN'
    assert len(count)==1


def test_rejection_is_recorded_without_secret(sample):
    image,shot,output=sample
    p=AgnesVideoProvider('secret-value',transport=httpx.MockTransport(lambda request:httpx.Response(400,json={'detail':'invalid request'})))
    with pytest.raises(ProviderError) as error:p.generate(shot,image,output,'run')
    assert error.value.code=='AGNES_REQUEST_REJECTED'
    text=(output/'provider_state.json').read_text()
    assert 'secret-value' not in text and 'invalid request' in text


def test_service_error_does_not_trigger_second_submission(sample):
    image,shot,output=sample
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(503,json={'detail':'temporarily unavailable'})
    p=AgnesVideoProvider('x',transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as error:p.generate(shot,image,output,'run')
    assert error.value.code=='AGNES_SERVICE_UNAVAILABLE'
    with pytest.raises(ProviderError) as error:p.generate(shot,image,output,'run')
    assert error.value.code=='SUBMISSION_UNCERTAIN' and len(calls)==1


def test_crop_consent_hash_is_checked_before_network(sample):
    image,shot,output=sample
    shot['_consent_reference_sha256']='not_the_approved_image'
    p=AgnesVideoProvider('x',transport=httpx.MockTransport(lambda request:pytest.fail('must not send')))
    with pytest.raises(ProviderError) as error:p.generate(shot,image,output,'run')
    assert error.value.code=='UPLOAD_CONSENT_MISMATCH'


def test_explicit_queue_rejection_retries_then_stops_when_accepted(sample):
    image,shot,output=sample
    posts=[]
    def handler(request):
        if request.method=='POST':
            posts.append(1)
            if len(posts)==1:return httpx.Response(503,json={'code':'video_queue_full','message':'video queue is full','data':None},headers={'x-request-id':'queue_rejected_1'})
            return httpx.Response(200,json={'video_id':'accepted_once'})
        if request.url.host=='apihub.agnes-ai.com':return httpx.Response(200,json={'status':'completed','url':'https://media.example/video.mp4'})
        return httpx.Response(200,content=b'test')
    p=AgnesVideoProvider('x',transport=httpx.MockTransport(handler),busy_delays=(0,0),poll_seconds=0)
    result=p.generate(shot,image,output,'run')
    assert result.call_id=='accepted_once' and len(posts)==2
    state=json.loads((output/'provider_state.json').read_text())
    assert state['submission_responses'][0]['request_id']=='queue_rejected_1'
    p.generate(shot,image,output,'run')
    assert len(posts)==2


def test_business_error_200_has_bounded_retry_budget_across_restarts(sample):
    image,shot,output=sample
    posts=[]
    def handler(request):
        posts.append(1)
        return httpx.Response(200,json={'error':{'code':'video_queue_full','message':'video queue is full'},'data':None})
    p=AgnesVideoProvider('x',transport=httpx.MockTransport(handler),busy_delays=(0,0))
    for _ in range(2):
        with pytest.raises(ProviderError) as error:p.generate(shot,image,output,'run')
        assert error.value.code=='AGNES_QUEUE_FULL'
    assert len(posts)==3


def test_task_id_prevents_resubmission_even_if_status_is_error(sample):
    image,shot,output=sample
    methods=[]
    def handler(request):
        methods.append(request.method)
        if request.method=='POST':return httpx.Response(503,json={'video_id':'known_task','code':'video_queue_full'})
        if request.url.host=='apihub.agnes-ai.com':return httpx.Response(200,json={'status':'completed','url':'https://media.example/video.mp4'})
        return httpx.Response(200,content=b'test')
    p=AgnesVideoProvider('x',transport=httpx.MockTransport(handler),busy_delays=(0,0),poll_seconds=0)
    assert p.generate(shot,image,output,'run').call_id=='known_task'
    assert methods.count('POST')==1


def test_single_submission_consent_overrides_retry_budget(sample):
    image,shot,output=sample
    shot['_consent_max_submissions']=1
    posts=[]
    def handler(request):
        posts.append(1)
        return httpx.Response(503,json={'code':'video_queue_full'})
    provider=AgnesVideoProvider('x',transport=httpx.MockTransport(handler),busy_delays=(0,0))
    for _ in range(2):
        with pytest.raises(ProviderError):provider.generate(shot,image,output,'run')
    assert len(posts)==1


def test_unqueryable_task_id_blocks_retry_even_with_queue_error(sample):
    image,shot,output=sample
    posts=[]
    def handler(request):
        posts.append(1)
        return httpx.Response(503,json={'code':'video_queue_full','data':{'task_id':'needs_platform_lookup'}})
    provider=AgnesVideoProvider('x',transport=httpx.MockTransport(handler),busy_delays=(0,0))
    for _ in range(2):
        with pytest.raises(ProviderError) as error:provider.generate(shot,image,output,'run')
        assert error.value.code=='SUBMISSION_UNCERTAIN'
    assert len(posts)==1
    assert json.loads((output/'provider_state.json').read_text())['task_id']=='needs_platform_lookup'
