import pytest
from alienape.config import Settings
from alienape.experience import ExperienceStore,ModelFingerprint,SuccessSignal as S,SUITE_VERSION
from alienape.onboarding import evidence_view
from alienape.routing import ModelRegistry,ModelRouter
from test_routing import profile


@pytest.fixture
def setup_store(tmp_path):
    store=ExperienceStore(tmp_path/'experience.sqlite3');a=profile('a','coding','low',tool_mode='none');b=profile('b','coding','medium',tool_mode='none')
    fps={}
    for p in (a,b):
        f=ModelFingerprint.for_profile(p);store.register(p,f);fps[p.id]=f.id
    return store,a,b,fps


@pytest.mark.parametrize('task,protocol',[(S.VERIFIED_SUCCESS,'VERIFIED_SUCCESS'),(S.VERIFIED_FAILURE,'VERIFIED_SUCCESS'),(S.VERIFIED_SUCCESS,'VERIFIED_FAILURE'),(S.VERIFIED_FAILURE,'VERIFIED_FAILURE')])
def test_independent_benchmark_record_and_view(setup_store,task,protocol):
    store,a,b,fps=setup_store
    store.record(fps['a'],'PYTHON_CODING',task,source='benchmark',suite_version=SUITE_VERSION,protocol_v2='file_content',capability_v2='coding',response_contract='FILE_CONTENT',information_complete=True,protocol_status=protocol)
    view=evidence_view(store,fps['a'])
    assert view['capabilities']['coding']['benchmark']['verified_successes']==int(task==S.VERIFIED_SUCCESS)
    assert view['protocols']['file_content']['benchmark']['verified_successes']==int(protocol=='VERIFIED_SUCCESS')
    assert view['protocols']['file_content']['benchmark']['verified_failures']==int(protocol=='VERIFIED_FAILURE')


def test_historical_null_stays_unknown(setup_store):
    store,a,b,fps=setup_store
    store.record(fps['a'],'PYTHON_CODING',S.VERIFIED_SUCCESS,source='benchmark',protocol_v2='file_content',response_contract='FILE_CONTENT')
    assert evidence_view(store,fps['a'])['protocols']['file_content']['benchmark']['unknown']==1
    with store.connection() as db:assert db.execute('SELECT protocol_status FROM model_experience').fetchone()[0] is None


@pytest.mark.parametrize('reason',['RESOURCE_OUTPUT_LIMIT','RESOURCE_CONTEXT_LIMIT','TIMEOUT','PERMISSION_FAILURE','INFORMATION_MISSING'])
def test_neutral_failures_cannot_be_recorded_as_protocol(setup_store,reason):
    store,a,b,fps=setup_store
    with pytest.raises(ValueError):store.record(fps['a'],'PYTHON_CODING',S.UNKNOWN,source='benchmark',response_contract='FILE_CONTENT',information_complete=True,failure_type=reason,protocol_status='VERIFIED_FAILURE')


def test_benchmark_routing_and_live_precedence(setup_store):
    store,a,b,fps=setup_store;s=Settings();s.routing.min_samples_for_experience=2
    for _ in range(2):
        store.record(fps['a'],'PYTHON_CODING',S.UNKNOWN,source='benchmark',suite_version=SUITE_VERSION,response_contract='FILE_CONTENT',information_complete=True,protocol_status='VERIFIED_FAILURE')
        store.record(fps['b'],'PYTHON_CODING',S.UNKNOWN,source='benchmark',suite_version=SUITE_VERSION,response_contract='FILE_CONTENT',information_complete=True,protocol_status='VERIFIED_SUCCESS')
    router=ModelRouter(ModelRegistry([a,b]),s.routing,store,fps)
    assert router.select_model('Write Python',state={'response_contract':'FILE_CONTENT'}).profile.id=='b'
    assert router.select_model('Write Python').profile.id=='a'
    for _ in range(2):
        store.record(fps['b'],'PYTHON_CODING',S.UNKNOWN,source='live',response_contract='FILE_CONTENT',information_complete=True,protocol_status='VERIFIED_FAILURE')
        store.record(fps['a'],'PYTHON_CODING',S.UNKNOWN,source='live',response_contract='FILE_CONTENT',information_complete=True,protocol_status='VERIFIED_SUCCESS')
    assert router.select_model('Write Python',state={'response_contract':'FILE_CONTENT'}).profile.id=='a'


