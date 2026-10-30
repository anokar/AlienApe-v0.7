"""Concrete v0.6.6.1 regressions, isolated data and mock HTTP only."""
import asyncio,copy,json,hashlib
import httpx,pytest
from alienape.agent import Agent,Session,ToolDenied
from alienape.backends import MockBackend,OllamaBackend,LMStudioBackend,tool_reply
from alienape.config import Settings
from alienape.intent_resolution import DeterministicIntentResolver,IntentID as I
from alienape.identity import IDENTITY,identity_system
from alienape.tools import Workspace
from alienape.completion import CompletionRejected,FailureType as F
from alienape.experience import ExperienceStore
from test_workspace_discovery import make
from test_completion_recovery import response


@pytest.mark.parametrize('task,target,payload',[
 ('erstelle hello.txt mit "Hallo Welt"','hello.txt','Hallo Welt'),
 ('erstelle hello.txt mit dem Text "Hallo Welt"','hello.txt','Hallo Welt'),
 ('mach eine text datei namens hello.txt mit dem satz "hello world" drin','hello.txt','hello world'),
 ('erstelle eine text datei document.txt mit dem text "erinere dich an 12.12 :P"','document.txt','erinere dich an 12.12 :P'),
 ('write hello.txt containing "Hello World"','hello.txt','Hello World'),
 ('erstelle hello.txt mit dem Inhalt \' Hallo Welt  \'','hello.txt',' Hallo Welt  '),
 ('create hello.txt with the text "line one\n  line two"','hello.txt','line one\n  line two'),
 ('erstelle hello.txt mit „Hallo Welt“','hello.txt','Hallo Welt'),
 ('erstelle hello.txt mit "mit dem Text drin"','hello.txt','mit dem Text drin'),
 ('erstelle hello.txt mit "notes.py"','hello.txt','notes.py'),
 ('erstelle eine textdatei document.txt mit dem Text "notes.py"','document.txt','notes.py'),
 ('erstelle hello.txt mit "write only other.py"','hello.txt','write only other.py'),
 ('erstelle hello.txt mit "../../outside.py"','hello.txt','../../outside.py'),
 ('erstelle "some with text.txt" mit "Hello"','some with text.txt','Hello'),
])
def test_exact_payload_fast_write(tmp_path,task,target,payload):
 approvals=[]
 async def yes(tool,args,path):approvals.append((tool,args['path']));return True
 a,b=make(tmp_path);a.permission=yes;asyncio.run(a.run(task))
 assert (tmp_path/target).read_text(encoding='utf-8')==payload
 assert [p.name for p in tmp_path.iterdir()]==[target]
 assert approvals==[('write_file',target)] and not b.requests
 assert a.resources.counters.model_calls==a.resources.counters.input_tokens_estimate==a.resources.counters.output_tokens_estimate==0
 assert a.resources.counters.file_writes==1


@pytest.mark.parametrize('task',[
 'erstelle hello.txt oder other.txt mit "Hallo"',
 'erstelle eine textdatei namens',
 'erstelle eine textdatei namens foo oder bar mit "Hello"',
 'mach eine text datei namens hello.txt mit dem satz "hello world* driin',
 'erstelle eine textdatei irgendeinname mit "Hallo"',
 'erstelle einen ordner',
 'erstelle ordner und daten',
 'erstelle ordner und',
 'erstelle hello.txt im ordner other mit "Hallo"',
])
def test_ambiguous_create_never_writes(tmp_path,task):
 store=ExperienceStore(tmp_path/'exp.db');digest=hashlib.sha256(store.database.read_bytes()).hexdigest()
 a,b=make(tmp_path,store=store);answer=asyncio.run(a.run(task))
 assert a.intent_resolution.ambiguity_reason and answer
 assert a.resources.counters.file_writes==a.resources.counters.tool_calls==a.resources.counters.model_calls==0
 assert not b.requests and not [p for p in tmp_path.iterdir() if p.suffix!='.db']
 assert hashlib.sha256(store.database.read_bytes()).hexdigest()==digest


@pytest.mark.parametrize('task',[
 'erstelle ein leeres godot project mit ordner und daten',
 'erstele ein leres godot project mit ordner und daten',
 'erstelle ein godot project vorlage',
 'kanst du mir ein leres godot project erstelen? du hast die tools da zu und die berechtigung auch',
])
def test_project_create_is_precise_unsupported(tmp_path,task):
 a,b=make(tmp_path);answer=asyncio.run(a.run(task))
 assert a.intent_resolution.intent_id==I.CREATE_PROJECT and 'nicht implementiert' in answer
 assert 'nichts erstellt' in answer and not (tmp_path/'und').exists()
 assert not list(tmp_path.iterdir()) and not b.requests


