import asyncio,ast,hashlib,json,sys,threading
from pathlib import Path
from dataclasses import replace
import pytest
from alienape.verification import VerificationRunner,VerificationPlan
from alienape.config import Settings,VerificationSettings
from alienape.permissions import PermissionManager
from alienape.tools import Workspace
from alienape.routed_agent import RoutedAgent
from alienape.routing import ModelRegistry
from alienape.experience import ExperienceStore
from alienape.backends import tool_reply
from alienape.completion import CompletionRejected
from test_routing import profile
from test_routed_agent import Pool
from test_completion_recovery import Script,response

def runner(tmp_path,*,allowed=True,timeout=10):
    events=[];stop=threading.Event();permissions=[]
    async def permission(name,args,path):permissions.append((name,args,path));return allowed
    def check():
        if stop.is_set():raise asyncio.CancelledError()
    r=VerificationRunner(Workspace(tmp_path),VerificationSettings(timeout_seconds=timeout),check,permission,lambda k,d:events.append((k,d)))
    return r,events,stop,permissions

def files(root,source='def value(): return 1',expect=1):
    (root/'inventory.py').write_text(source,encoding='utf-8')
    (root/'test_inventory.py').write_text(f'from inventory import value\ndef test_value(): assert value()=={expect}\n',encoding='utf-8')

def agent(root,source,task=None,*,permission=True,godot=None):
    settings=Settings();settings.models=[profile('coder','coding',tool_mode='native')]
    settings.verification.godot_executable=godot or ''
    events=[];p=PermissionManager({'write_file':'auto','verify_python_tests':'auto' if permission else 'block','verify_godot':'auto' if permission else 'block'})
    async def allow(name,args,path):return await p.request(name,args,path,lambda *a:asyncio.sleep(0,result='once'))
    backend=Script([tool_reply('write_file',{'path':'inventory.py','content':source}),response('Done')])
    store=ExperienceStore(root.parent/(root.name+'-experience.sqlite3'))
    a=RoutedAgent(Workspace(root),settings,registry=ModelRegistry(settings.models),pool=Pool({'coder':backend}),permission=allow,experience=store,observe=lambda k,d,*rest:events.append((k,d)))
    return a,backend,store,events

@pytest.mark.parametrize('source,status',[('def value(): return 1','PASS'),('def value(:','FAIL')])
def test_ast_no_execution(tmp_path,source,status):
    (tmp_path/'code.py').write_text(source)
    r,*_=runner(tmp_path);r.create_plan(['code.py'])
    outcome=r.syntax(['code.py']);assert outcome['status']==status
    if status=='FAIL':assert outcome['syntax_errors'][0]['file']=='code.py' and outcome['syntax_errors'][0]['line']==1

@pytest.mark.parametrize('before,after,expected_signal',[(1,1,'VERIFIED_SUCCESS'),(2,1,'VERIFIED_SUCCESS'),(1,2,'VERIFIED_FAILURE'),(2,2,'UNKNOWN')])
def test_real_pytest_baselines_and_domain_experience(tmp_path,before,after,expected_signal):
    root=tmp_path/'project';root.mkdir();files(root,f'def value(): return {before}')
    a,b,store,events=agent(root,f'def value(): return {after}\n# changed\n')
    answer=asyncio.run(a.run('The tests in this Python project are failing. Find the cause and fix the implementation.'))
    result=a.verification.result
    assert result.baseline_status==('PASS' if before==1 else 'FAIL')
    assert result.post_status==('PASS' if after==1 else 'FAIL') and result.task_signal==expected_signal
    with store.connection() as db:
        row=db.execute("SELECT * FROM model_experience WHERE task_category!='TOOL_USE' ORDER BY id DESC").fetchone()
        assert row['success_signal']==expected_signal and json.loads(row['verification_json'])['post_status']==result.post_status
    assert len(b.requests)==2 # No verification-driven repair/model retry.
    assert [k for k,d in events].index('VERIFICATION_PLAN_CREATED')<[k for k,d in events].index('MODEL_CALL')
    assert [k for k,d in events].index('VERIFICATION_BASELINE')<[k for k,d in events].index('FILE_WRITE')
    if after==2:assert 'Änderung wurde geschrieben' in answer

