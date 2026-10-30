import asyncio
import json
import sqlite3
from pathlib import Path
import pytest
from alienape.contracts import ResponseContract, ResponseMode, contract_for_task, normalize_response, WriteScope
from alienape.agent import Agent
from alienape.backends import tool_reply
from alienape.completion import CompletionRejected, FailureType as F
from alienape.config import Settings
from alienape.experience import ExperienceStore
from alienape.routed_agent import RoutedAgent
from alienape.routing import ModelRegistry, referenced_paths
from alienape.taxonomy import task_category
from alienape.tools import Workspace, ToolRegistry, ToolArgumentInvalid
from alienape.validators import ExactValueValidator, validator_for_task
from test_completion_recovery import Script, response, make
from test_routed_agent import Pool
from test_routing import profile

SAVED=json.loads((Path(__file__).parent/'fixtures/v04_protocol_cases.json').read_text(encoding='utf-8'))

@pytest.mark.parametrize('task,mode',[
    ('Was ist JSON?',ResponseMode.PLAIN_TEXT),
    ('Was bedeutet null in JSON?',ResponseMode.PLAIN_TEXT),
    ('Erkläre Arrays in JSON.',ResponseMode.PLAIN_TEXT),
    ('Antworte als JSON mit A und B',ResponseMode.STRUCTURED_JSON),
    ('Analysiere diese JSON-Datei settings.json',ResponseMode.ANALYSIS_RESULT),
    ('Ändere nur player.gd.',ResponseMode.FILE_PROPOSAL),
])
def test_contract_is_explicit(task,mode):
    assert contract_for_task(task).mode==mode
    if mode==ResponseMode.PLAIN_TEXT:assert validator_for_task(task) is None

@pytest.mark.parametrize('name,expected',[
    ('C-model_1-file',{'main_scene':'res://scenes/isometric_world.tscn','renderer':'gl_compatibility'}),
    ('C-model_1-information',{'target':'archipelago','prefix':'SEA'}),
    ('C-model_1-python',{'code':'def solve(a,b):\n    return a + b'}),
    ('C-model_2-python',{'code':'def solve(a, b):\n return a + b'}),
    ('C-model_4-structured',{'city':'Bern','active':True,'count':2}),
])
def test_saved_v04_protocol_payloads(name,expected):
    result=normalize_response(SAVED[name]['text'],contract_for_task('Antworte als JSON'))
    assert json.loads(result.payload)==expected

def test_fence_only_no_healing_or_extra_text():
    contract=contract_for_task('Antworte als JSON')
    inner=' { "A" : false } '
    normalized=normalize_response('```json\n'+inner+'\n```',contract)
    assert normalized.payload==inner and normalized.protocol_normalized
    for text in ['```json\n{"A":\n```','Hier:\n```json\n{}\n```','```json\n{}\n```\nErklärung']:
        assert normalize_response(text,contract).payload==text
        assert not normalize_response(text,contract).protocol_normalized

def test_invalid_envelope_and_user_answer_key_not_confused():
    contract=ResponseContract(mode=ResponseMode.STRUCTURED_JSON,expects_json=True,required_keys=['answer'])
    assert normalize_response('{"answer":42}',contract).payload=='{"answer":42}'
    with pytest.raises(CompletionRejected):normalize_response('{"payload":{},"status":7}',contract)
    assert json.loads(normalize_response('{"payload":{"A":false},"status":"ok"}',contract).payload)=={'A':False}

def test_godot_domain_not_json_domain():
    assert str(task_category('Analysiere player.gd und antworte als JSON'))=='GODOT_CODE'
    assert str(task_category('Debug Python und antworte als JSON'))=='PYTHON_DEBUG'
    assert set(referenced_paths('Lies project.godot scene.tscn item.tres config.cfg'))=={'project.godot','scene.tscn','item.tres','config.cfg'}
    assert not referenced_paths('Lies evil.exe')

@pytest.mark.parametrize('task,readonly,paths',[
    ('Ändere nichts.',True,[]),
    ('Nur analysieren, keine Dateien verändern.',True,[]),
    ('Ändere note.txt, aber keine andere Datei.',False,['note.txt']),
    ('Ändere nur player.gd.',False,['player.gd']),
])
def test_write_scope(task,readonly,paths):
    scope=WriteScope.for_task(task)
    assert scope.read_only==readonly and scope.allowed_paths==paths

def agent_for(tmp_path,answers,*,mode='none',validator=None,permission=None,store=None):
    settings=Settings(); settings.models=[profile('test','general',tool_mode=mode)]
    settings.routing.allow_escalation=False
    backend=Script(answers)
    agent=RoutedAgent(Workspace(tmp_path),settings,registry=ModelRegistry(settings.models),pool=Pool({'test':backend}),
        validator=validator,permission=permission,experience=store)
    return agent,backend