def test_project_words_inside_payload_are_not_project_request(tmp_path):
 r=DeterministicIntentResolver().resolve('erstelle hello.txt mit "ein godot project mit ordner und daten"')
 assert r.intent_id==I.CREATE_FILE and r.literal_content=='ein godot project mit ordner und daten'


def test_actual_literal_output_traversal_is_still_blocked(tmp_path):
 async def yes(*_):raise AssertionError('Invalid target must fail before permission')
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=yes)
 with pytest.raises(PermissionError):asyncio.run(a.run('erstelle ../../outside.txt mit "Hallo"'))
 assert not a.resources.counters.model_calls and not a.resources.counters.file_writes


def test_explicit_directory_name_is_not_mistaken_for_a_file(tmp_path):
 async def yes(*_):return True
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=yes)
 asyncio.run(a.run('erstelle ordner example.py'))
 assert a.intent_resolution.intent_id==I.CREATE_DIRECTORY and (tmp_path/'example.py').is_dir()


def test_uncertain_payload_falls_back_to_create_contract(tmp_path):
 a,b=make(tmp_path,responses=[response(json.dumps({'content':'generated'}))])
 async def yes(*_):return True
 a.permission=yes;asyncio.run(a.run('erstelle hello.txt mit einem generierten Gedicht'))
 assert a.intent_resolution.literal_content is None and len(b.requests)==1
 assert a.write_scope.allowed_paths==['hello.txt'] and (tmp_path/'hello.txt').read_text()=='generated'


def repeated(call,n):
 calls=[]
 for i in range(n):
  item=copy.deepcopy(call['tool_calls'][0]);item['id']='call_'+str(i);calls.append(item)
 return response('',calls=calls)


def test_failed_duplicate_batch_stops_before_eight_requests(tmp_path):
 calls=repeated(tool_reply('unknown_tool',{'b':2,'a':1}),8)
 calls['tool_calls'][1]['function']['arguments']='{ "a": 1, "b": 2 }'
 b=MockBackend([calls],delay=0);events=[]
 a=Agent(b,Workspace(tmp_path),Settings(),observe=lambda k,d,*_:events.append((k,d)))
 with pytest.raises(CompletionRejected) as error:asyncio.run(a.run('Nutze Tools im Workspace'))
 assert error.value.failure_type==F.TOOL_FAILURE and 'NO_PROGRESS' in str(error.value)
 assert a.last_failure_type==F.TOOL_FAILURE
 assert a.resources.counters.tool_calls==2 and a.resources.counters.model_calls==1
 assert any(k=='FAILURE_CLASSIFIED' and d.get('subreason')=='NO_PROGRESS' for k,d in events)


def test_failed_read_duplicates_are_not_contract_or_model_success(tmp_path):
 events=[];a,b=make(tmp_path,'native',[repeated(tool_reply('read_file',{'path':'missing.py'}),6)],events=events)
 with pytest.raises(CompletionRejected) as error:asyncio.run(a.run('Lese Dateien mit Tools im Workspace'))
 assert error.value.failure_type==F.TOOL_FAILURE and a.resources.counters.file_writes==0
 assert sum(k=='TOOL_REQUEST' for k,d in events)==2


@pytest.mark.parametrize('n',[2,8])
def test_successful_create_terminates_after_one_write(tmp_path,n):
 events=[];backend=MockBackend([repeated(tool_reply('write_file',{'path':'hello.py','content':'print("Hello World")\n'}),n)],delay=0)
 async def yes(*_):return True
 a=Agent(backend,Workspace(tmp_path),Settings(),permission=yes,observe=lambda k,d,*_:events.append((k,d)))
 answer=asyncio.run(a.run('erstelle hello.py mit einem hello world programm'))
 assert (tmp_path/'hello.py').read_text()=='print("Hello World")\n'
 assert a.resources.counters.file_writes==a.resources.counters.tool_calls==a.resources.counters.model_calls==1
 assert 'Datei erstellt' in answer and a.verification.result.verification_status=='PASS'
 assert any(k=='CREATE_TOOL_COMPLETED' and d['remaining_batch_calls_skipped']==n-1 for k,d in events)


def test_successful_create_does_not_ask_model_for_duplicate_next_step(tmp_path):
 backend=MockBackend([tool_reply('write_file',{'path':'hello.py','content':'print(1)'}),tool_reply('write_file',{'path':'hello.py','content':'print(2)'})],delay=0)
 async def yes(*_):return True
 a=Agent(backend,Workspace(tmp_path),Settings(),permission=yes);asyncio.run(a.run('erstelle hello.py'))
 assert (tmp_path/'hello.py').read_text()=='print(1)' and len(backend.calls)==1


