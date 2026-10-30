import asyncio,json,hashlib,math
from dataclasses import asdict
from types import SimpleNamespace
import httpx,pytest
from jsonschema import validate
from alienape.semantic_intent import SemanticSettings,SemanticIntentResolver,OllamaEmbeddingBackend,IntentSuggestion,DESCRIPTIONS,SCHEMA,accept_suggestion
from alienape.intent_resolution import IntentID as I,DeterministicIntentResolver
from alienape.agent import Agent,Session
from alienape.backends import MockBackend,tool_reply
from alienape.config import Settings
from alienape.tools import Workspace
from alienape.experience import ExperienceStore
from alienape.profiles import ModelProfile
from alienape.routing import ModelRegistry
from alienape.routed_agent import RoutedAgent
from alienape.diagnostics import DiagnosticStore

class Embeddings:
    def __init__(self,query_intent=I.CAPABILITY_QUERY):self.query_intent=query_intent;self.requests=[];self.digest='fixture-1';self.bad=False
    def vector(self,index):return [float(i==index) for i in range(len(DESCRIPTIONS)+1)]
    def handle(self,request):
        self.requests.append(request)
        assert request.url.host=='localhost' and request.url.path in {'/api/tags','/api/show','/api/embed'}
        assert 'authorization' not in request.headers
        if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'fixture-embed:latest','digest':self.digest}]})
        if request.url.path=='/api/show':return httpx.Response(200,json={'capabilities':['embedding']})
        body=json.loads(request.content);assert body['truncate'] is False
        vectors=[]
        for text in body['input']:
            index=list(DESCRIPTIONS.values()).index(text) if text in DESCRIPTIONS.values() else list(DESCRIPTIONS).index(self.query_intent)
            vectors.append(self.vector(index))
        if self.bad:vectors=[[float('nan')]]*len(body['input'])
        return httpx.Response(200,json={'embeddings':vectors})
    def resolver(self,config=None):
        config=config or SemanticSettings(enabled=True,model='fixture-embed:latest',declared_multilingual=True)
        return SemanticIntentResolver(config,OllamaEmbeddingBackend(config,httpx.MockTransport(self.handle)))

async def yes(*args):return True

def make(tmp_path,resolver=None,responses=(),session=None):
    s=Settings();s.semantic_resolver=SemanticSettings(enabled=True,model='fixture-embed:latest')
    return Agent(MockBackend(list(responses),delay=0),Workspace(tmp_path),s,session=session,semantic_resolver=resolver,permission=yes)


def test_schema_catalog_finite_advisory_only():
    assert len(DESCRIPTIONS)==16
    assert 'REASON_QUERY' not in DESCRIPTIONS
    validate(json.loads(json.dumps(asdict(IntentSuggestion()))),SCHEMA)
    value=json.loads(json.dumps(asdict(IntentSuggestion())));value['target_path']='invented.py'
    with pytest.raises(Exception):validate(value,SCHEMA)
    assert not hasattr(SemanticIntentResolver,'execute_tool')


def test_default_off_and_roundtrip(tmp_path):
    s=Settings();assert not s.semantic_resolver.enabled and not s.diagnostic_trace
    s.semantic_resolver=SemanticSettings(enabled=True,model='fixture-embed:latest',declared_multilingual=True)
    s.save(tmp_path/'config.toml');assert Settings.load(tmp_path/'config.toml')==s

@pytest.mark.parametrize('url',['https://evil.example','http://127.0.0.2:11434','http://user:password@localhost:11434','http://localhost:11434/api/pull','http://localhost:11434?token=x'])
def test_local_endpoint_only(url):
    with pytest.raises(ValueError):SemanticSettings(base_url=url).validate()


def test_actual_embed_transport_cache_and_fingerprint():
    fixture=Embeddings();resolver=fixture.resolver()
    first=asyncio.run(resolver.suggest('was kanst du?'));second=asyncio.run(resolver.suggest('What can you do for me?'))
    assert first.intent_id==I.CAPABILITY_QUERY and first.match_status=='HIGH_MATCH' and first.accepted
    validate(json.loads(json.dumps(asdict(first))),SCHEMA)
    embeds=[json.loads(r.content)['input'] for r in fixture.requests if r.url.path=='/api/embed']
    assert len(embeds[0])==17 and len(embeds[1])==1
    assert first.embedding_calls==second.embedding_calls==1 and first.input_size>second.input_size
    fixture.digest='fixture-2';third=asyncio.run(resolver.suggest('what can you do?'))
    assert third.fingerprint!=first.fingerprint
    assert len(json.loads(fixture.requests[-1].content)['input'])==17


