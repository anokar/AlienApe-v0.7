"""Real user regressions; isolated workspaces, no productive model evidence."""
import asyncio,json,hashlib
from dataclasses import asdict
import pytest
from alienape.agent import Agent,Session,ToolDenied
from alienape.backends import MockBackend,tool_reply
from alienape.config import Settings
from alienape.intent_resolution import DeterministicIntentResolver,IntentID as I
from alienape.identity import IDENTITY
from alienape.tools import Workspace
from alienape.experience import ExperienceStore
from alienape.completion import CompletionRejected
from alienape.routing import TaskType,classify_task
from test_workspace_discovery import make
from test_completion_recovery import response


@pytest.mark.parametrize('task,intent',[
 ('hallo wie gehts :P',I.GREETING),('hi',I.GREETING),('guten morgen',I.GREETING),
 ('thx',I.THANKS),('thank you',I.THANKS),('tschüss',I.GOODBYE),('bye',I.GOODBYE),
 ('wer bist du?',I.IDENTITY_QUERY),('who are you?',I.IDENTITY_QUERY),
 ('auch gut. Kanst du eine hallo.txt anlegen mit ( Hallo welt hier kommen die aliens)',I.CREATE_FILE),
 ('erstele eine text date hallowelt.txt mit dem inhat hallo welt',I.CREATE_FILE),
 ('hallo kanst du mir ein python datei erstelen mit dem hallo welt program? in hallo.py erstelen',I.CREATE_FILE),
 ('mach eine leere textdatei hallo.txt',I.CREATE_FILE),
 ('mach ein python programm als hallo.py',I.CREATE_FILE),
 ('wie programiere ich hallo welt in python',I.EXPLAIN_CODE),
 ('schreib mir hallo welt in python',I.EXPLAIN_CODE),
 ('es ist eingestelt das du berechtigung hast',I.CAPABILITY_QUERY),
 ('can you use tools?',I.CAPABILITY_QUERY),
])
def test_real_requests_resolve(task,intent):assert DeterministicIntentResolver().resolve(task).intent_id==intent


@pytest.mark.parametrize('task',['hallo','hallo wie gehts :P','danke','thx','bye','wer bist du?','who are you?','was kannst du alles machen?'])
@pytest.mark.parametrize('mode',['native','none'])
def test_direct_response_never_wakes_backend_or_experience(tmp_path,task,mode):
 store=ExperienceStore(tmp_path/'experience.db');before=hashlib.sha256(store.database.read_bytes()).hexdigest()
 events=[];a,b=make(tmp_path,mode,events=events,store=store)
 # An accidental backend lookup, identity probe or model registration must fail this test.
 def unexpected(*_):raise AssertionError('L0 must not touch model pool')
 a.pool.get=unexpected
 answer=asyncio.run(a.run(task));snapshot=a.resources.snapshot()
 assert answer and not b.requests and a.current_profile is None and not a.fingerprints
 assert all(snapshot[k]==0 for k in ['model_calls','input_tokens_estimate','output_tokens_estimate','file_reads','file_writes','tool_calls'])
 assert not a.resources.per_model and not a.experience_ids
 assert hashlib.sha256(store.database.read_bytes()).hexdigest()==before
 direct=[d for k,d in events if k=='DIRECT_RESPONSE'];assert len(direct)==1
 assert direct[0]['model_calls']==0 and direct[0]['model_latency'] is None
 assert not any(k in {'MODEL_CALL','MODEL_ROUTED','MODEL_RESPONSE','EXPERIENCE_RECORDED'} for k,d in events)
 if 'bist' in task or task=='who are you?':assert 'AlienApe' in answer and 'Chatassistent' not in answer


@pytest.mark.parametrize('task',['hallo, lies foo.py','danke, erstelle hallo.txt','hi please create hello.py','hello but what is recursion?','wer bist du und ändere foo.py','hi there this is ambiguous'])
def test_l0_does_not_swallow_combined_or_unknown_requests(task):
 r=DeterministicIntentResolver().resolve(task)
 assert r.intent_id not in {I.GREETING,I.THANKS,I.GOODBYE,I.IDENTITY_QUERY}