def test_saved_fence_through_full_gate_and_experience(tmp_path):
    store=ExperienceStore(tmp_path/'e.db')
    agent,backend=agent_for(tmp_path,[response(SAVED['C-model_4-structured']['text'])],
        validator=ExactValueValidator({'city':'Bern','active':True,'count':2}),store=store)
    events=[];agent.observe=lambda kind,details,*_:events.append((kind,details))
    assert json.loads(asyncio.run(agent.run('Antworte nur als JSON')))=={'city':'Bern','active':True,'count':2}
    assert any(k=='PROTOCOL_NORMALIZED' and d['normalization_type']=='MARKDOWN_JSON_FENCE_REMOVED' for k,d in events)
    with store.connection() as db:r=dict(db.execute('SELECT * FROM model_experience WHERE model_calls>0').fetchone())
    assert r['task_domain']=='GENERAL_SIMPLE' and r['response_contract']=='STRUCTURED_JSON'
    assert r['protocol_normalized']==1 and r['attribution_version']=='v0.5'

def test_none_godot_delivered_and_missing_not_capability(tmp_path):
    (tmp_path/'project.godot').write_text('run/main_scene="world"',encoding='utf-8')
    a,b=agent_for(tmp_path,[response('{"scene":"world"}')],validator=ExactValueValidator({'scene':'world'}))
    asyncio.run(a.run('Lies project.godot und antworte als JSON'))
    assert 'run/main_scene' in json.dumps(b.requests[0]['messages'])
    (tmp_path/'project.godot').unlink()
    store=ExperienceStore(tmp_path/'missing.db')
    a,b=agent_for(tmp_path,[response('{}')],store=store)
    with pytest.raises((CompletionRejected,FileNotFoundError)):asyncio.run(a.run('Lies project.godot und antworte als JSON'))
    assert a.last_failure_type==F.INFORMATION_MISSING and not b.requests
    with store.connection() as db:assert not db.execute('SELECT id FROM model_experience WHERE success_value=0').fetchall()

def test_write_scope_reaches_permission_and_blocks_other_target(tmp_path):
    (tmp_path/'note.txt').write_text('old')
    approvals=[]
    async def approve(name,args,path):approvals.append(path.name);return True
    proposal=json.dumps({'answer':'Änderung','proposed_files':[{'path':'note.txt','content':'new'}],'status':'ok'})
    a,b=agent_for(tmp_path,[response(proposal)],permission=approve)
    asyncio.run(a.run('Ändere nur note.txt, aber keine andere Datei.'))
    assert approvals==['note.txt'] and (tmp_path/'note.txt').read_text()=='new'
    wrong=proposal.replace('note.txt','other.txt')
    a,b=agent_for(tmp_path,[response(wrong)],permission=approve)
    with pytest.raises(CompletionRejected):asyncio.run(a.run('Ändere nur note.txt.'))
    assert approvals==['note.txt'] and not (tmp_path/'other.txt').exists()

@pytest.mark.parametrize('task,write',[
    ('Was ist JSON?',False),('Analysiere nur note.txt',False),('Ändere nur note.txt',True)])
def test_native_tool_gating(tmp_path,task,write):
    (tmp_path/'note.txt').write_text('old')
    responses=[tool_reply('read_file',{'path':'note.txt'}),response('Fertig')] if 'note.txt' in task else [response('JSON ist ein Format.')]
    a,b=agent_for(tmp_path,responses,mode='native')
    asyncio.run(a.run(task))
    names=[t['function']['name'] for t in b.requests[0]['tools']]
    assert ('write_file' in names)==write
    if write:
        schema=next(t['function']['parameters'] for t in b.requests[0]['tools'] if t['function']['name']=='write_file')
        assert schema['properties']['path']['enum']==['note.txt']

def test_native_missing_information_before_negative_result(tmp_path, monkeypatch):
    # Simulate a genuinely missing context delivery, rather than relying on old
    # behavior that did not prefetch explicit file inputs.
    async def no_explicit_inputs(agent): return []
    monkeypatch.setattr('alienape.agent.provide_explicit_inputs', no_explicit_inputs)
    (tmp_path/'note.txt').write_text('source')
    store=ExperienceStore(tmp_path/'e.db')
    a,b=agent_for(tmp_path,[response('{"wrong":true}')],mode='native',validator=ExactValueValidator({'ok':True}),store=store)
    with pytest.raises(CompletionRejected) as exc:asyncio.run(a.run('Analysiere note.txt und antworte als JSON'))
    assert exc.value.failure_type==F.INFORMATION_MISSING
    assert len(b.requests)==1 and not any((m.get('content') or '').startswith('WORKSPACE_INPUTS\n') for m in b.requests[0]['messages'])
    with store.connection() as db:
        r=db.execute('SELECT * FROM model_experience').fetchone()
        assert r['information_complete']==0 and r['success_value'] is None
        assert r['success_signal']=='UNKNOWN' and r['failure_type']=='INFORMATION_MISSING'