def test_easy_deterministic_bypasses_semantics(tmp_path):
    fixture=Embeddings();a=make(tmp_path,fixture.resolver())
    for task in ['hallo','wer bist du?',"erstelle hello.txt mit dem Text 'Hallo Welt'"]:
        asyncio.run(a.run(task))
    assert not fixture.requests and a.resources.counters.model_calls==0
    assert a.semantic_usage['semantic_resolver_calls']==0


def test_capability_typo_grounded_no_worker(tmp_path):
    fixture=Embeddings();a=make(tmp_path,fixture.resolver());a.settings.permissions['write_file']='block'
    answer=asyncio.run(a.run('was kanst du?'))
    assert 'block' in answer and 'Kein Löschtool' in answer
    assert a.intent_resolution.intent_id==I.CAPABILITY_QUERY and a.resources.counters.model_calls==0
    assert a.semantic_usage['semantic_resolver_calls']==1 and a.semantic_usage['semantic_resolver_elapsed_ms']>=0


def test_semantic_resolver_does_not_create_worker_experience(tmp_path):
    fixture=Embeddings();s=Settings();s.semantic_resolver=fixture.resolver().settings
    p=ModelProfile('mock','mock','mock','demo');s.models=[p];store=ExperienceStore(tmp_path/'exp.sqlite3')
    before=hashlib.sha256(store.database.read_bytes()).hexdigest()
    a=RoutedAgent(Workspace(tmp_path),s,registry=ModelRegistry([p]),experience=store,semantic_resolver=fixture.resolver())
    asyncio.run(a.run('was kanst du?'))
    assert not a.experience_ids and a.resources.counters.model_calls==0
    assert hashlib.sha256(store.database.read_bytes()).hexdigest()==before


def test_wrong_semantic_create_without_safe_target_is_question(tmp_path):
    fixture=Embeddings(I.CREATE_FILE);a=make(tmp_path,fixture.resolver())
    answer=asyncio.run(a.run('Könntest du mir irgendwie helfen?'))
    assert a.intent_resolution.intent_id==I.CREATE_FILE and 'heißen' in answer
    assert not a.intent_resolution.slots.target_paths and a.resources.counters.file_writes==a.resources.counters.model_calls==0
    assert not list(tmp_path.iterdir())


def test_semantic_create_with_path_but_no_explicit_mutation_cannot_write(tmp_path):
    fixture=Embeddings(I.CREATE_FILE);a=make(tmp_path,fixture.resolver())
    answer=asyncio.run(a.run('hello.py ist ein Beispiel für meinen Vortrag'))
    assert 'ausdrücklich' in answer
    assert a.intent_resolution.slots.target_paths==('hello.py',)
    assert 'explicit_mutation_request' in a.intent_resolution.slots.missing_slots
    assert not (tmp_path/'hello.py').exists() and not a.resources.counters.model_calls


def test_unavailable_no_cloud_or_download(tmp_path):
    requests=[]
    def no_model(request):requests.append(request);return httpx.Response(200,json={'models':[]})
    config=SemanticSettings(enabled=True,model='fixture-embed:latest')
    resolver=SemanticIntentResolver(config,OllamaEmbeddingBackend(config,httpx.MockTransport(no_model)))
    a=make(tmp_path,resolver,[{'role':'assistant','content':'Deterministic fallback conversation'}])
    answer=asyncio.run(a.run('Eine allgemeine Frage'))
    assert 'fallback' in answer and all(r.url.path=='/api/tags' for r in requests)
    assert a.semantic_usage['semantic_resolver_calls']==0 and a.resources.counters.model_calls==1
    assert a.write_scope.read_only


def test_disabled_no_semantic_call(tmp_path):
    fixture=Embeddings();a=make(tmp_path,fixture.resolver(),[{'role':'assistant','content':'ordinary answer'}]);a.settings.semantic_resolver.enabled=False
    asyncio.run(a.run('was kanst du?'))
    assert not fixture.requests and a.resolver_chain['fallback_reason']=='SEMANTIC_DISABLED'


