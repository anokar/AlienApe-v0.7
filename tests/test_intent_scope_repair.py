import asyncio,json
import pytest
from alienape.agent import Agent
from alienape.completion import ModelResponse,CompletionMetadata,FailureType as F,CompletionRejected
from alienape.contracts import WriteScope
from alienape.controller import ResourceController
from alienape.intents import information_need
from alienape.validators import ValidationResult,ResultValidator
from alienape.config import Settings
from alienape.tools import Workspace
from alienape.backends import tool_reply
from test_workspace_discovery import make
from test_completion_recovery import Script,response

@pytest.mark.parametrize('prompt,group',[
    ('Find where the PvP rules are implemented.','FIND_IMPLEMENTATION'),
    ('Locate PvP','FIND_IMPLEMENTATION'),('Where is movement defined?','FIND_IMPLEMENTATION'),
    ('Inspect the item system.','INSPECT_SUBSYSTEM'),('Inspect existing code.','INSPECT_SUBSYSTEM'),
    ('The tests in this Python project are failing. Find the cause, fix the implementation.','DEBUG_EXISTING_CODE'),
    ('Debug failing tests.','DEBUG_EXISTING_CODE'),
    ('Refactor the implementation. Do not change the tests.','REFACTOR_EXISTING_CODE'),
    ('Review the implementation.','REFACTOR_EXISTING_CODE'),
    ('Add support for underscores in this project.','MODIFY_EXISTING_CODE'),
    ('Untersuche das Itemsystem.','INSPECT_SUBSYSTEM'),
    ('Create a small Python module implementing slugify(text).',None),('What does write mean?',None)])
def test_information_groups(prompt,group):assert information_need(prompt)==group

@pytest.mark.parametrize('mode',['none','native'])
@pytest.mark.parametrize('prompt,names',[
    ('Find where the PvP rules are implemented. Inspect relevant files. Do not modify anything.',{'scripts/pvp_rules.gd'}),
    ('Inspect the item system. Do not modify files.',{'scripts/item_catalog.gd','scripts/item_definition.gd'})])
def test_targeted_real_content_before_inference(tmp_path,mode,prompt,names):
    (tmp_path/'scripts').mkdir()
    for i in range(8):(tmp_path/'scripts'/f'a{i}.gd').write_text('IRRELEVANT')
    for name in ['pvp_rules','item_catalog','item_definition']:(tmp_path/'scripts'/f'{name}.gd').write_text('extends RefCounted\n# '+name+' REAL_SENTINEL')
    a,b=make(tmp_path,mode)
    asyncio.run(a.run(prompt));delivered={f['path'] for f in a.workspace_discovery.files}
    assert names<=delivered and 'REAL_SENTINEL' in json.dumps(b.requests[0]['messages'])
    assert a.information_complete and a.write_scope.read_only
    assert not {'write_file','make_directory'}&{t['function']['name'] for t in (b.requests[0]['tools'] or [])}

@pytest.mark.parametrize('mode',['none','native'])
@pytest.mark.parametrize('prompt',[
    'The tests in this Python project are failing. Find the cause, fix the implementation, change only the necessary source file, and make the existing tests pass.',
    'Refactor the implementation to remove duplicated logic. Do not change the tests.'])
def test_discovered_source_scope_and_protected_tests(tmp_path,mode,prompt):
    (tmp_path/'inventory.py').write_text('def value(): return 1')
    (tmp_path/'test_inventory.py').write_text('from inventory import value\ndef test_value(): assert value()==1')
    (tmp_path/'unrelated.txt').write_text('not a target')
    a,b=make(tmp_path,mode)
    if mode=='none':b.responses=iter([response(json.dumps({'answer':'Checked','proposed_files':[{'path':'inventory.py','content':'def value(): return 1'}]}))])
    asyncio.run(a.run(prompt))
    assert {'inventory.py','test_inventory.py'}<={f['path'] for f in a.workspace_discovery.files}
    assert not a.write_scope.read_only and a.write_scope.permits('inventory.py',a.workspace)
    assert not a.write_scope.permits('test_inventory.py',a.workspace) and not a.write_scope.permits('unrelated.txt',a.workspace)
    if mode=='native':
        schema=next(t for t in b.requests[0]['tools'] if t['function']['name']=='write_file')
        assert schema['function']['parameters']['properties']['path']['enum']==['inventory.py']