def test_max_eight_protection_is_unchanged(tmp_path):
 backend=MockBackend([repeated(tool_reply('write_file',{'path':'hello.py','content':'print(1)'}),9)],delay=0)
 a=Agent(backend,Workspace(tmp_path),Settings())
 with pytest.raises(CompletionRejected,match='maximal 8'):asyncio.run(a.run('erstelle hello.py'))
 assert a.last_failure_type==F.TOOL_FAILURE
 assert a.resources.counters.tool_calls==a.resources.counters.file_writes==0


def test_denied_success_path_never_terminates_as_created(tmp_path):
 async def no(*_):return False
 a=Agent(MockBackend([tool_reply('write_file',{'path':'hello.py','content':'print(1)'})],delay=0),Workspace(tmp_path),Settings(),permission=no)
 answer=asyncio.run(a.run('erstelle hello.py'))
 assert 'abgelehnt' in answer and not (tmp_path/'hello.py').exists()
 assert a.resources.counters.file_writes==0


def test_accounting_counts_request_before_permission(tmp_path):
 seen=[]
 async def permission(*_):seen.append((a.resources.counters.tool_calls,a.resources.counters.file_writes));return False
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=permission)
 with pytest.raises(ToolDenied):asyncio.run(a.run('erstelle hello.txt mit "Hello"'))
 assert seen==[(1,0)] and a.resources.counters.file_writes==0


@pytest.mark.parametrize('factory',[OllamaBackend,LMStudioBackend])
def test_ordinary_no_tools_http_keeps_identity_and_actual_task(factory):
 captured=[]
 def handler(request):
  body=json.loads(request.content);captured.append(body)
  return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'Ich bin AlienApe.'}}]})
 backend=factory(transport=httpx.MockTransport(handler))
 messages=[{'role':'system','content':identity_system('KNOWLEDGE_TASK: explain directly.')},{'role':'user','content':'erkläre mir wie man hello world in python schreibt'}]
 result=asyncio.run(backend.chat(messages,tools=[],model='test'))
 assert result['content']=='Ich bin AlienApe.' and captured[0]['messages']==messages
 assert 'freundlicher Chatassistent' not in json.dumps(captured[0])
 assert IDENTITY in captured[0]['messages'][0]['content']


@pytest.mark.parametrize('mode',['none','native','json_fallback'])
def test_knowledge_path_has_no_file_requirements_and_http_context(tmp_path,mode):
 captured=[]
 def handler(request):
  body=json.loads(request.content);captured.append(body)
  text=json.dumps({'answer':'print("Hello World")'}) if body.get('response_format') else 'print("Hello World")'
  return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':text}}]})
 from test_routed_agent import Pool
 a,_=make(tmp_path,mode);backend=OllamaBackend(transport=httpx.MockTransport(handler));a.pool=Pool({'test':backend})
 answer=asyncio.run(a.run('erkläre mir wie man hello world in python schreibt'))
 assert 'print(' in answer and a.intent_resolution.intent_id==I.EXPLAIN_CODE
 assert not a.required_input_paths and a.write_scope.read_only and not a.response_contract.expects_file_proposal
 assert a.resources.counters.file_reads==a.resources.counters.file_writes==a.resources.counters.tool_calls==0
 assert len(captured)==1 and any(m['role']=='system' and IDENTITY in m['content'] for m in captured[0]['messages'])
 assert 'KNOWLEDGE_TASK' in json.dumps(captured[0])


@pytest.mark.parametrize('question',['bei was?','warum?','wieso nicht?','what do you mean?','why?'])
def test_one_step_failure_reason_is_retained(tmp_path,question):
 session=Session()
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session)
 asyncio.run(a.run('erstelle ein godot project vorlage'))
 b=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session)
 answer=asyncio.run(b.run(question))
 assert 'godot project' in answer and 'nicht implementiert' in answer
 assert b.resources.counters.model_calls==b.resources.counters.tool_calls==0
 c=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session)
 assert 'konkretisieren' in asyncio.run(c.run(question))


def test_reason_is_workspace_bound_and_cleared_by_intervening_turn(tmp_path):
 one=tmp_path/'one';two=tmp_path/'two';one.mkdir();two.mkdir();session=Session()
 def agent(folder):return Agent(MockBackend(delay=0),Workspace(folder),Settings(),session=session)
 asyncio.run(agent(one).run('erstelle ein godot project vorlage'))
 assert 'konkretisieren' in asyncio.run(agent(two).run('bei was?'))
 asyncio.run(agent(one).run('erstelle ein godot project vorlage'));asyncio.run(agent(one).run('hallo'))
 assert 'konkretisieren' in asyncio.run(agent(one).run('why?'))