def test_syntax_damage_write_success_task_failure(tmp_path):
    root=tmp_path/'project';root.mkdir();files(root)
    a,b,store,events=agent(root,'def broken(:')
    asyncio.run(a.run('The tests are failing. Find the cause and fix the implementation.'))
    assert a.resources.counters.file_writes==1 and a.verification.result.task_signal=='VERIFIED_FAILURE'
    assert a.verification.result.post['syntax_errors'][0]['line']==1
    assert (root/'inventory.py').read_text()=='def broken(:' # No rollback.

def test_noop_write_cannot_become_verified_task_success(tmp_path):
    root=tmp_path/'project';root.mkdir();files(root)
    a,b,store,events=agent(root,'def value(): return 1')
    asyncio.run(a.run('The tests are failing. Find the cause and fix the implementation.'))
    assert a.resources.counters.file_writes==1 and not a.changed_paths
    with store.connection() as db:assert db.execute("SELECT success_signal FROM model_experience WHERE task_category!='TOOL_USE'").fetchone()[0]=='UNKNOWN'

def test_create_nested_tests_ignores_parent_root_and_conftest(tmp_path):
    (tmp_path/'pyproject.toml').write_text('[tool.pytest.ini_options]\naddopts=""\n')
    (tmp_path/'conftest.py').write_text('raise RuntimeError("OUTSIDE_WORKSPACE_CONFT EST_MUST_NOT_RUN")\n')
    root=tmp_path/'project';root.mkdir();a,b,store,events=agent(root,'unused')
    b.responses=iter([tool_reply('write_file',{'path':'slugify.py','content':'def slugify(text): return "-".join(text.lower().split())\n'}),
        tool_reply('write_file',{'path':'tests/test_slugify.py','content':'from slugify import slugify\ndef test_slugify(): assert slugify("Hello World")=="hello-world"\n'}),response('Created')])
    asyncio.run(a.run('Create slugify.py and tests/test_slugify.py implementing slugify(text) and automated tests.'))
    assert a.verification.result.verification_status=='PASS'
    with store.connection() as db:
        row=db.execute("SELECT * FROM model_experience WHERE task_category='PYTHON_CODING'").fetchone()
        assert row['success_signal']=='VERIFIED_SUCCESS' and json.loads(row['verification_json'])['post_status']=='PASS'

def test_tool_success_ast_only_not_task_success(tmp_path):
    r,*_=runner(tmp_path);(tmp_path/'code.py').write_text('x=1')
    async def run():
        await r.prepare(['code.py']);(tmp_path/'code.py').write_text('x=2');return await r.post(['code.py'])
    result=asyncio.run(run());assert result.verification_status=='PASS' and result.task_signal=='UNKNOWN'

def test_permission_denied_no_process_and_no_negative(tmp_path):
    files(tmp_path);r,events,_,permissions=runner(tmp_path,allowed=False)
    async def forbidden(args):raise AssertionError('Must not start a process')
    r.process=forbidden
    async def run():await r.prepare(['inventory.py']);return await r.post(['inventory.py'])
    result=asyncio.run(run());assert result.verification_status=='UNAVAILABLE' and result.task_signal=='UNKNOWN' and permissions

def test_missing_pytest_no_unittest_is_unavailable(tmp_path):
    files(tmp_path);r,*_=runner(tmp_path)
    async def no_pytest(args):return dict(status='COMPLETED',exit_code=0,stdout='PYTEST_ABSENT',stderr='')
    r.process=no_pytest
    async def run():await r.prepare(['inventory.py']);return await r.post(['inventory.py'])
    result=asyncio.run(run());assert result.verification_status=='UNAVAILABLE' and result.task_signal=='UNKNOWN'

