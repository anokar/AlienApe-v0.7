import asyncio,json
import pytest
from alienape.agent import Agent,Session
from alienape.backends import MockBackend,tool_reply
from alienape.config import Settings
from alienape.intent_resolution import DeterministicIntentResolver,IntentID as I
from alienape.semantic_intent import SemanticSettings
from alienape.task_slots import ContentMode as M
from alienape.tools import Workspace
from test_semantic_intent import Embeddings
from test_diagnostics import enable,bundle

async def permit(*_):return True


def test_observed_file_selector_pending_then_create(tmp_path):
    prompt='erstele eine .py datei die ein file auswählen kann'
    session=Session();initial=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session)
    answer=asyncio.run(initial.run(prompt));s=initial.intent_resolution.slots
    assert s.intent_id==I.CREATE_FILE and s.content_mode==M.GENERATE
    assert s.language_hint=='python' and s.generation_request=='Python-Datei, die eine Datei auswählen kann'
    assert s.missing_slots==('target_path',) and 'heißen' in answer
    assert session.task_state.pending_task and initial.resources.counters.model_calls==0
    assert not (tmp_path/'hm.py').exists()
    backend=MockBackend([tool_reply('write_file',{'path':'hm.py','content':'from tkinter import filedialog\nfiledialog.askopenfilename()\n'})],delay=0)
    next_agent=Agent(backend,Workspace(tmp_path),Settings(),session=session,permission=permit)
    asyncio.run(next_agent.run('hm.py'))
    assert next_agent.intent_resolution.intent_id==I.CREATE_FILE
    assert next_agent.intent_resolution.slots.resolution_source=='SESSION_PENDING'
    assert next_agent.intent_resolution.slots.generation_request=='Python-Datei, die eine Datei auswählen kann'
    assert not next_agent.required_input_paths and 'askopenfilename' in (tmp_path/'hm.py').read_text()
    assert 'Datei auswählen kann' in json.dumps(backend.calls,ensure_ascii=False)
    assert next_agent.resources.counters.file_reads==0


@pytest.mark.parametrize('prompt',['eine datei erstelen die ein ordner öffnet in python','erstelle eine Python Datei die einen Ordner öffnet','erstelle eine Datei die einen folder öffnen kann'])
def test_folder_as_program_behavior_is_file_not_directory(prompt):
    r=DeterministicIntentResolver().resolve(prompt)
    assert r.intent_id==I.CREATE_FILE and r.slots.content_mode==M.GENERATE
    assert r.slots.generation_request==prompt and r.slots.missing_slots==('target_path',)


@pytest.mark.parametrize('prompt,name',[('erstelle einen Ordner test','test'),('make a directory assets','assets')])
def test_requested_folder_remains_directory(tmp_path,prompt,name):
    r=DeterministicIntentResolver().resolve(prompt)
    assert r.intent_id==I.CREATE_DIRECTORY and r.slots.target_paths==(name,)
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=permit)
    asyncio.run(a.run(prompt));assert (tmp_path/name).is_dir() and a.resources.counters.model_calls==0


def test_directory_word_alone_never_creates_blank_directory(tmp_path):
    r=DeterministicIntentResolver().resolve('erstelle ein Programm für Ordner')
    assert r.intent_id!=I.CREATE_DIRECTORY or r.ambiguity_reason


def test_html_field_fixture_is_safe_file_clarification(tmp_path):
    prompt='kanst du eine html seite erstelen mit einem button in der mite?'
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    answer=asyncio.run(a.run(prompt));s=a.intent_resolution.slots
    assert s.intent_id==I.CREATE_FILE and s.content_mode==M.GENERATE
    assert s.language_hint=='html' and s.generation_request==prompt and s.missing_slots==('target_path',)
    assert 'heißen' in answer and a.resources.counters.model_calls==0
    assert a.session.task_state.pending_task and not list(tmp_path.iterdir())


@pytest.mark.parametrize('intent,mutation',[(I.CAPABILITY_QUERY,False),(I.CREATE_FILE,True)])
def test_actual_semantic_thresholds_recorded_in_trace(tmp_path,intent,mutation):
    config=SemanticSettings(enabled=True,model='fixture-embed:latest',confidence_threshold=.45,ambiguity_margin=.10,timeout_seconds=20.0)
    fixture=Embeddings(intent);resolver=fixture.resolver(config)
    settings=Settings();settings.semantic_resolver=config
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),settings,semantic_resolver=resolver)
    store=enable(a,tmp_path)
    asyncio.run(a.run('was kanst du?' if not mutation else 'Könntest du mir irgendwie helfen?'))
    trace=bundle(store,tmp_path)[0][0];data=trace['resolver_chain']['suggestion']
    assert data['configured_confidence_threshold']==.45 and data['configured_ambiguity_margin']==.10
    assert data['mutation_threshold_applied'] is mutation
    assert data['effective_confidence_threshold']==pytest.approx(.55 if mutation else .45)
    assert data['effective_ambiguity_margin']==pytest.approx(.15 if mutation else .10)
    assert data['top_similarity'] is not None and data['margin'] is not None


def test_unavailable_semantics_reports_config_without_invented_decision():
    config=SemanticSettings(enabled=True,model='missing',confidence_threshold=.45,ambiguity_margin=.10)
    import httpx
    from alienape.semantic_intent import SemanticIntentResolver,OllamaEmbeddingBackend
    backend=OllamaEmbeddingBackend(config,httpx.MockTransport(lambda request:httpx.Response(200,json={'models':[]})))
    result=asyncio.run(SemanticIntentResolver(config,backend).suggest('hello'))
    assert result.match_status=='UNAVAILABLE' and result.configured_confidence_threshold==.45
    assert result.configured_ambiguity_margin==.10 and result.effective_confidence_threshold is None
    assert result.effective_ambiguity_margin is None and result.mutation_threshold_applied is None


def test_reset_is_explicit_saved_only_and_current_values_visible(tmp_path):
    from PyQt6.QtWidgets import QApplication
    from alienape.dialogs import SettingsDialog
    app=QApplication.instance() or QApplication([])
    settings=Settings();settings.semantic_resolver.confidence_threshold=.45
    settings.semantic_resolver.ambiguity_margin=.10;settings.semantic_resolver.timeout_seconds=20.0
    path=tmp_path/'config.toml';settings.save(path);before=path.read_bytes()
    d=SettingsDialog(Settings.load(path));d.show();d.semantic_toggle.setChecked(True);app.processEvents()
    w=d.semantic_widget
    assert w.threshold.value()==.45 and w.margin.value()==.10 and w.timeout.value()==20.0
    assert 'Gespeichert' in w.saved_values.text() and '20' in w.saved_values.text() and w.reset.isVisible()
    w.reset.click();assert (w.threshold.value(),w.margin.value(),w.timeout.value())==(.55,.10,5.0)
    assert path.read_bytes()==before and Settings.load(path).semantic_resolver.confidence_threshold==.45
    d.reject();d2=SettingsDialog(Settings.load(path));assert d2.semantic_widget.threshold.value()==.45
    assert d2.semantic_widget.timeout.value()==20.0
    d2.semantic_widget.reset_defaults();d2.save();d2.settings.save(path)
    saved=Settings.load(path).semantic_resolver
    assert (saved.confidence_threshold,saved.ambiguity_margin,saved.timeout_seconds)==(.55,.10,5.0)
    d3=SettingsDialog(Settings.load(path));assert d3.semantic_widget.timeout.value()==5.0
    d.close();d2.close();d3.close()
