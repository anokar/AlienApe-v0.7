import asyncio,csv,json
from dataclasses import asdict,replace
from pathlib import Path
import pytest
from alienape.training import catalog,training_suite,plan_for,TrainingRunner,GROUPS
from alienape.experience import ExperienceStore,ModelFingerprint,SuccessSignal as S,confidence,SUITE_VERSION
from alienape.config import Settings
from alienape.routing import ModelRegistry,ModelRouter
from alienape.backends import tool_reply
from alienape.completion import FailureType as F
from test_completion_recovery import Script,response
from test_routed_agent import Pool
from test_routing import profile

def task(key):return next(t for t in catalog() if t.id.endswith('-'+key))

def setup(tmp_path,tasks,values,mode='native'):
    settings=Settings();p=profile('test',tool_mode=mode);p.capabilities+=['coding','planning','tool_use']
    settings.models=[p];store=ExperienceStore(tmp_path/'experience.sqlite3');registry=ModelRegistry([p])
    backend=values if hasattr(values,'chat') else Script(values)
    runner=TrainingRunner(registry,settings,store,['test'],pool=Pool({'test':backend}))
    runner.tasks=tasks
    return runner,backend,store,settings

@pytest.mark.parametrize('scope,max_count',[('quick',8),('standard',16)])
def test_bounded_plan(scope,max_count):
    p=profile('all',tool_mode='native');p.capabilities+=['coding','planning','tool_use']
    assert len(catalog())==16 and len(plan_for(p,catalog(),scope))==max_count
    assert len({t.id for t in catalog()})==16
    assert all(t.difficulty in {'EASY','NORMAL','HARD'} for t in catalog())
    assert all(len(contents.encode())<2000 for t in catalog() for _,contents in t.fixtures)

def test_capability_controls_not_four_unneeded_coding_tasks():
    p=profile('general',tool_mode='none')
    for scope in ['quick','standard']:
        selected=plan_for(p,catalog(),scope)
        assert sum(GROUPS[t.category]=='coding' for t in selected)<=1
        assert sum(GROUPS[t.category]=='reasoning' for t in selected)<=1
        assert not any(t.category=='TOOL_USE' for t in selected)
    assert 6<=len(plan_for(p,catalog()))<=8
    reason=profile('reason','reasoning',tool_mode='none')
    assert len(plan_for(reason,catalog()))==6

def test_same_shared_tasks_and_no_cartesian_explosion():
    a=profile('a');b=profile('b','coding',tool_mode='native')
    first={t.id:asdict(t) for t in plan_for(a,catalog())};second={t.id:asdict(t) for t in plan_for(b,catalog())}
    assert all(first[k]==second[k] for k in first.keys()&second.keys())
    assert {'PLAIN_TEXT','STRUCTURED_JSON','FILE_PROPOSAL','TOOL_ACTION'}=={t.response_contract for t in catalog()}

def test_success_fields_source_suite_resources_and_no_answer_cache(tmp_path):
    runner,b,store,s=setup(tmp_path,[task('general-add')],[response('12',input_tokens=71,output_tokens=3)])
    asyncio.run(runner.run());r=runner.results[0]
    assert r['verified_status']=='VERIFIED_SUCCESS' and r['model_calls']==1
    assert (r['input_tokens'],r['output_tokens'])==(71,3) and not r['tokens_estimated']
    assert r['suite_version']==SUITE_VERSION==3 and r['task_domain']=='GENERAL_SIMPLE' and r['response_contract']=='PLAIN_TEXT'
    with store.connection() as db:
        row=db.execute('SELECT * FROM model_experience').fetchone()
        assert row['source']=='benchmark' and row['suite_version']==3 and row['difficulty']=='EASY'
        assert row['task_domain']=='GENERAL_SIMPLE' and row['benchmark_task_id']==task('general-add').id
        assert db.execute('SELECT status FROM benchmark_runs').fetchone()[0]=='COMPLETED'
        assert 'answer' not in {c[1] for c in db.execute('PRAGMA table_info(model_experience)')}
    with (runner.output_directory/'benchmark_results.csv').open(encoding='utf-8-sig') as f:rows=list(csv.DictReader(f,delimiter=';'))
    assert rows[0]['model_fingerprint']==r['model_fingerprint']
    assert s.routing.min_samples_for_experience==5