@pytest.mark.parametrize('task,name,expected',[
 ('auch gut. Kanst du eine hallo.txt anlegen mit ( Hallo welt hier kommen die aliens)','hallo.txt','Hallo welt hier kommen die aliens'),
 ('erstele eine text date hallowelt.txt mit dem inhat hallo welt','hallowelt.txt','hallo welt'),
 ('mach eine leere textdatei hallo.txt','hallo.txt',''),
 ('mach eine lere text datei namens hallo.txt','hallo.txt',''),
 ('erstelle eine textdatei hallo.txt mit hallo welt','hallo.txt','hallo welt'),
])
def test_literal_and_empty_creates_use_real_permissions(tmp_path,task,name,expected):
 approvals=[]
 async def yes(tool,args,path):approvals.append((tool,args['path']));return True
 a,b=make(tmp_path);a.permission=yes
 asyncio.run(a.run(task))
 assert a.intent_resolution.intent_id==I.CREATE_FILE and (tmp_path/name).read_text()==expected
 assert not a.required_input_paths and not b.requests and a.resources.counters.model_calls==0
 assert approvals==[('write_file',name)] and a.resources.counters.file_writes==1


@pytest.mark.parametrize('task',['erstele eine text datei im ordner hallo.txt mit einem generierten Text','erstelle hallo.txt mit einem generierten Text','mach ein python programm als hallo.py','hallo kanst du mir ein python datei erstelen mit dem hallo welt program? in hallo.py erstelen'])
@pytest.mark.parametrize('mode',['none','native'])
def test_generated_create_new_target_real_write(tmp_path,task,mode):
 name='hallo.py' if 'python' in task else 'hallo.txt';content='print("Hallo Welt")\n'
 responses=[response(json.dumps({'content':content}))] if mode=='none' else [response('',calls=tool_reply('write_file',{'path':name,'content':content})['tool_calls']),response('done')]
 a,b=make(tmp_path,mode,responses)
 async def yes(*_):return True
 a.permission=yes;asyncio.run(a.run(task))
 assert (tmp_path/name).read_text()==content and not a.required_input_paths
 assert a.write_scope.allowed_paths==[name] and a.information_complete


@pytest.mark.parametrize('task',['wie programmiere ich hallo welt in python?','wie programiere ich hallo welt in python','schreib mir hallo welt in python','erklär mir ein python hallo welt programm'])
@pytest.mark.parametrize('mode',['none','native'])
def test_knowledge_gets_identity_and_no_file_contract(tmp_path,task,mode):
 a,b=make(tmp_path,mode,[response('```python\nprint("Hallo Welt")\n```')]);answer=asyncio.run(a.run(task))
 assert 'print(' in answer and a.intent_resolution.intent_id==I.EXPLAIN_CODE
 assert classify_task(a.scope_task)[0]==TaskType.GENERAL_TASK
 assert a.write_scope.read_only and not a.response_contract.expects_file_proposal
 assert not a.required_input_paths and not a.workspace_discovery
 assert a.resources.counters.model_calls==1 and not a.resources.counters.tool_calls
 payload=json.dumps(b.requests,ensure_ascii=False)
 assert IDENTITY.strip() in payload.replace('\\n','\n') and 'CONTROLLER_CAPABILITIES' in payload
 assert 'KNOWLEDGE_TASK' in payload and not list(tmp_path.glob('*.py'))


def test_standalone_model_identity_not_only_routed(tmp_path):
 backend=MockBackend([{'role':'assistant','content':'print("Hallo Welt")'}],delay=0)
 a=Agent(backend,Workspace(tmp_path),Settings());asyncio.run(a.run('schreib mir hallo welt in python'))
 assert backend.calls[0][0]['content'].startswith(IDENTITY)
 assert 'CONTROLLER_CAPABILITIES' in json.dumps(backend.calls)


def test_non_l0_acknowledgement_short_model_path_has_identity(tmp_path):
 a,b=make(tmp_path,responses=[response('AlienApe hilft gern.')]);asyncio.run(a.run('ah cool'))
 assert b.requests[0]['messages'][0]['role']=='system'
 assert b.requests[0]['messages'][0]['content'].startswith(IDENTITY)


