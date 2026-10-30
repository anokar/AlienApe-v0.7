import asyncio,json
from dataclasses import replace
import pytest
from alienape.intent_resolution import DeterministicIntentResolver,IntentResolution,IntentID as I
from alienape.agent import Agent,Session,ToolDenied
from alienape.backends import MockBackend,tool_reply
from alienape.tools import Workspace
from alienape.config import Settings
from alienape.completion import CompletionRejected
from test_workspace_discovery import make
from test_completion_recovery import response

@pytest.mark.parametrize('task,intent',[
 ('erstelle eine textdatei hallo.txt mit hallo welt',I.CREATE_FILE),
 ('mach ein python programm als hallowelt.py',I.CREATE_FILE),
 ('kanst du mir ein python program machen mit der hallo welt funktion alls hallowelt.py',I.CREATE_FILE),
 ('erstele eine text datei im ordner hallo.txt mit einem generierten Text',I.CREATE_FILE),
 ('auch gut. kanst du mir eine text datei erstelen mit inhalt. hallo welt hier ist der alien affe.',I.CREATE_FILE),
 ('prüfe scripts/pvp_rules.gd auf bugs',I.VERIFY_FILE),
 ('Lies scripts/pvp_rules.gd und prüfe es',I.VERIFY_FILE),
 ('wie schreibe ich ein hallo welt in python?',I.EXPLAIN_CODE),
 ('erklär mir ein python hallo welt programm',I.EXPLAIN_CODE),
 ('was kannst du alles machen?',I.CAPABILITY_QUERY)])
def test_resolved_real_prompts(task,intent):assert DeterministicIntentResolver().resolve(task).intent_id==intent

@pytest.mark.parametrize('task,target',[('erstelle eine textdatei hallo.txt mit hallo welt','hallo.txt'),('mach ein python programm als hallowelt.py','hallowelt.py'),('erstele eine text datei im ordner hallo.txt mit einem generierten Text','hallo.txt')])
def test_native_create_has_no_preexisting_input(tmp_path,task,target):
    b=MockBackend([tool_reply('write_file',{'path':target,'content':'hello\n'}),{'role':'assistant','content':'written'}],delay=0)
    async def yes(*_):return True
    a=Agent(b,Workspace(tmp_path),Settings(),permission=yes)
    asyncio.run(a.run(task))
    expected='hallo welt' if 'mit hallo welt' in task else 'hello\n'
    assert (tmp_path/target).read_text()==expected and not a.required_input_paths

def test_none_create_scoped_proposal(tmp_path):
    a,b=make(tmp_path,responses=[response(json.dumps({'content':'hello'}))])
    async def yes(*_):return True
    a.permission=yes;asyncio.run(a.run('erstelle eine textdatei hallo.txt mit einem generierten Text'))
    assert (tmp_path/'hallo.txt').read_text()=='hello' and a.information_complete

def test_create_plain_answer_is_not_file_success(tmp_path):
    b=MockBackend([{'role':'assistant','content':'hello world'}],delay=0);a=Agent(b,Workspace(tmp_path),Settings())
    with pytest.raises(CompletionRejected):asyncio.run(a.run('erstelle eine textdatei hallo.txt mit einem generierten Text'))
    assert not (tmp_path/'hallo.txt').exists()

@pytest.mark.parametrize('task',['prüfe scripts/pvp_rules.py auf bugs','Lies scripts/pvp_rules.py und prüfe es'])
def test_verify_explicit_file_read_and_ast(tmp_path,task):
    (tmp_path/'scripts').mkdir();(tmp_path/'scripts/pvp_rules.py').write_text('x = 1\n')
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());answer=asyncio.run(a.run(task))
    assert 'PASS' in answer and a.resources.counters.file_reads==1 and a.resources.counters.model_calls==0

@pytest.mark.parametrize('follow',['prüfe es','erklär mir das','help me with it'])
def test_single_reference(tmp_path,follow):
    (tmp_path/'foo.py').write_text('x = 1\n');s=Session()
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=s);asyncio.run(a.run('Lies foo.py'))
    b=Agent(MockBackend([{'role':'assistant','content':'x has value one'}],delay=0),Workspace(tmp_path),Settings(),session=s)
    result=asyncio.run(b.run(follow));assert b.intent_resolution.referenced_paths==('foo.py',)
    assert b.required_input_paths==['foo.py']

def test_multiple_reference_clarifies_no_write(tmp_path):
    for name in ['a.py','b.py']:(tmp_path/name).write_text('x=1\n')
    s=Session();s.task_state.commit(Workspace(tmp_path),'task','read',['a.py','b.py'],[],[],{})
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=s)
    answer=asyncio.run(a.run('ändere es'));assert 'Mehrere' in answer and not a.resources.counters.file_writes and not a.resources.counters.model_calls

def test_create_then_explain_uses_output_reference(tmp_path):
    s=Session()
    async def yes(*_):return True
    a=Agent(MockBackend([tool_reply('write_file',{'path':'hello.py','content':'print(1)\n'}),{'role':'assistant','content':'done'}],delay=0),Workspace(tmp_path),Settings(),session=s,permission=yes)
    asyncio.run(a.run('erstelle hello.py'))
    b=Agent(MockBackend([{'role':'assistant','content':'prints one'}],delay=0),Workspace(tmp_path),Settings(),session=s)
    asyncio.run(b.run('erklär mir das'));assert b.required_input_paths==['hello.py']