@pytest.mark.parametrize('text,failure,hard',[('13','VALIDATION_FAILURE',1),('{','VALIDATION_FAILURE',1)])
def test_known_wrong_plain_values_are_verified(tmp_path,text,failure,hard):
    run,b,store,_=setup(tmp_path,[task('general-add')],[response(text)])
    asyncio.run(run.run());r=run.results[0]
    assert r['verified_status']=='VERIFIED_FAILURE' and r['failure_type']==failure
    assert store.summary(r['model_fingerprint'],'GENERAL_SIMPLE')['hard_sample_count']==hard

def test_format_not_domain_failure_but_contract_evidence(tmp_path):
    run,b,store,_=setup(tmp_path,[task('json-name')],[response('{')])
    asyncio.run(run.run());r=run.results[0]
    assert r['failure_type']=='FORMAT_FAILURE'
    assert store.summary(r['model_fingerprint'],'GENERAL_SIMPLE')['hard_sample_count']==0
    assert store.summary(r['model_fingerprint'],'GENERAL_SIMPLE','STRUCTURED_JSON')['success_rate']==0
    counts=store.training_breakdown(r['model_fingerprint'])
    assert counts['domains']['GENERAL_SIMPLE']['hard_sample_count']==0
    assert counts['contracts']['STRUCTURED_JSON']['hard_sample_count']==1

def test_missing_information_neutral(tmp_path):
    item=replace(task('file-owner'),fixtures=(),prompt='Analysiere missing.cfg und antworte als JSON mit owner und items.')
    run,b,store,_=setup(tmp_path,[item],[response('{"owner":"Ada","items":7}')])
    asyncio.run(run.run());r=run.results[0]
    assert r['failure_type']=='INFORMATION_MISSING' and not r['information_complete']
    assert store.summary(r['model_fingerprint'],'FILE_ANALYSIS')['hard_sample_count']==0

def test_resource_recovery_records_sufficiency_not_minimum(tmp_path):
    run,b,store,_=setup(tmp_path,[task('json-name')],[response('{','length'),response('{"name":"Ada","count":3}',output_tokens=22)])
    asyncio.run(run.run());r=run.results[0]
    assert r['verified_status']=='VERIFIED_SUCCESS' and r['resource_retries']==1
    assert (r['initial_requested_limit'],r['successful_requested_limit'],r['actual_success_output_tokens'])==(1024,2048,22)
    assert b.requests[0]['messages']==b.requests[1]['messages'] and r['model_calls']==2
    assert 'minimum_required_limit' not in r

def test_exhausted_resource_neutral(tmp_path):
    run,b,store,_=setup(tmp_path,[task('json-name')],[response('{','length'),response('{','length')])
    asyncio.run(run.run());r=run.results[0]
    assert r['failure_type']=='RESOURCE_OUTPUT_LIMIT' and r['resource_retries']==1
    assert store.summary(r['model_fingerprint'],'GENERAL_SIMPLE')['hard_sample_count']==0

def test_tool_correct_actual_read_and_components(tmp_path):
    run,b,store,_=setup(tmp_path,[task('tool-read')],[tool_reply('read_file',{'path':'fixture.txt'})])
    asyncio.run(run.run());r=run.results[0]
    assert r['verified_status']=='VERIFIED_SUCCESS' and r['tool_calls']==1
    assert all(r['tool_observation'][k] for k in ['selected','name_correct','arguments_correct','permission_allowed','tool_successful'])
    assert not r['tool_observation']['loop']

def test_tool_failure_is_not_capability_negative(tmp_path):
    run,b,store,_=setup(tmp_path,[task('tool-read')],[tool_reply('read_file',{'path':'wrong.txt'})])
    asyncio.run(run.run());r=run.results[0]
    assert r['failure_type']=='TOOL_FAILURE' and r['verified_status']=='VERIFIED_FAILURE'
    with store.connection() as db:assert db.execute('SELECT success_value FROM model_experience').fetchone()[0] is None
    assert store.training_breakdown(r['model_fingerprint'])['contracts']['TOOL_ACTION']['success_count']==0

