import asyncio,json,sqlite3
import pytest
from alienape.experience import ExperienceStore
from alienape.completion import CompletionRejected
from test_workspace_discovery import make
from test_completion_recovery import response

def run_case(tmp_path,text,mode='OFF',name='index.html'):
    store=ExperienceStore(tmp_path/'e.sqlite3');a,b=make(tmp_path,responses=[response(text)],store=store);a.settings.verification.mode=mode
    async def yes(*_):return True
    a.permission=yes
    try:asyncio.run(a.run('erstelle '+name+' mit einem generierten beispiel'))
    except CompletionRejected:pass
    with store.connection() as db:row=dict(db.execute("select * from model_experience where source='live' and model_calls>0 order by id desc").fetchone())
    return row,a

def test_protocol_success_without_verifier(tmp_path):
    row,a=run_case(tmp_path,json.dumps({'content':'<button>ok</button>'}))
    assert row['protocol_status']=='VERIFIED_SUCCESS' and row['success_signal']=='UNKNOWN' and row['verified']==0

def test_protocol_failure_without_domain_failure(tmp_path):
    row,a=run_case(tmp_path,'```html\n<button>ok</button>\n```')
    assert row['protocol_status']=='VERIFIED_FAILURE' and not (tmp_path/'index.html').exists()

def test_successful_protocol_invalid_python(tmp_path):
    row,a=run_case(tmp_path,json.dumps({'content':'def broken(:\n'}),'AUTO_SAFE','testfile.py')
    assert row['protocol_status']=='VERIFIED_SUCCESS' and row['success_signal']=='VERIFIED_FAILURE'

def test_successful_protocol_valid_python(tmp_path):
    row,a=run_case(tmp_path,json.dumps({'content':'x=1\n'}),'AUTO_SAFE','testfile.py')
    assert row['protocol_status']=='VERIFIED_SUCCESS'
    assert row['verification_json'] is not None


def test_protocol_and_domain_both_succeed(tmp_path):
    (tmp_path/'test_check.py').write_text('def test_ok():\n    assert True\n',encoding='utf-8')
    row,a=run_case(tmp_path,json.dumps({'content':'x=1\n'}),'AUTO_SAFE','hello.py')
    assert row['protocol_status']=='VERIFIED_SUCCESS'
    assert row['success_signal']=='VERIFIED_SUCCESS'
    assert json.loads(row['verification_json'])['post_status']=='PASS'

def test_controller_changed_content_is_not_positive_evidence(tmp_path,monkeypatch):
    from alienape.agent import transport_redactor
    original=transport_redactor.clean
    def altered(value):
        result=original(value)
        if isinstance(result,dict) and isinstance(result.get('content'),str) and 'ORIGINAL_MARKER' in result['content']:
            result['content']=result['content'].replace('ORIGINAL_MARKER','ALTERED_MARKER')
        return result
    monkeypatch.setattr(transport_redactor,'clean',altered)
    row,a=run_case(tmp_path,json.dumps({'content':'ORIGINAL_MARKER'}))
    assert row['protocol_status'] is None
    assert not (tmp_path/'index.html').exists()

def test_historical_rows_survive_protocol_schema_migration(tmp_path):
    from pathlib import Path
    import shutil
    import os
    fixture=os.environ.get('ALIENAPE_HISTORICAL_EXPERIENCE')
    if not fixture:pytest.skip('Authoritative historical Experience fixture not configured')
    archive=Path(fixture)
    assert archive.is_file()
    dbfile=tmp_path/'legacy.sqlite3'
    shutil.copyfile(archive,dbfile)
    with sqlite3.connect(dbfile) as db:
        columns=[r[1] for r in db.execute('pragma table_info(model_experience)')]
        assert 'protocol_status' not in columns
        before=db.execute('select * from model_experience order by id').fetchall()
        assert before
    ExperienceStore(dbfile)
    with sqlite3.connect(dbfile) as db:
        after=db.execute('select '+','.join('"'+c+'"' for c in columns)+' from model_experience order by id').fetchall()
        assert before==after
        assert db.execute('select count(*) from model_experience where protocol_status is not null').fetchone()[0]==0


def test_router_uses_actual_live_protocol_outcomes_only_for_file_content(tmp_path):
    from alienape.config import Settings
    from alienape.routing import ModelRegistry,ModelRouter
    from alienape.routed_agent import RoutedAgent
    from alienape.tools import Workspace
    from test_routing import profile
    from test_routed_agent import Pool
    from test_completion_recovery import Script
    store=ExperienceStore(tmp_path/'live.sqlite3')
    profiles=[profile('a','coding','low',tool_mode='none'),profile('b','coding','medium',tool_mode='none')]
    fingerprints={}
    async def yes(*_): return True
    for p,reply in zip(profiles,['```python\nx=1\n```',json.dumps({'content':'x=1\n'})]):
        folder=tmp_path/p.id;folder.mkdir()
        settings=Settings();settings.models=[p];settings.verification.mode='OFF'
        agent=RoutedAgent(Workspace(folder),settings,registry=ModelRegistry([p]),pool=Pool({p.id:Script([response(reply)])}),permission=yes,experience=store)
        try: asyncio.run(agent.run('erstelle hello.py mit einem generierten beispiel'))
        except CompletionRejected: pass
        fingerprints[p.id]=agent.fingerprints[p.id]
    with store.connection() as db:
        rows=db.execute("select protocol_status,success_signal from model_experience where source='live' and model_calls>0 order by id").fetchall()
    assert [r['protocol_status'] for r in rows]==['VERIFIED_FAILURE','VERIFIED_SUCCESS']
    assert rows[1]['success_signal']=='UNKNOWN'
    settings=Settings();settings.routing.min_samples_for_experience=1
    router=ModelRouter(ModelRegistry(profiles),settings.routing,store,fingerprints)
    assert router.select_model('Write Python',state={'response_contract':'FILE_CONTENT'}).profile.id=='b'
    assert router.select_model('Write Python').profile.id=='a'

