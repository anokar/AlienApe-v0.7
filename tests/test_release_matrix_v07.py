import asyncio,sqlite3
from dataclasses import asdict
from pathlib import Path
import pytest
from alienape.agent import Agent
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.tools import Workspace
from alienape.memory import MemoryStore
from alienape.diagnostics import DiagnosticStore

async def yes(*_):return True
async def no(*_):return False

def run_case(path,locale,kind):
    path.mkdir();workspace=path/'workspace';workspace.mkdir();settings=Settings();settings.ui.language=locale;settings.verification.mode='OFF'
    memory=MemoryStore(path/'isolated-memory.sqlite3','fixture');events=[]
    a=Agent(MockBackend(delay=0),Workspace(workspace),settings,memory=memory,permission=no if kind=='deny' else yes,observe=lambda k,d,*_:events.append((k,d)))
    if kind in {'read','choice','modify'}:(workspace/'hello.txt').write_text('hello',encoding='utf-8')
    cases={'capability':['Was kannst du mit Dateien machen?'],'directory':['erstelle einen Ordner report'], 'create':['erstelle note.txt leer'],'read':['lies hello.txt'],'pending':['erstelle eine textdatei'],'choice':['lösche den inhalt von hello.txt','den inhalt'],'modify':['lösche den inhalt von hello.txt'],'deny':['erstelle denied.txt leer'],'delete':['lösche hello.txt'],'memory':['merke dir: Lieblingswort ist Banane'],'sensitive':['merke dir: api key FAKE_SECRET_NOT_REAL_123456789'],'status':['Welches Modell benutze ich gerade?'],'verification':['erstelle note.py leer']}
    observed=[]
    for text in cases[kind]:
        try:asyncio.run(a.run(text));error=None
        except Exception as exc:error=type(exc).__name__
        r=a.intent_resolution
        observed.append({'intent':r.intent_id.value if r else None,'slots':asdict(r.slots) if r and r.slots else None,'paths':list(r.referenced_paths) if r else [],'outputs':list(r.output_paths) if r else [],'error':error})
    with sqlite3.connect(memory.database) as db:memory_count=db.execute('select count(*) from memory').fetchone()[0]
    return {'observed':observed,'tools':[(k,d.get('name'),d.get('origin')) for k,d in events if k in {'TOOL_REQUEST','TOOL_DENIED'}],'writes':sorted(str(x.relative_to(workspace)) for x in workspace.rglob('*') if x.is_file()),'model_calls':a.resources.counters.model_calls,'write_scope':asdict(a.write_scope) if a.write_scope else None,'memory_count':memory_count,'verification':getattr(getattr(a,'verification',None),'result',None).verification_status if getattr(getattr(a,'verification',None),'result',None) else None}

@pytest.mark.parametrize('kind',['capability','directory','create','read','pending','choice','modify','deny','delete','memory','sensitive','status','verification'])
def test_locale_preserves_controller_decisions(tmp_path,kind):
    values=[run_case(tmp_path/locale,locale,kind) for locale in ['de','en','fr']]
    assert values[0]==values[1]==values[2]

@pytest.mark.parametrize('locale',['de','en','fr'])
def test_diagnostic_failure_remains_an_error(tmp_path,locale):
    s=DiagnosticStore(tmp_path/'diag')
    with pytest.raises(ValueError):s.export(tmp_path/'not-created.zip')
    assert not (tmp_path/'not-created.zip').exists()


def test_locale_does_not_change_router_choice():
    from alienape.routing import ModelRegistry,ModelRouter
    from test_routing import profile
    profiles=[profile('a','coding','low',tool_mode='none'),profile('b','coding','medium',tool_mode='none')]
    selected=[]
    for locale in ['de','en','fr']:
        settings=Settings();settings.ui.language=locale
        selected.append(ModelRouter(ModelRegistry(profiles),settings.routing).select_model('Write Python').profile.id)
    assert selected[0]==selected[1]==selected[2]


@pytest.mark.parametrize('valid',[True,False])
def test_protocol_experience_and_routing_invariance(tmp_path,valid):
    import json
    from alienape.experience import ExperienceStore
    from alienape.completion import CompletionRejected
    from test_workspace_discovery import make
    from test_completion_recovery import response
    outcomes=[]
    for lang in ['de','en','fr']:
        folder=tmp_path/lang;folder.mkdir();store=ExperienceStore(folder/'e.sqlite3')
        raw=json.dumps({'content':'hello'}) if valid else '```html\nhello\n```'
        agent,backend=make(folder,responses=[response(raw)],store=store);agent.settings.ui.language=lang;agent.settings.verification.mode='OFF';agent.permission=yes
        try:asyncio.run(agent.run('erstelle index.html mit einem generierten beispiel'));error=None
        except CompletionRejected as exc:error=str(exc.failure_type)
        with store.connection() as db:rows=[tuple(x) for x in db.execute("select task_category,success_signal,response_contract,protocol_status from model_experience where model_calls>0 order by id")]
        outcomes.append({'profile':agent.current_profile.id,'contract':str(agent.response_contract.mode),'calls':agent.resources.counters.model_calls,'rows':rows,'error':error})
    assert outcomes[0]==outcomes[1]==outcomes[2]