def test_redirect_is_not_followed():
    requests=[]
    def redirect(request):requests.append(request);return httpx.Response(302,headers={'location':'https://external.example/steal'})
    config=SemanticSettings(enabled=True,model='fixture-embed:latest')
    result=asyncio.run(SemanticIntentResolver(config,OllamaEmbeddingBackend(config,httpx.MockTransport(redirect))).suggest('anything'))
    assert result.match_status=='UNAVAILABLE' and len(requests)==1 and requests[0].url.host=='localhost'


def test_invalid_vectors_safe_unavailable():
    fixture=Embeddings();fixture.bad=True
    result=asyncio.run(fixture.resolver().suggest('anything'))
    assert result.match_status=='UNAVAILABLE' and result.embedding_calls==1


def test_heuristic_mutation_threshold_is_stricter():
    class Backend:
        embedding_calls=0;input_size=0
        async def identity(self):return 'fp'
        async def embed(self,texts):
            self.embedding_calls+=1
            anchors=[Embeddings().vector(i) for i in range(16)]
            index=list(DESCRIPTIONS).index(self.intent)
            query=[0.]*17;query[index]=.62;query[16]=math.sqrt(1-.62**2)
            return anchors+[query]
    backend=Backend();backend.intent=I.CREATE_FILE
    settings=SemanticSettings(enabled=True,model='fixture')
    assert asyncio.run(SemanticIntentResolver(settings,backend).suggest('anything')).match_status=='NO_MATCH'
    backend.intent=I.CAPABILITY_QUERY
    assert asyncio.run(SemanticIntentResolver(settings,backend).suggest('anything')).match_status=='HIGH_MATCH'


def test_ambiguous_result_questions_no_mutation(tmp_path):
    class Ambiguous:
        async def suggest(self,text):return IntentSuggestion(intent_id='CREATE_FILE',match_status='AMBIGUOUS',top_similarity=.8,second_similarity=.79,margin=.01,model='fixture',embedding_calls=1)
    a=make(tmp_path,Ambiguous());answer=asyncio.run(a.run('Vielleicht könntest du mir helfen'))
    assert 'mehrdeutig' in answer and not a.resources.counters.model_calls and not a.resources.counters.file_writes

@pytest.mark.parametrize('text',["Erstelle hello.txt mit dem Text Hallo Welt","Create hello.txt containing Hello World","Crée le fichier hello.txt avec le texte Bonjour le monde"])
def test_multilingual_lexical_fixtures_still_validate_paths(tmp_path,text):
    fixture=Embeddings(I.CREATE_FILE);a=make(tmp_path,fixture.resolver());asyncio.run(a.run(text))
    assert a.intent_resolution.intent_id==I.CREATE_FILE and a.intent_resolution.slots.target_paths==('hello.txt',)
    assert a.resources.counters.model_calls==0
    assert (tmp_path/'hello.txt').read_text() in {'Hallo Welt','Hello World','Bonjour le monde'}


def test_multilingual_semantic_space_mock_only():
    fixture=Embeddings(I.CAPABILITY_QUERY);resolver=fixture.resolver()
    for text in ['Was vermagst du?','Which functions are available to you?','Quelles sont tes capacités ?']:
        result=asyncio.run(resolver.suggest(text));assert result.intent_id==I.CAPABILITY_QUERY
    # This verifies plumbing, never real multilingual model quality.


def test_diagnostics_chain_slots_pending_no_secret(tmp_path):
    from alienape.security import register_provider_secret
    secret='V068_TEST_NOT_REAL_92A14';register_provider_secret(secret)
    session=Session();a=make(tmp_path,Embeddings().resolver(),session=session);a.settings.diagnostic_trace=True
    a.diagnostic_store=DiagnosticStore(tmp_path/'traces')
    asyncio.run(a.run('erstelle eine Python-Datei mit Hello World '+secret))
    assert secret not in session.task_state.pending_task.slots.generation_request
    second=make(tmp_path,Embeddings().resolver(),[tool_reply('write_file',{'path':'hello.py','content':'print(1)\n'})],session)
    second.settings.diagnostic_trace=True;second.diagnostic_store=a.diagnostic_store
    asyncio.run(second.run('hello.py'))
    asyncio.run(make(tmp_path,Embeddings().resolver(),session=session).run('hallo '+secret))
    # Export only actual trace data, redacted before disk.
    traces=a.diagnostic_store.records(None)
    assert traces
    import zipfile
    destination=tmp_path/'bundle.zip';a.diagnostic_store.export(destination,None)
    with zipfile.ZipFile(destination) as z:
        data=z.read('TRACE.jsonl').decode();assert secret not in data
        values=[json.loads(x) for x in data.splitlines()]
    assert all('resolver_chain' in t and 'task_slots' in t and 'pending_state' in t for t in values)
    assert any(e['action']=='created' for t in values for e in t['pending_state'])
    assert any(e['action']=='consumed' for t in values for e in t['pending_state'])