def test_json_protocol_success_invalid_plan_separate(setup_store):
    store,a,b,fps=setup_store
    store.record(fps['a'],'REASONING',S.VERIFIED_FAILURE,source='benchmark',response_contract='STRUCTURED_JSON',protocol_v2='structured_json',capability_v2='reasoning',protocol_status='VERIFIED_SUCCESS')
    view=evidence_view(store,fps['a'])
    assert view['protocols']['structured_json']['benchmark']['verified_successes']==1
    assert view['capabilities']['reasoning']['benchmark']['verified_failures']==1


def test_normal_training_records_valid_json_with_failed_task(tmp_path):
    import asyncio
    from alienape.backends import MockBackend
    from alienape.completion import ModelResponse,CompletionMetadata
    from alienape.training import TrainingRunner,TrainingTask
    settings=Settings();p=profile('a','coding','low',tool_mode='none');store=ExperienceStore(tmp_path/'e.sqlite3')
    fp=ModelFingerprint.for_profile(p);store.register(p,fp)
    response=ModelResponse({'role':'assistant','content':'{"a": 2}'},completion=CompletionMetadata('stop'))
    backend=MockBackend([response],delay=0)
    runner=TrainingRunner(ModelRegistry([p]),settings,store,[p.id]);runner.planned=1
    task=TrainingTask('independent','GENERAL_SIMPLE','Return JSON','json',{'a':1},'STRUCTURED_JSON')
    result=asyncio.run(runner.measure(p,backend,fp,task,{}))
    assert result['signal']=='VERIFIED_FAILURE'
    assert result['protocol_status']=='VERIFIED_SUCCESS'
    with store.connection() as db:
        row=db.execute('SELECT success_signal,protocol_status FROM model_experience').fetchone()
        assert tuple(row)==('VERIFIED_FAILURE','VERIFIED_SUCCESS')


def test_single_or_stale_benchmark_does_not_qualify(setup_store):
    store,a,b,fps=setup_store;s=Settings();s.routing.min_samples_for_experience=2
    for _ in range(2):store.record(fps['a'],'PYTHON_CODING',S.UNKNOWN,source='benchmark',suite_version=SUITE_VERSION,response_contract='FILE_CONTENT',information_complete=True,protocol_status='VERIFIED_FAILURE')
    store.record(fps['b'],'PYTHON_CODING',S.UNKNOWN,source='benchmark',suite_version=SUITE_VERSION,response_contract='FILE_CONTENT',information_complete=True,protocol_status='VERIFIED_SUCCESS')
    router=ModelRouter(ModelRegistry([a,b]),s.routing,store,fps)
    assert router.select_model('Write Python',state={'response_contract':'FILE_CONTENT'}).profile.id=='a'
    store.record(fps['b'],'PYTHON_CODING',S.UNKNOWN,source='benchmark',suite_version=SUITE_VERSION,response_contract='FILE_CONTENT',information_complete=True,protocol_status='VERIFIED_SUCCESS')
    store.mark_stale(fps['b'])
    assert router.select_model('Write Python',state={'response_contract':'FILE_CONTENT'}).profile.id=='a'


def test_native_benchmark_can_qualify_without_file_content_claim(setup_store):
    store,a,_,fps=setup_store;s=Settings();s.routing.min_samples_for_experience=2
    native=profile('native','coding','medium',tool_mode='native');fp=ModelFingerprint.for_profile(native);store.register(native,fp);fps[native.id]=fp.id
    for _ in range(2):
        store.record(fps['a'],'PYTHON_CODING',S.UNKNOWN,source='benchmark',suite_version=SUITE_VERSION,response_contract='FILE_CONTENT',information_complete=True,protocol_status='VERIFIED_FAILURE')
        store.record(fp.id,'PYTHON_CODING',S.UNKNOWN,source='benchmark',suite_version=SUITE_VERSION,response_contract='TOOL_ACTION',information_complete=True,protocol_status='VERIFIED_SUCCESS')
    router=ModelRouter(ModelRegistry([a,native]),s.routing,store,fps)
    assert router.select_model('Write Python',state={'response_contract':'FILE_CONTENT'}).profile.id=='native'
