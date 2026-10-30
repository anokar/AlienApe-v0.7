import asyncio,json,threading
from pathlib import Path
import pytest
from alienape.agent import Agent
from alienape.completion import CompletionRejected,FailureType as F
from alienape.contracts import contract_for_task,ResponseMode
from alienape.config import Settings
from alienape.discovery import workspace_intent,DiscoveryLimits
from alienape.experience import ExperienceStore
from alienape.routed_agent import RoutedAgent
from alienape.routing import ModelRegistry,classify_task,TaskType
from alienape.taxonomy import task_category
from alienape.tools import Workspace,ToolRegistry
from test_routed_agent import Pool
from test_routing import profile
from test_completion_recovery import Script,response
from alienape.backends import tool_reply

LIST='List the files in the current project folder.'
ANALYSE='Analyse the current project folder. Inspect its files first.'

def make(tmp_path,mode='none',responses=None,*,events=None,store=None,standalone=False):
    settings=Settings();settings.models=[profile('test',tool_mode=mode)]
    backend=Script(responses or [response('REAL_PROJECT_SENTINEL uses Godot.')])
    observe=lambda kind,details,*rest:events.append((kind,details)) if events is not None else None
    if standalone:a=Agent(backend,Workspace(tmp_path),settings,observe=observe)
    else:a=RoutedAgent(Workspace(tmp_path),settings,registry=ModelRegistry(settings.models),pool=Pool({'test':backend}),observe=observe,experience=store)
    return a,backend

def project(tmp_path):
    (tmp_path/'project.godot').write_text('config/name="REAL_PROJECT_SENTINEL"\nrun/main_scene="res://scenes/world.tscn"\n',encoding='utf-8')
    (tmp_path/'README.md').write_text('REAL_PROJECT_SENTINEL is a Godot project.',encoding='utf-8')
    (tmp_path/'scripts').mkdir();(tmp_path/'scripts/world.gd').write_text('extends Node2D\n',encoding='utf-8')

@pytest.mark.parametrize('task,intent',[(LIST,'listing'),(ANALYSE,'analysis'),('Analysiere den aktuellen Projektordner.','analysis'),
    ('Liste Dateien im Workspace','listing'),('List files','listing'),('Analyse the file project.godot',None),
    ('Merke dir: Wir wollen das Projekt analysieren.',None),('List three advantages of Python',None)])
def test_workspace_intents(task,intent):assert workspace_intent(task)==intent

def test_plain_listing_contract_and_domain():
    assert contract_for_task(LIST).mode==ResponseMode.PLAIN_TEXT
    assert contract_for_task(LIST).validator_id is None
    assert classify_task(LIST)[0]==TaskType.FILE_TASK and str(task_category(ANALYSE))=='FILE_ANALYSIS'

@pytest.mark.parametrize('mode',['none','native'])
def test_real_listing_no_model_or_write(tmp_path,mode):
    project(tmp_path);events=[];a,b=make(tmp_path,mode,events=events)
    answer=asyncio.run(a.run(LIST))
    assert 'project.godot' in answer and 'README.md' in answer
    assert not b.requests and a.resources.counters.model_calls==0
    assert a.last_failure_type==F.SUCCESS
    assert [d['name'] for k,d in events if k=='TOOL_REQUEST']==['list_files']
    assert a.write_scope.read_only and a.resources.counters.file_writes==0

@pytest.mark.parametrize('mode',['none','native'])
def test_controller_gets_real_data_before_model(tmp_path,mode):
    project(tmp_path);events=[];a,b=make(tmp_path,mode,events=events)
    answer=asyncio.run(a.run(ANALYSE))
    assert 'REAL_PROJECT_SENTINEL' in json.dumps(b.requests[0]['messages'])
    assert a.workspace_discovery and a.information_complete
    assert 'keine vollständige Projektprüfung' in answer
    kinds=[kind for kind,_ in events]
    assert kinds.index('WORKSPACE_DISCOVERY_COMPLETED')<kinds.index('MODEL_CALL')
    calls=[d['name'] for k,d in events if k=='TOOL_REQUEST']
    assert calls[0]=='list_files' and 'read_file' in calls and 'write_file' not in calls
    assert len(b.requests)==1
    if mode=='none':
        packet=json.loads(b.requests[0]['messages'][-1]['content'])
        assert packet['workspace_discovery']['project_complete'] is False
        assert packet['workspace_discovery']['files_read']
    else:assert not {'write_file','make_directory'} & {t['function']['name'] for t in b.requests[0]['tools']}

def test_native_extra_read_uses_same_bounded_snapshot(tmp_path):
    project(tmp_path);events=[]
    a,b=make(tmp_path,'native',[tool_reply('read_file',{'path':'project.godot'}),response('Real analysis')],events=events)
    asyncio.run(a.run(ANALYSE))
    assert a.resources.counters.cache_hits==1
    assert a.resources.counters.file_reads==len(a.workspace_discovery.files)

def test_concrete_file_regression(tmp_path):
    project(tmp_path)
    a,b=make(tmp_path,'native',[tool_reply('read_file',{'path':'project.godot'}),response('REAL_PROJECT_SENTINEL is configured.')])
    answer=asyncio.run(a.run('Analyse the file project.godot'))
    assert 'REAL_PROJECT_SENTINEL' in answer and not a.workspace_discovery
    assert a.resources.counters.file_reads==1 and len(b.requests)==2