def test_proposal_scope_and_known_content(tmp_path):
    value={'payload':'Vorschlag','status':'ok','proposed_files':[{'path':'note.txt','content':'READY'}]}
    run,b,store,_=setup(tmp_path,[task('file-proposal')],[response(json.dumps(value))],mode='none')
    asyncio.run(run.run());assert run.results[0]['verified_status']=='VERIFIED_SUCCESS'
    value['proposed_files'][0]['path']='../never.txt'
    run2,b,store,_=setup(tmp_path/'second',[task('file-proposal')],[response(json.dumps(value))],mode='none')
    asyncio.run(run2.run());assert run2.results[0]['failure_type']=='PERMISSION_FAILURE'
    assert store.summary(run2.results[0]['model_fingerprint'],'FILE_ANALYSIS')['hard_sample_count']==0

def test_stop_keeps_partial_and_no_next_task(tmp_path):
    class Slow(Script):
        def __init__(self):super().__init__([response('12'),response('7')]);self.begun=asyncio.Event();self.count=0
        async def chat(self,*args,**kwargs):
            self.count+=1
            if self.count==2:self.begun.set();await asyncio.sleep(10)
            return await super().chat(*args,**kwargs)
    async def work():
        backend=Slow();run,_,store,_=setup(tmp_path,[task('general-add'),task('general-fact'),task('json-name')],backend)
        job=asyncio.create_task(run.run());await asyncio.wait_for(backend.begun.wait(),2)
        run.stopped.set();job.cancel()
        with pytest.raises(asyncio.CancelledError):await job
        assert run.status=='ABORTED' and len(run.results)==1 and backend.count==2
        with store.connection() as db:
            assert db.execute('SELECT count(*) FROM model_experience').fetchone()[0]==1
            assert db.execute('SELECT status FROM benchmark_runs').fetchone()[0]=='ABORTED'
        assert (run.output_directory/'benchmark_results.csv').exists()
    asyncio.run(work())

def test_stop_before_start_no_modelcall(tmp_path):
    run,b,store,_=setup(tmp_path,[task('general-add')],[response('12')]);run.stopped.set()
    with pytest.raises(asyncio.CancelledError):asyncio.run(run.run())
    assert not b.requests and run.status=='ABORTED'

def test_no_cached_answer_second_run_really_calls_backend(tmp_path):
    backend=Script([response('12'),response('13')])
    for i in range(2):
        run,b,store,_=setup(tmp_path/str(i),[task('general-add')],backend);asyncio.run(run.run())
        assert run.results[0]['verified_status']==('VERIFIED_SUCCESS' if i==0 else 'VERIFIED_FAILURE')
    assert len(backend.requests)==2

@pytest.mark.parametrize('n,label',[(0,'VERY_LOW'),(2,'VERY_LOW'),(3,'LOW'),(4,'LOW'),(5,'MEDIUM'),(9,'MEDIUM'),(10,'HIGHER')])
def test_confidence_is_not_certainty(n,label):assert confidence(n)==label

def test_suite_old_and_fingerprint_not_mixed(tmp_path):
    store=ExperienceStore(tmp_path/'e.db');p=profile('p');fp=ModelFingerprint.for_profile(p)
    store.register(p,fp);store.record(fp.id,'GENERAL_SIMPLE',S.VERIFIED_SUCCESS,source='benchmark',suite_version=2)
    assert store.summary(fp.id,'GENERAL_SIMPLE')['hard_sample_count']==0
    new=replace(fp,digest='new-model');store.register(p,new)
    assert store.summary(fp.id,'GENERAL_SIMPLE') is None and any(r['stale'] for r in store.summaries())

def test_contract_rate_does_not_claim_general_domain_inability(tmp_path):
    store=ExperienceStore(tmp_path/'e.db');p=profile('p');fp=ModelFingerprint.for_profile(p);store.register(p,fp)
    store.record(fp.id,'GENERAL_SIMPLE',S.VERIFIED_SUCCESS,source='live',task_domain='GENERAL_SIMPLE',response_contract='PLAIN_TEXT',information_complete=True,failure_type='SUCCESS')
    store.record(fp.id,'GENERAL_SIMPLE',S.VERIFIED_FAILURE,source='benchmark',suite_version=3,task_domain='GENERAL_SIMPLE',response_contract='STRUCTURED_JSON',information_complete=True,failure_type='FORMAT_FAILURE')
    assert store.summary(fp.id,'GENERAL_SIMPLE')['success_rate']==1
    assert store.summary(fp.id,'GENERAL_SIMPLE','PLAIN_TEXT')['success_rate']==1
    assert store.summary(fp.id,'GENERAL_SIMPLE','STRUCTURED_JSON')['success_rate']==0