def test_unknown_framework_probe_is_error(tmp_path):
    files(tmp_path);r,*_=runner(tmp_path)
    async def unexpected(args):return dict(status='COMPLETED',exit_code=0,stdout='something else',stderr='')
    r.process=unexpected
    async def run():await r.prepare(['inventory.py']);return await r.post(['inventory.py'])
    result=asyncio.run(run());assert result.verification_status=='ERROR' and result.task_signal=='UNKNOWN'

def test_zero_unittest_tests_is_not_verified_success(tmp_path):
    files(tmp_path);(tmp_path/'test_inventory.py').write_text('import unittest\nclass Empty(unittest.TestCase): pass\n')
    r,*_=runner(tmp_path);original=r.process
    async def absent(args):
        if '-c' in args:return dict(status='COMPLETED',exit_code=0,stdout='PYTEST_ABSENT',stderr='')
        return await original(args)
    r.process=absent
    async def run():await r.prepare(['inventory.py']);return await r.post(['inventory.py'])
    result=asyncio.run(run());assert result.verification_status=='UNAVAILABLE' and result.task_signal=='UNKNOWN'

def test_unittest_fallback_explicit_adapter(tmp_path):
    files(tmp_path);(tmp_path/'test_inventory.py').write_text('import unittest\nfrom inventory import value\nclass T(unittest.TestCase):\n def test_value(self): self.assertEqual(value(),1)\n')
    r,*_=runner(tmp_path);original=r.process;commands=[]
    async def no_pytest(args):
        commands.append(args)
        if '-c' in args:return dict(status='COMPLETED',exit_code=0,stdout='PYTEST_ABSENT',stderr='')
        return await original(args)
    r.process=no_pytest
    async def run():await r.prepare(['inventory.py']);return await r.post(['inventory.py'])
    result=asyncio.run(run());assert result.verification_status=='PASS' and result.post['adapter']=='PYTHON_UNITTEST'
    assert all('discover' in c for c in commands if '-c' not in c)

@pytest.mark.parametrize('status',['ERROR','TIMEOUT','UNAVAILABLE'])
def test_verifier_problem_never_capability_negative(tmp_path,status):
    files(tmp_path);r,*_=runner(tmp_path)
    async def problem(args):return {'status':status,'timeout':status=='TIMEOUT'}
    r.process=problem
    async def run():await r.prepare(['inventory.py']);return await r.post(['inventory.py'])
    result=asyncio.run(run());assert result.verification_status==status and result.task_signal=='UNKNOWN'

def test_missing_executable(tmp_path):
    files(tmp_path);r,*_=runner(tmp_path);r.python=str(tmp_path/'missing-python.exe')
    async def run():await r.prepare(['inventory.py']);return await r.post(['inventory.py'])
    assert asyncio.run(run()).verification_status=='UNAVAILABLE'

def test_process_output_is_bounded_and_hashes_entire_stream(tmp_path):
    r,*_=runner(tmp_path);r.create_plan(['code.py']);r.plan=replace(r.plan,output_limit=256)
    result=asyncio.run(r.process([sys.executable,'-I','-c','import sys;sys.stdout.write("x"*100000);sys.stderr.write("y"*100000)']))
    assert len(result['stdout'])==len(result['stderr'])==256 and result['stdout_bytes']==100000
    assert result['stdout_sha256']==hashlib.sha256(b'x'*100000).hexdigest()

@pytest.mark.parametrize('stop_after',['timeout','stop'])
def test_running_process_is_terminated(tmp_path,stop_after):
    r,events,stop,_=runner(tmp_path,timeout=.15 if stop_after=='timeout' else 10);r.create_plan(['code.py'])
    async def run():
        task=asyncio.create_task(r.process([sys.executable,'-I','-c','import time;time.sleep(20)']))
        if stop_after=='stop':await asyncio.sleep(.15);stop.set()
        return await task
    result=asyncio.run(run());assert result['status']==('TIMEOUT' if stop_after=='timeout' else 'ABORTED') and result['exit_code'] is not None