def test_native_explicit_input_delivered_before_validation(tmp_path):
    (tmp_path/'note.txt').write_text('source')
    store=ExperienceStore(tmp_path/'e.db')
    a,b=agent_for(tmp_path,[response('{"wrong":true}')],mode='native',validator=ExactValueValidator({'ok':True}),store=store)
    with pytest.raises(CompletionRejected) as exc: asyncio.run(a.run('Analysiere note.txt und antworte als JSON'))
    assert exc.value.failure_type==F.VALIDATION_FAILURE
    assert any((m.get('content') or '').startswith('WORKSPACE_INPUTS\n') and 'source' in m['content'] for m in b.requests[0]['messages'])
    with store.connection() as db:
        r=db.execute('SELECT * FROM model_experience').fetchone()
        assert r['information_complete']==1 and r['success_value']==0 and r['failure_type']=='VALIDATION_FAILURE'

def test_invalid_search_argument_before_os_and_unknown_experience(tmp_path):
    (tmp_path/'note.txt').write_text('source')
    tools=ToolRegistry(Workspace(tmp_path))
    with pytest.raises(ToolArgumentInvalid,match='must be a directory'):tools.execute('search_files',{'path':'note.txt','query':'x'})
    assert tools.execute('search_files',{'directory':'.','query':'source'})['matches']
    store=ExperienceStore(tmp_path/'e.db')
    a,b=agent_for(tmp_path,[tool_reply('search_files',{'directory':'note.txt','query':'x'}),response('Kein Ergebnis')],mode='native',store=store)
    asyncio.run(a.run('Suche x im Workspace'))
    with store.connection() as db:
        r=db.execute('SELECT * FROM model_experience WHERE model_calls>0').fetchone()
        assert r['success_value'] is None and r['failure_type']=='TOOL_FAILURE'

def test_store_defends_attribution_and_legacy_rows(tmp_path):
    # Use the real archived schema/data, not a fabricated toy migration.
    import os
    fixture=os.environ.get('ALIENAPE_HISTORICAL_EXPERIENCE')
    if not fixture:pytest.skip('Authoritative historical Experience fixture not configured')
    old=Path(fixture)
    import shutil
    shutil.copy2(old,tmp_path/'e.db')
    with sqlite3.connect(tmp_path/'e.db') as db:before=db.execute('SELECT id,task_category,success_value FROM model_experience ORDER BY id').fetchall()
    store=ExperienceStore(tmp_path/'e.db')
    with store.connection() as db:
        assert [tuple(r) for r in db.execute('SELECT id,task_category,success_value FROM model_experience ORDER BY id')]==before
        assert all(r[0] is None for r in db.execute('SELECT attribution_version FROM model_experience'))
    from alienape.experience import ModelFingerprint,SuccessSignal as S
    p=profile('x');fp=store.register(p,ModelFingerprint.for_profile(p))
    for failure in ['INFORMATION_MISSING','TOOL_FAILURE','PERMISSION_FAILURE']:
        store.record(fp,'GENERAL_SIMPLE',S.VERIFIED_FAILURE,failure_type=failure,attribution_version='v0.5',information_complete=False)
    with store.connection() as db:assert not db.execute('SELECT id FROM model_experience WHERE attribution_version=? AND success_value=0',('v0.5',)).fetchall()

def test_truncated_fenced_json_still_recovers_once(tmp_path):
    a,b,large,events,store=make(tmp_path,[response('```json\n{"A":','length'),response('```json\n{"A": false,"B":true,"C":true,"D":false,"E":false}\n```')])
    # Explicit test-local declarations isolate recovery from mutable Model Cards.
    from dataclasses import asdict
    from alienape.profile_v2 import ProfileV2
    for profile in a.registry.profiles.values():
        v=ProfileV2()
        v.task_capabilities['general_chat']='SUPPORTED'
        v.protocol_capabilities['structured_json']='SUPPORTED'
        profile.profile_v2=asdict(v)
    asyncio.run(a.run('Antworte als JSON'))
    assert [r['limit'] for r in b.requests]==[1024,2048]
    assert b.requests[0]['messages']==b.requests[1]['messages'] and not large.requests