def test_no_extreme_or_empty_selection(tmp_path):
    run,b,store,s=setup(tmp_path,[task('general-add')],[response('12')])
    with pytest.raises(ValueError):TrainingRunner(run.registry,s,store,['test'],scope='EXTREME')
    with pytest.raises(ValueError):TrainingRunner(run.registry,s,store,[],scope='quick')

def test_three_model_ground_training_then_existing_router(tmp_path):
    from alienape.backends import MockBackend
    small=profile('SMALL_GENERAL','general','low',tool_mode='none')
    coder=profile('CODER','coding','medium',tool_mode='none')
    large=profile('LARGE','reasoning','high',tool_mode='none');large.capabilities+=['general','coding']
    settings=Settings();settings.models=[small,coder,large];registry=ModelRegistry(settings.models)
    store=ExperienceStore(tmp_path/'e.db')
    class TrainedMock(MockBackend):
        async def chat_with_mode(self,messages,tools=None,model='',mode='none'):
            prompt=json.loads(messages[-1]['content'])['task']
            text='12' if '7 + 5' in prompt else 'def solve(a,b): return a - b' if model!='SMALL_GENERAL' else 'def solve(a,b): return 999'
            return response(text)
    models={p.id:TrainedMock(delay=0) for p in settings.models}
    run=TrainingRunner(registry,settings,store,list(models),scope='standard',pool=Pool(models))
    # Zero-compute policy fixture gives the existing five-sample rule enough evidence.
    # These five distinct trial IDs are not added to the production catalog.
    run.tasks=[replace(task('general-add'),id=f'mock-general-{i}') for i in range(5)]+[replace(task('python-debug'),id=f'mock-debug-{i}') for i in range(5)]
    asyncio.run(run.run())
    fps={p.id:ModelFingerprint.for_profile(p,{'model_version':'demo-1','backend_version':'mock-1'}).id for p in settings.models}
    events=[];router=ModelRouter(registry,settings.routing,store,fps,lambda k,d:events.append(k))
    assert router.select_model('Was ist 7 + 5?').profile.id=='SMALL_GENERAL'
    assert router.select_model('Debug Python').profile.id=='CODER'
    for _ in range(5):store.record(fps['CODER'],'PYTHON_DEBUG',S.VERIFIED_FAILURE,failure_type='VALIDATION_FAILURE',task_domain='PYTHON_DEBUG',response_contract='PLAIN_TEXT',information_complete=True)
    assert router.select_model('Debug Python').profile.id=='LARGE'
    assert 'MODEL_SELECTED_FROM_EXPERIENCE' in events and 'MODEL_SKIPPED_FROM_EXPERIENCE' in events
    assert settings.routing.min_samples_for_experience==5

def test_unknown_does_not_change_hard_rate_and_live_remains_live(tmp_path):
    store=ExperienceStore(tmp_path/'e.db');p=profile('p');fp=ModelFingerprint.for_profile(p);store.register(p,fp)
    store.record(fp.id,'GENERAL_SIMPLE',S.VERIFIED_SUCCESS,source='live')
    store.record(fp.id,'GENERAL_SIMPLE',S.UNKNOWN,source='benchmark',suite_version=3)
    assert store.summary(fp.id,'GENERAL_SIMPLE')['hard_sample_count']==1
    with store.connection() as db:assert {r[0] for r in db.execute('SELECT source FROM model_experience')}=={'live','benchmark'}

def test_respects_model_call_budget_one(tmp_path):
    run,b,store,s=setup(tmp_path,[task('json-name')],[response('{','length')]);s.agent.max_steps=1
    asyncio.run(run.run());assert len(b.requests)==1 and run.results[0]['resource_retries']==0

def test_clipped_wrong_plain_output_not_subject_failure_or_speculative_retry(tmp_path):
    run,b,store,s=setup(tmp_path,[task('reason-order')],[response('A then B but ...','length')])
    asyncio.run(run.run());r=run.results[0]
    assert r['failure_type']=='RESOURCE_OUTPUT_LIMIT' and r['verified_status']=='UNKNOWN'
    assert r['resource_retries']==0 and len(b.requests)==1
    assert store.summary(r['model_fingerprint'],'REASONING')['hard_sample_count']==0