def test_knowledge_has_no_workspace_tools_or_validator(tmp_path):
    b=MockBackend([{'role':'assistant','content':'print("Hallo Welt")'}],delay=0);a=Agent(b,Workspace(tmp_path),Settings())
    answer=asyncio.run(a.run('wie schreibe ich hallo welt in python?'))
    assert 'print' in answer and not a.required_input_paths and a.workspace_discovery is None and a.write_scope.read_only
    assert not a.active_tool_names
    from alienape.routing import classify_task,TaskType
    assert classify_task(a.scope_task)[0]==TaskType.GENERAL_TASK

def test_capabilities_are_controller_grounded(tmp_path):
    s=Settings();s.permissions['write_file']='block'
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),s);answer=asyncio.run(a.run('was kannst du alles machen?'))
    assert 'block' in answer and 'Kein Löschtool' in answer and not a.resources.counters.model_calls

def test_create_directory_denied_and_allowed(tmp_path):
    async def no(*_):return False
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=no)
    with pytest.raises(ToolDenied):asyncio.run(a.run('erstelle ordner hello'))
    assert not (tmp_path/'hello').exists()
    async def yes(*_):return True
    b=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=yes)
    asyncio.run(b.run('erstelle ordner hello'));assert (tmp_path/'hello').is_dir() and not b.resources.counters.model_calls

def test_modify_missing_file_is_not_create(tmp_path):
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    with pytest.raises(CompletionRejected):asyncio.run(a.run('ändere absent.py'))
    assert not (tmp_path/'absent.py').exists() and not a.resources.counters.model_calls

def test_create_existing_target_is_not_overwrite(tmp_path):
    (tmp_path/'hello.py').write_text('old')
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    with pytest.raises(CompletionRejected):asyncio.run(a.run('erstelle hello.py'))
    assert (tmp_path/'hello.py').read_text()=='old'

def test_find_read_is_bounded_controller_action(tmp_path):
    (tmp_path/'scripts').mkdir();(tmp_path/'scripts/foo.py').write_text('sentinel')
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    answer=asyncio.run(a.run('find foo.py and read it'))
    assert 'sentinel' in answer and a.intent_resolution.referenced_paths==('scripts/foo.py',) and not a.resources.counters.model_calls

def test_find_modify_keeps_exact_found_scope(tmp_path):
    (tmp_path/'scripts').mkdir();(tmp_path/'scripts/foo.py').write_text('x=1\n')
    b=MockBackend([tool_reply('write_file',{'path':'scripts/foo.py','content':'x=2\n'}),{'role':'assistant','content':'done'}],delay=0)
    async def yes(*_):return True
    a=Agent(b,Workspace(tmp_path),Settings(),permission=yes)
    asyncio.run(a.run('find foo.py and modify it to x=2'))
    assert a.write_scope.allowed_paths==['scripts/foo.py'] and (tmp_path/'scripts/foo.py').read_text()=='x=2\n'

def test_explicit_analysis_receives_files_before_model(tmp_path):
    (tmp_path/'foo.py').write_text('ANALYSIS_SENTINEL=1\n');b=MockBackend([{'role':'assistant','content':'explained'}],delay=0)
    a=Agent(b,Workspace(tmp_path),Settings());asyncio.run(a.run('analysiere foo.py'))
    assert 'ANALYSIS_SENTINEL' in json.dumps(b.calls) and a.information_complete

def test_new_intent_does_not_relax_secret_boundary(tmp_path):
    a=Agent(MockBackend([tool_reply('write_file',{'path':'.env','content':'blocked'})],delay=0),Workspace(tmp_path),Settings())
    with pytest.raises((PermissionError,ToolDenied)):asyncio.run(a.run('erstelle textdatei .env mit einem generierten Text'))
    assert not (tmp_path/'.env').exists()

def test_none_create_bad_format_keeps_contract_failure(tmp_path):
    a,b=make(tmp_path,responses=[response('hello world')])
    with pytest.raises(CompletionRejected) as error:asyncio.run(a.run('erstelle textdatei hallo.txt mit einem generierten Text'))
    assert str(error.value.failure_type)=='FORMAT_FAILURE' and len(b.requests)==1

def test_explicit_literal_text_create_no_model_and_permission_required(tmp_path):
    async def yes(*_):return True
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=yes)
    asyncio.run(a.run('erstelle eine textdatei hallo.txt mit hallo welt'))
    assert (tmp_path/'hallo.txt').read_text()=='hallo welt' and a.resources.counters.model_calls==0
    async def no(*_):return False
    b=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=no)
    with pytest.raises(ToolDenied):asyncio.run(b.run('erstelle eine textdatei other.txt mit explicit text'))
    assert not (tmp_path/'other.txt').exists()

def test_pronoun_does_not_cross_workspace(tmp_path):
    p=tmp_path/'one';q=tmp_path/'two';p.mkdir();q.mkdir();(p/'foo.py').write_text('x=1')
    session=Session();a=Agent(MockBackend(delay=0),Workspace(p),Settings(),session=session);asyncio.run(a.run('Lies foo.py'))
    b=Agent(MockBackend(delay=0),Workspace(q),Settings(),session=session);answer=asyncio.run(b.run('prüfe es'))
    assert 'Keine eindeutige' in answer and not b.resources.counters.model_calls

def test_resolution_seam_does_not_depend_on_task_words(tmp_path):
    class Resolver:
        def resolve(self,task):return IntentResolution(I.CREATE_FILE,1.0,'test-seam',('new.txt',),True,'files',handled=True,output_paths=('new.txt',))
    async def yes(*_):return True
    a=Agent(MockBackend([tool_reply('write_file',{'path':'new.txt','content':'hello'}),{'role':'assistant','content':'done'}],delay=0),Workspace(tmp_path),Settings(),permission=yes,intent_resolver=Resolver())
    asyncio.run(a.run('opaque user language'));assert (tmp_path/'new.txt').read_text()=='hello'