def test_permission_statement_is_not_grant_or_resume(tmp_path):
 s=Settings();s.permissions['write_file']='block';before=asdict(s);session=Session()
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),s,session=session)
 answer=asyncio.run(a.run('es ist eingestelt das du berechtigung hast'))
 assert 'block (gesperrt)' in answer and 'ändert keine Freigabe' in answer
 assert asdict(s)==before and not a.resources.counters.model_calls and not a.resources.counters.file_writes
 assert not a.stopped.is_set() and not list(tmp_path.iterdir())


def test_capability_inventory_does_not_invent_removed_tools(tmp_path):
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
 for name in ['write_file','make_directory','read_file']:a.tools.tools.pop(name)
 answer=asyncio.run(a.run('was kannst du alles machen?'))
 assert 'Dateien erstellen/ändern: nicht verfügbar' in answer
 assert 'Dateien lesen: nicht verfügbar' in answer and 'Ordner auflisten: verfügbar' in answer
 assert 'Ordner erstellen: nicht verfügbar' in answer


def test_directory_unsupported_and_denied_are_precise(tmp_path):
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());a.tools.tools.pop('make_directory')
 answer=asyncio.run(a.run('erstelle ordner new'))
 assert 'kein Werkzeug' in answer and not (tmp_path/'new').exists()
 async def no(*_):return False
 b=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=no)
 with pytest.raises(ToolDenied,match='make_directory'):asyncio.run(b.run('erstelle ordner denied'))
 assert not b.resources.counters.file_writes and not (tmp_path/'denied').exists()


def test_social_and_permission_queries_preserve_recent_target(tmp_path):
 (tmp_path/'foo.gd').write_text('extends Node\n');session=Session()
 def agent():return Agent(MockBackend([{'role':'assistant','content':'extends Node'}],delay=0),Workspace(tmp_path),Settings(),session=session)
 asyncio.run(agent().run('Lies foo.gd'))
 for task in ['hallo','wer bist du?','es ist eingestelt das du berechtigung hast']:
  asyncio.run(agent().run(task));assert session.task_state.last_inspected_files==['foo.gd']
 a=agent();asyncio.run(a.run('prüfe es'));assert a.intent_resolution.referenced_paths==('foo.gd',)


@pytest.mark.parametrize('follow',['erklär mir das','explain the file','help me with that','hilf mir dabei','erklär mir das Script'])
def test_created_file_reference(tmp_path,follow):
 session=Session()
 async def yes(*_):return True
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session,permission=yes)
 asyncio.run(a.run('mach eine leere textdatei hello.txt'))
 b=Agent(MockBackend([{'role':'assistant','content':'empty'}],delay=0),Workspace(tmp_path),Settings(),session=session)
 asyncio.run(b.run(follow));assert b.intent_resolution.referenced_paths==('hello.txt',)


def test_normal_coding_router_and_compound_write_verify_unchanged(tmp_path):
 from test_routed_agent import setup,make_agent,answer
 proposal={'answer':'fixed','proposed_files':[{'path':'player.py','content':'def divide(a,b):\n    return a / b\n'}],'status':'ok'}
 s,registry,pool=setup(tmp_path,{'coder':[answer(json.dumps(proposal))]})
 async def yes(*_):return True
 a=make_agent(tmp_path,s,registry,pool,permission=yes)
 asyncio.run(a.run('modify player.py to use division and verify it'))
 assert a.current_profile.id=='coder' and a.resources.counters.file_writes==1
 assert a.verification.result.verification_status=='PASS'


def test_direct_reply_respects_stop_and_preserves_no_file_side_effects(tmp_path):
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());a.stopped.set()
 with pytest.raises(asyncio.CancelledError):asyncio.run(a.run('hallo'))
 assert not a.resources.counters.model_calls and not list(tmp_path.iterdir())


def test_reused_agent_l0_does_not_attach_cached_analysis_scope(tmp_path):
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
 class OldDiscovery:
  files=[{'path':'old.py'}]
 a.workspace_discovery=OldDiscovery()
 answer=asyncio.run(a.run('wer bist du?'))
 assert 'AlienApe' in answer and 'Analyseumfang' not in answer
