import asyncio,json
from alienape.backends import MockBackend
from alienape.completion import ModelResponse,CompletionMetadata
from alienape.config import Settings
from alienape.experience import ExperienceStore,ModelFingerprint
from alienape.onboarding import project_plan_qualification_tasks
from alienape.routing import ModelRegistry
from alienape.training import TrainingRunner
from test_routing import profile


def measure(tmp_path,plan):
    settings=Settings();p=profile('a','coding','low',tool_mode='none');store=ExperienceStore(tmp_path/'e.sqlite3')
    fp=ModelFingerprint.for_profile(p);store.register(p,fp)
    response=ModelResponse({'role':'assistant','content':json.dumps(plan)},completion=CompletionMetadata('stop'))
    runner=TrainingRunner(ModelRegistry([p]),settings,store,[p.id]);runner.planned=1
    result=asyncio.run(runner.measure(p,MockBackend([response],delay=0),fp,project_plan_qualification_tasks()[0],{}))
    return result


def test_valid_json_valid_plan_is_independent_success(tmp_path):
    result=measure(tmp_path,{'files':[{'path':'a.py','purpose':'x','language':'python','depends_on':[]}]})
    assert result['signal']=='VERIFIED_SUCCESS' and result['protocol_status']=='VERIFIED_SUCCESS'


def test_valid_json_cycle_is_task_failure_not_protocol_failure(tmp_path):
    result=measure(tmp_path,{'files':[{'path':'a.py','purpose':'x','language':'python','depends_on':['a.py']}]})
    assert result['signal']=='VERIFIED_FAILURE' and result['protocol_status']=='VERIFIED_SUCCESS'


def test_explicit_normal_runner_includes_unknown_planner_profile(tmp_path):
    from alienape.onboarding import project_plan_qualification_tasks
    from alienape.training import plan_for
    p=profile('unknown','coding','low',tool_mode='none')
    tasks=project_plan_qualification_tasks()
    assert len(tasks)==6
    assert plan_for(p,tasks,'project_plan')==list(tasks)
    assert p.v2().protocol_capabilities['structured_json']=='UNKNOWN'


def test_fenced_plan_does_not_count_as_plan_success(tmp_path):
    from alienape.backends import MockBackend
    settings=Settings();p=profile('a','coding','low',tool_mode='none');store=ExperienceStore(tmp_path/'e.sqlite3')
    fp=ModelFingerprint.for_profile(p);store.register(p,fp)
    text='```json\n'+json.dumps({'files':[{'path':'a.py','purpose':'x','language':'python','depends_on':[]}]})+'\n```'
    response=ModelResponse({'role':'assistant','content':text},completion=CompletionMetadata('stop'))
    runner=TrainingRunner(ModelRegistry([p]),settings,store,[p.id]);runner.planned=1
    result=asyncio.run(runner.measure(p,MockBackend([response],delay=0),fp,project_plan_qualification_tasks()[0],{}))
    assert result['protocol_status']=='VERIFIED_FAILURE'
    assert result['signal']=='UNKNOWN'


def test_normal_project_plan_runner_persists_six_fingerprinted_cases(tmp_path,monkeypatch):
    import tempfile
    class Scripted(MockBackend):pass
    class Pool:
        def __init__(self,b):self.b=b
        def get(self,p):return self.b
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    plan={'files':[{'path':'a.py','purpose':'x','language':'python','depends_on':[]}]}
    replies=[ModelResponse({'role':'assistant','content':json.dumps(plan)},completion=CompletionMetadata('stop')) for _ in range(6)]
    p=profile('a','coding','low',tool_mode='none');settings=Settings();store=ExperienceStore(tmp_path/'e.sqlite3')
    runner=TrainingRunner(ModelRegistry([p]),settings,store,[p.id],scope='project_plan',pool=Pool(Scripted(replies,delay=0)))
    asyncio.run(runner.run())
    with store.connection() as db:
        rows=db.execute('select source,protocol_status,success_signal,model_fingerprint_id from model_experience').fetchall()
    assert len(rows)==6 and all(tuple(row)[:3]==('benchmark','VERIFIED_SUCCESS','VERIFIED_SUCCESS') for row in rows)
    assert len({row['model_fingerprint_id'] for row in rows})==1