def test_no_new_process_after_stop(tmp_path):
    r,_,stop,_=runner(tmp_path);r.create_plan(['code.py']);stop.set()
    with pytest.raises(asyncio.CancelledError):asyncio.run(r.process([sys.executable,'-c','raise Exception()']))

@pytest.mark.parametrize('poststatus',['PASS','FAIL'])
def test_godot_adapter_mock_baseline_and_post(tmp_path,poststatus):
    (tmp_path/'code.gd').write_text('extends Node')
    r,*_=runner(tmp_path);r.godot=sys.executable;commands=[]
    async def mock(args):
        commands.append(args);bad=len(commands)>1 and poststatus=='FAIL'
        return dict(status='COMPLETED',exit_code=1 if bad else 0,stdout='',stderr='SCRIPT ERROR: Parse Error' if bad else '')
    r.process=mock
    async def run():await r.prepare(['code.gd']);return await r.post(['code.gd'])
    result=asyncio.run(run());assert result.baseline_status=='PASS' and result.verification_status==poststatus
    assert result.task_signal==('VERIFIED_SUCCESS' if poststatus=='PASS' else 'VERIFIED_FAILURE')
    assert all('--check-only' in a and '--script' in a for a in commands)

def test_commands_cwd_and_scope_not_model_supplied(tmp_path):
    files(tmp_path);r,*_=runner(tmp_path);r.create_plan(['inventory.py']);r.plan=replace(r.plan,workspace=str(tmp_path.parent))
    with pytest.raises(PermissionError):asyncio.run(r.process([sys.executable,'-c','pass']))
    with pytest.raises(PermissionError):r.create_plan(['../bad.py'])
    assert 'shell=True' not in Path(__import__('alienape.verification',fromlist=['x']).__file__).read_text()
    assert not asyncio.run(PermissionManager().request('run_command',{},tmp_path,lambda *a:asyncio.sleep(0,result='once')))

def test_off_no_verifiers(tmp_path):
    r,*_=runner(tmp_path);r.config.mode='OFF'
    asyncio.run(r.prepare(['code.py']));assert r.plan is None

def test_changed_test_files_are_never_chosen_as_source_targets(tmp_path):
    root=tmp_path/'project';root.mkdir();files(root)
    a,b,store,events=agent(root,'def value(): return 1\n# refactored\n')
    asyncio.run(a.run('Refactor the implementation. Do not change the tests.'))
    assert a.write_scope.allowed_paths==['inventory.py'] and a.verification.result.task_signal=='VERIFIED_SUCCESS'

def test_ui_shows_verification_and_execution_uses_existing_permissions(tmp_path):
    from test_gui import app
    from alienape.dialogs import PermissionDialog
    qt=app.__wrapped__()
    dialog=PermissionDialog({'name':'verify_python_tests','arguments':{},'path':str(tmp_path)})
    assert 'Verifikation' in dialog.windowTitle();dialog.close()

def test_model_has_no_verifier_or_shell_tool(tmp_path):
    from alienape.tools import ToolRegistry
    names=set(ToolRegistry(Workspace(tmp_path)).tools)
    assert not names & {'verify_python_tests','verify_godot','run_command','run_python'}

def test_format_failure_new_file_never_starts_verifier(tmp_path):
    root=tmp_path/'project';root.mkdir();a,b,_,events=agent(root,'x=1')
    a.registry.profiles['coder'].tool_mode='none';b.responses=iter([response('not a file proposal')])
    with pytest.raises(CompletionRejected):asyncio.run(a.run('Create module.py implementing a constant VALUE=1.'))
    assert not any(k in {'VERIFICATION_BASELINE','VERIFICATION_COMPLETED'} for k,d in events)

def test_information_missing_no_verifier(tmp_path):
    root=tmp_path/'project';root.mkdir();a,b,_,events=agent(root,'x=1')
    with pytest.raises(CompletionRejected):asyncio.run(a.run('The tests are failing. Find the cause and fix the implementation.'))
    assert not b.requests and not any(k.startswith('VERIFICATION_') for k,d in events)