def test_controller_failure_before_worker_not_negative_experience(tmp_path):
    p=ModelProfile('mock','mock','mock','demo');s=Settings();s.models=[p];store=ExperienceStore(tmp_path/'exp.sqlite3')
    a=RoutedAgent(Workspace(tmp_path),s,registry=ModelRegistry([p]),experience=store)
    answer=asyncio.run(a.run('erstelle eine Python-Datei mit Hello World'))
    with store.connection() as db:assert db.execute('SELECT count(*) FROM model_experience').fetchone()[0]==0
    assert not a.experience_ids and 'heißen' in answer


def test_new_cpp_create_experience_is_generation_not_review(tmp_path):
    from test_workspace_discovery import make as routed_make
    from test_completion_recovery import response
    store=ExperienceStore(tmp_path/'exp.sqlite3')
    a,b=routed_make(tmp_path,responses=[response(json.dumps({'content':'#include <iostream>\nint main(){std::cout<<"Hello";}'}))],store=store)
    a.permission=yes;asyncio.run(a.run('schreib Hello World in C++ und speichere es in hello.cpp'))
    with store.connection() as db:row=dict(db.execute("SELECT * FROM model_experience WHERE source='live' AND model_calls>0").fetchone())
    assert row['task_category']=='CODE_GENERATION' and row['capability_v2']=='code_generation'
    assert row['protocol_v2']=='structured_json' and row['response_contract']=='FILE_CONTENT' and row['language_v2'] is None


def test_settings_semantic_ui_no_automatic_probe(tmp_path):
    from PyQt6.QtWidgets import QApplication
    from alienape.dialogs import SettingsDialog
    app=QApplication.instance() or QApplication([])
    d=SettingsDialog(Settings());assert d.semantic_widget.worker is None and not d.semantic_widget.enabled.isChecked()
    d.semantic_toggle.setChecked(True);d.semantic_widget.enabled.setChecked(True);d.semantic_widget.model.setCurrentText('fixture-embed:latest');d.save()
    assert d.settings.semantic_resolver.enabled and d.settings.semantic_resolver.model=='fixture-embed:latest'
    d.close()


def test_pending_completion_is_authoritative_and_skips_semantics(tmp_path):
    fixture=Embeddings(I.CAPABILITY_QUERY);session=Session()
    first=make(tmp_path,fixture.resolver(),session=session)
    asyncio.run(first.run('erstelle eine Python-Datei mit Hello World'))
    second=make(tmp_path,fixture.resolver(),[tool_reply('write_file',{'path':'hello.py','content':'print(1)\n'})],session)
    asyncio.run(second.run('hello.py'))
    assert (tmp_path/'hello.py').read_text()=='print(1)\n'
    assert not fixture.requests and second.semantic_usage['semantic_resolver_calls']==0
    assert second.intent_resolution.slots.resolution_source=='SESSION_PENDING'


def test_invalid_advisory_payload_cannot_change_scope(tmp_path):
    class Invalid:
        async def suggest(self,text):return {'intent_id':'CREATE_FILE','accepted':True,'target_paths':['invented.py']}
    a=make(tmp_path,Invalid(),[{'role':'assistant','content':'ordinary reply'}])
    asyncio.run(a.run('An ordinary undecided question'))
    assert a.write_scope.read_only and not (tmp_path/'invented.py').exists()
    assert a.resolver_chain['controller_decision']=='INVALID_ADVISORY_RESULT'


def test_resolver_cancellation_does_not_call_worker(tmp_path):
    class Wait:
        async def suggest(self,text):raise asyncio.CancelledError()
    a=make(tmp_path,Wait())
    with pytest.raises(asyncio.CancelledError):asyncio.run(a.run('Was vermagst du?'))
    assert a.resources.counters.model_calls==0 and a.resources.counters.file_writes==0