@pytest.mark.parametrize('prompt',[
    'Do not modify anything. Analyse the project.','Do not change files. Fix the implementation.',
    'Only analyse. Change allowed.txt.','Only inspect.','Read only. Modify allowed.txt.',
    'Do not edit. Change allowed.txt.','Keine Dateien ändern. Repariere allowed.txt.','Nur analysieren.'])
def test_global_readonly_priority(prompt):assert WriteScope.for_task(prompt).read_only

@pytest.mark.parametrize('prompt,path',[
    ('Add support for underscores. Change only slugify.py.','slugify.py'),
    ('Change only allowed.txt. Do not modify any other file.','allowed.txt'),
    ('Fix the implementation. Change only inventory.py.','inventory.py'),
    ('Rename allowed.txt.','allowed.txt'),('Remove allowed.txt.','allowed.txt'),('Delete allowed.txt.','allowed.txt')])
def test_exact_targets(tmp_path,prompt,path):
    scope=WriteScope.for_task(prompt);w=Workspace(tmp_path)
    assert not scope.read_only and scope.allowed_paths==[path] and scope.deny_other_paths
    assert scope.permits(path,w) and not scope.permits('forbidden.txt',w) and not scope.permits('.hidden',w)

def test_exact_new_paths(tmp_path):
    scope=WriteScope.for_task('Create utils/slugify.py and tests/test_slugify.py.')
    assert scope.allow_new_file and scope.allowed_paths==['utils/slugify.py','tests/test_slugify.py']
    assert not scope.permits('extra.py',Workspace(tmp_path))

@pytest.mark.parametrize('mode',['none','native'])
def test_new_plan_precedes_model_and_is_finite(tmp_path,mode):
    events=[];a,b=make(tmp_path,mode,events=events)
    # The response need not implement code; this checks the actual outbound plan/scope.
    payload=json.dumps({'content':'def slugify(text): return text'})
    if mode=='none':b.responses=iter([response(payload)])
    else:b.responses=iter([tool_reply('write_file',{'path':'slugify.py','content':'def slugify(text): return text'}),response('Created')])
    asyncio.run(a.run('Create slugify.py implementing slugify(text).'))
    plan=next(d for k,d in events if k=='NEW_FILE_PLAN')
    assert plan['planned_new_paths']==['slugify.py']
    assert [k for k,d in events].index('NEW_FILE_PLAN')<[k for k,d in events].index('MODEL_CALL')
    assert not a.write_scope.permits('extra.py',a.workspace)

def test_new_plan_collision_never_overwrites(tmp_path):
    (tmp_path/'module.py').write_text('ORIGINAL')
    a,b=make(tmp_path)
    with pytest.raises(CompletionRejected):asyncio.run(a.run('Create module.py implementing a constant VALUE=1.'))
    assert not b.requests and (tmp_path/'module.py').read_text()=='ORIGINAL'

@pytest.mark.parametrize('mode',['none','native'])
def test_planned_nested_file_uses_normal_permissions_and_no_extra_paths(tmp_path,mode):
    a,b=make(tmp_path,mode);seen=[]
    async def permit(name,args,path):seen.append((name,args['path']));return True
    a.permission=permit
    if mode=='none':
        b.responses=iter([response(json.dumps({'content':'def test_value(): assert True'}))])
    else:b.responses=iter([tool_reply('write_file',{'path':'tests/test_module.py','content':'def test_value(): assert True'}),response('Created')])
    asyncio.run(a.run('Create tests/test_module.py with a generated Python test.'))
    assert (tmp_path/'tests/test_module.py').is_file()
    assert ('make_directory','tests') in seen
    assert not a.write_scope.permits('extra.py',a.workspace)
    assert not a.write_scope.permits('other',a.workspace,directory=True)