def test_ignored_dirs_binaries_and_limits(tmp_path):
    project(tmp_path)
    for directory in ['node_modules','.git','dist','.godot','.venv','assets']:
        (tmp_path/directory).mkdir()
        (tmp_path/directory/('video.mp4' if directory=='assets' else 'should_not_read.py')).write_bytes(b'FORBIDDEN_DATA'*500)
    events=[];a,b=make(tmp_path,events=events);asyncio.run(a.run(ANALYSE))
    paths=[d['arguments']['path'] for k,d in events if k=='TOOL_REQUEST' and d['name']=='read_file']
    assert not any(set(Path(p).parts)&{'node_modules','.git','dist','.godot','.venv','video.mp4'} for p in paths)
    assert 'FORBIDDEN_DATA' not in json.dumps(b.requests)

def test_large_fixture_bounds(tmp_path):
    project(tmp_path)
    for i in range(250):(tmp_path/f'file_{i}.txt').write_text('x'*1800,encoding='utf-8')
    (tmp_path/'too_big.md').write_text('z'*20000)
    deep=tmp_path/'scripts/deeper/third';deep.mkdir(parents=True);(deep/'never.py').write_text('TOO_DEEP')
    a,b=make(tmp_path);asyncio.run(a.run(ANALYSE));d=a.workspace_discovery;l=DiscoveryLimits()
    assert d.entries_seen<=l.entries and len(d.listings)<=l.directories
    assert len(d.files)<=l.file_reads and d.read_attempts<=l.file_reads
    assert d.bytes_read<=d.limits['effective_content_bytes']<=l.total_bytes
    assert all(len(f['content'].encode())<=l.file_bytes for f in d.files)
    assert all(len(Path(p).parts)<=l.depth for p in d.listings)
    assert 'TOO_DEEP' not in json.dumps(b.requests)

@pytest.mark.parametrize('failure',['empty','tool'])
def test_discovery_failure_has_no_model_or_capability_negative(tmp_path,failure):
    store=ExperienceStore(tmp_path/'experience.sqlite3');events=[]
    a,b=make(tmp_path,events=events,store=store)
    if failure=='tool':
        def broken(*args):raise OSError('simulated listing failure')
        a.tools.execute=broken
    with pytest.raises(CompletionRejected) as exc:asyncio.run(a.run(ANALYSE))
    assert exc.value.failure_type in {F.INFORMATION_MISSING,F.TOOL_FAILURE}
    assert not b.requests and not a.information_complete
    with store.connection() as db:assert not db.execute('SELECT id FROM model_experience WHERE success_value=0').fetchall()

def test_stop_during_discovery_no_later_read_or_model(tmp_path):
    project(tmp_path);a,b=make(tmp_path);reads=[]
    def observe(kind,details,*_):
        if kind=='FILE_READ':reads.append(details['path']);a.stopped.set()
    a.observe=observe
    with pytest.raises(asyncio.CancelledError):asyncio.run(a.run(ANALYSE))
    assert len(reads)==1 and not b.requests

def test_discovery_data_cannot_be_trimmed_to_false_complete(tmp_path):
    project(tmp_path);a,b=make(tmp_path);a.settings.agent.context_budget=2048
    with pytest.raises(CompletionRejected) as exc:asyncio.run(a.run(ANALYSE))
    assert exc.value.failure_type in {F.RESOURCE_CONTEXT_LIMIT,F.INFORMATION_MISSING}
    assert not b.requests and not a.information_complete

def test_standalone_listing_and_analysis_supported(tmp_path):
    project(tmp_path);a,b=make(tmp_path,'native',standalone=True)
    assert 'project.godot' in asyncio.run(a.run(LIST)) and not b.requests
    answer=asyncio.run(a.run(ANALYSE));assert 'keine vollständige Projektprüfung' in answer

def test_tools_obey_explicit_read_and_listing_byte_limits(tmp_path):
    (tmp_path/'big.txt').write_text('12345')
    tools=ToolRegistry(Workspace(tmp_path))
    with pytest.raises(ValueError):tools.execute('read_file',{'path':'big.txt','max_bytes':3})
    for i in range(5):(tmp_path/f'{i}.txt').write_text('x')
    result=tools.execute('list_files',{'path':'.','limit':2})
    assert len(result['entries'])==2 and result['scanned_entries']==2 and result['truncated']

def test_explicit_json_contract_remains_valid(tmp_path):
    project(tmp_path);a,b=make(tmp_path,responses=[response('{"engine":"Godot"}')])
    from alienape.validators import ExactValueValidator
    a.validator=ExactValueValidator({'engine':'Godot'})
    answer=asyncio.run(a.run('Analyse the current project folder. Reply only as JSON with engine.'))
    assert json.loads(answer)=={'engine':'Godot'} and a.workspace_discovery.metadata()['project_complete'] is False

def test_no_recursive_read_when_native_requests_excluded_directory(tmp_path):
    project(tmp_path);(tmp_path/'node_modules').mkdir();(tmp_path/'node_modules/private.txt').write_text('DO_NOT_READ')
    a,b=make(tmp_path,'native',[tool_reply('list_files',{'path':'node_modules'}),response('Snapshot analysis')])
    asyncio.run(a.run(ANALYSE))
    assert a.resources.counters.file_reads==len(a.workspace_discovery.files)
    assert 'DO_NOT_READ' not in json.dumps(b.requests)

def test_empty_listing_is_a_success_without_llm(tmp_path):
    a,b=make(tmp_path);answer=asyncio.run(a.run(LIST))
    assert '(leer)' in answer and a.last_failure_type==F.SUCCESS and not b.requests