def test_truncated_file_proposal_recovers_without_partial_write(tmp_path):
    (tmp_path/'note.txt').write_text('old')
    approvals=[]
    async def approve(*args):approvals.append(args[2].name);return True
    a,b=agent_for(tmp_path,[response('{"payload":','length'),response('{"payload":"Fertig","status":"ok","proposed_files":[{"path":"note.txt","content":"new"}]}')],permission=approve)
    asyncio.run(a.run('Ändere nur note.txt.'))
    assert [r['limit'] for r in b.requests]==[1024,2048] and approvals==['note.txt']
    assert (tmp_path/'note.txt').read_text()=='new'

def test_missing_memory_and_trimmed_context_cannot_be_negative(tmp_path):
    store=ExperienceStore(tmp_path/'e.db')
    a,b=agent_for(tmp_path,[response('{"prefix":"invented"}')],validator=ExactValueValidator({'prefix':'SEA'}),store=store)
    with pytest.raises(CompletionRejected) as exc:asyncio.run(a.run('Nutze die gespeicherte Projektregel region-prefix und antworte als JSON.'))
    assert exc.value.failure_type==F.INFORMATION_MISSING
    with store.connection() as db:assert db.execute('SELECT success_value FROM model_experience').fetchone()[0] is None

def test_text_about_write_does_not_expose_write_tools():
    assert WriteScope.for_task('Was bedeutet write in Python?').read_only
    assert WriteScope.for_task('Erkläre das Wort ändere.').read_only

def test_feedback_cannot_promote_missing_info_to_capability_negative(tmp_path):
    from alienape.experience import ModelFingerprint,SuccessSignal as S
    store=ExperienceStore(tmp_path/'e.db');p=profile('fixture');fp=store.register(p,ModelFingerprint.for_profile(p))
    identifier=store.record(fp,'FILE_ANALYSIS',S.VERIFIED_FAILURE,failure_type='INFORMATION_MISSING',information_complete=False,attribution_version='v0.5')
    store.feedback(identifier,False)
    assert store.summary(fp,'FILE_ANALYSIS')['hard_sample_count']==0

def test_correct_format_wrong_values_still_capability_negative(tmp_path):
    store=ExperienceStore(tmp_path/'e.db')
    a,b=agent_for(tmp_path,[response('{"allowed":true}')],validator=ExactValueValidator({'allowed':False}),store=store)
    with pytest.raises(CompletionRejected) as exc:asyncio.run(a.run('Antworte als JSON mit allowed=false.'))
    assert exc.value.failure_type==F.VALIDATION_FAILURE
    with store.connection() as db:
        r=db.execute('SELECT * FROM model_experience').fetchone()
        assert r['information_complete']==1 and r['success_value']==0

def test_mistral_narrative_is_not_silently_deleted():
    result=normalize_response(SAVED['C-model_2-structured']['text'],contract_for_task('Antworte als JSON'))
    with pytest.raises(ValueError):json.loads(result.payload)

def test_json_fallback_fence_normalized_with_original_pipeline(tmp_path):
    import httpx
    from alienape.backends import OllamaBackend
    from alienape.routed_agent import ModeBackend
    def handle(request):
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'```json\n{"answer":"fertig"}\n```'}}]})
    raw=OllamaBackend(transport=httpx.MockTransport(handle))
    a=Agent(ModeBackend(raw,profile('x',tool_mode='json_fallback')),Workspace(tmp_path),Settings())
    assert asyncio.run(a.run('Erkläre etwas'))=='fertig' and a.protocol_normalized

def test_reused_agent_does_not_keep_previous_write_scope(tmp_path):
    (tmp_path/'note.txt').write_text('old')
    a,b=agent_for(tmp_path,[tool_reply('read_file',{'path':'note.txt'}),response('Gelesen'),response('JSON ist ein Format.')],mode='native')
    asyncio.run(a.run('Lies note.txt und ändere nur note.txt.'))
    asyncio.run(a.run('Was ist JSON?'))
    assert b.requests[-1]['tools']==[] and a.write_scope.read_only

def test_tool_action_cannot_claim_completion_without_action(tmp_path):
    a,b=agent_for(tmp_path,[response('Dateien aufgelistet')],mode='native')
    with pytest.raises(CompletionRejected) as exc:asyncio.run(a.run('Rufe list_files im Workspace auf.'))
    assert exc.value.failure_type==F.TOOL_FAILURE
    assert contract_for_task('Liste drei Vorteile auf').mode==ResponseMode.PLAIN_TEXT