def test_unplanned_none_proposal_rejected_before_permission(tmp_path):
    a,b=make(tmp_path);seen=[]
    async def permit(*args):seen.append(args);return True
    a.permission=permit
    b.responses=iter([response(json.dumps({'answer':'Created','proposed_files':[{'path':'extra.py','content':'bad'}]}))])
    with pytest.raises(CompletionRejected):asyncio.run(a.run('Create module.py implementing a constant VALUE=1.'))
    assert not seen and not (tmp_path/'extra.py').exists()

@pytest.mark.parametrize('prompt',['Find where PvP is implemented.','The tests are failing. Find the cause and fix the implementation.'])
def test_missing_required_project_context_no_paid_call(tmp_path,prompt):
    (tmp_path/'README.md').write_text('unrelated text only')
    a,b=make(tmp_path)
    with pytest.raises(CompletionRejected) as exc:asyncio.run(a.run(prompt))
    assert exc.value.failure_type==F.INFORMATION_MISSING and not a.information_complete and not b.requests

@pytest.mark.parametrize('reason,tokens,limit,text,valid,complete,expected',[
    ('length',1024,1024,'',None,None,F.RESOURCE_OUTPUT_LIMIT),
    ('length',1023,1024,' ',None,None,F.RESOURCE_OUTPUT_LIMIT),
    ('length',500,1024,'',None,None,F.UNKNOWN),
    ('length',None,1024,'',None,None,F.UNKNOWN),
    ('UNKNOWN',1024,1024,'',None,None,F.UNKNOWN),
    ('stop',1024,1024,'',None,None,F.UNKNOWN),
    ('length',1024,1024,'VALID',True,True,F.SUCCESS),
    ('length',1024,1024,'unverified text',None,None,F.UNKNOWN)])
def test_conservative_empty_completion(reason,tokens,limit,text,valid,complete,expected):
    r=ModelResponse({'content':text},completion=CompletionMetadata(reason,100,tokens,limit))
    assert ResourceController().classify_completion(r,ValidationResult(valid,complete))==expected

@pytest.mark.parametrize('failure',[F.INFORMATION_MISSING,F.TOOL_FAILURE,F.PERMISSION_FAILURE,F.TIMEOUT])
def test_stronger_failure_not_hidden_by_exhaustion(failure):
    r=ModelResponse({'content':''},completion=CompletionMetadata('length',100,1024,1024))
    assert ResourceController().classify_completion(r,ValidationResult(False,True,False,failure))==failure

def test_empty_completion_existing_single_retry_exact_request(tmp_path):
    class Usable(ResultValidator):
        def validate(self,task,r):return ValidationResult(True,True,True,F.SUCCESS) if r['content']=='OK' else ValidationResult()
    b=Script([response('','length',output_tokens=1024),response('OK')]);events=[]
    a=Agent(b,Workspace(tmp_path),Settings(),validator=Usable(),observe=lambda k,d,*rest:events.append((k,d)))
    assert asyncio.run(a.run('Reply with OK.'))=='OK'
    assert [r['limit'] for r in b.requests]==[1024,2048]
    assert b.requests[0]['messages']==b.requests[1]['messages'] and b.requests[0]['tools']==b.requests[1]['tools']
    assert a.resources.counters.resource_retries==1 and sum(k=='RESOURCE_RETRY_STARTED' for k,d in events)==1

def test_no_second_resource_retry(tmp_path):
    b=Script([response('','length',output_tokens=1024),response('','length',output_tokens=2048)])
    a=Agent(b,Workspace(tmp_path),Settings())
    with pytest.raises(CompletionRejected):asyncio.run(a.run('Reply with OK.'))
    assert len(b.requests)==2 and a.resources.counters.resource_retries==1