def test_settings_probe_cancels_before_dialog_closes(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtTest import QTest
    from alienape.dialogs import SettingsDialog
    from test_gui import until
    class Probe:
        def __init__(self,*args):pass
        async def health_check(self):await asyncio.sleep(10);return []
        def get_capabilities(self):return {}
    monkeypatch.setattr('alienape.semantic_ui.OllamaEmbeddingBackend',Probe)
    app=QApplication.instance() or QApplication([])
    d=SettingsDialog(Settings());d.show();d.semantic_widget.discover();d.reject()
    until(lambda:d.semantic_widget.worker is None)
    assert d.semantic_widget.closing
    d.close()


def test_semantic_usage_is_per_turn_on_reused_agent(tmp_path):
    a=make(tmp_path,Embeddings().resolver())
    asyncio.run(a.run('was kanst du?'));assert a.semantic_usage['semantic_resolver_calls']==1
    asyncio.run(a.run('hallo'));assert a.semantic_usage['semantic_resolver_calls']==0 and a.semantic_usage['semantic_resolver_elapsed_ms']==0


def test_controller_ambiguous_reference_cannot_be_overridden(tmp_path):
    session=Session();session.task_state.bind(Workspace(tmp_path))
    for p in ['a.py','b.py']:(tmp_path/p).write_text('x=1')
    session.task_state.last_inspected_files=['a.py','b.py']
    fixture=Embeddings(I.CREATE_FILE);a=make(tmp_path,fixture.resolver(),session=session)
    answer=asyncio.run(a.run('mach das'))
    assert 'Mehrdeutiger' in answer and not fixture.requests
    assert a.semantic_usage['semantic_resolver_calls']==a.resources.counters.model_calls==a.resources.counters.file_writes==0
    assert a.resolver_chain['fallback_reason']=='DETERMINISTIC_CLARIFICATION'


@pytest.mark.parametrize('seconds',[0.25,5.0,12.5,20.0])
def test_settings_semantic_timeout_persisted_and_loaded(tmp_path,seconds):
    from PyQt6.QtWidgets import QApplication
    from alienape.dialogs import SettingsDialog
    app=QApplication.instance() or QApplication([])
    settings=Settings();dialog=SettingsDialog(settings)
    dialog.show();dialog.semantic_toggle.setChecked(True);app.processEvents()
    spin=dialog.semantic_widget.timeout
    assert spin.isVisible() and spin.accessibleName()=='Timeout (Sekunden)'
    assert spin.value()==5.0 and spin.minimum()==0.25 and spin.maximum()==20.0
    spin.setValue(seconds);dialog.save()
    assert dialog.result()==dialog.DialogCode.Accepted
    path=tmp_path/'settings.toml';dialog.settings.save(path)
    reloaded=Settings.load(path);reopened=SettingsDialog(reloaded)
    assert reloaded.semantic_resolver.timeout_seconds==seconds
    assert reopened.semantic_widget.timeout.value()==seconds
    reopened.close();dialog.close()


def test_unmodified_high_precision_timeout_is_preserved(tmp_path):
    from PyQt6.QtWidgets import QApplication
    from alienape.dialogs import SettingsDialog
    app=QApplication.instance() or QApplication([])
    settings=Settings();settings.semantic_resolver.timeout_seconds=5.1234567891234567
    dialog=SettingsDialog(settings);dialog.save()
    path=tmp_path/'settings.toml';dialog.settings.save(path)
    assert Settings.load(path).semantic_resolver.timeout_seconds==settings.semantic_resolver.timeout_seconds
    dialog.close()


def test_timeout_typed_in_settings_is_saved(tmp_path):
    from PyQt6.QtWidgets import QApplication,QDialogButtonBox
    from PyQt6.QtTest import QTest
    app=QApplication.instance() or QApplication([])
    dialog=SettingsDialog_for_timeout_test()
    dialog.show();dialog.semantic_toggle.setChecked(True);app.processEvents()
    spin=dialog.semantic_widget.timeout;editor=spin.lineEdit()
    editor.setFocus();editor.selectAll()
    QTest.keyClicks(editor,'13'+spin.locale().decimalPoint()+'5')
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
    assert dialog.result()==dialog.DialogCode.Accepted
    path=tmp_path/'settings.toml';dialog.settings.save(path)
    assert Settings.load(path).semantic_resolver.timeout_seconds==13.5
    dialog.close()


def SettingsDialog_for_timeout_test():
    from alienape.dialogs import SettingsDialog
    return SettingsDialog(Settings())
