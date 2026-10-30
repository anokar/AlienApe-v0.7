import asyncio,json,time
from dataclasses import asdict
from types import SimpleNamespace
import pytest
from alienape.profiles import ModelProfile
from alienape.profile_v2 import ProfileV2,SCHEMA,Support
from alienape.onboarding import catalog_v2,plan_v2,eta,evidence_view,VERSION
from alienape.routing import ModelRegistry,ModelRouter,eligible
from alienape.config import Settings
from alienape.experience import ExperienceStore,ModelFingerprint,SuccessSignal as S
from alienape.training import TrainingRunner
from jsonschema import validate


def profile(name='model',tasks=('general_chat',),protocols=('plain_text',),mode='none',**kwargs):
    p=ModelProfile(name,name,'mock',name,tool_mode=mode,**kwargs)
    v=ProfileV2(migration_version='explicit-v2')
    for key in tasks:v.task_capabilities[key]='SUPPORTED'
    for key in protocols:v.protocol_capabilities[key]='SUPPORTED'
    p.profile_v2=asdict(v);return p


def test_legacy_roundtrip_and_conservative_subtypes(tmp_path):
    p=ModelProfile.from_dict({'id':'old','provider':'mock','model':'old','capabilities':['coding','tools'],'tool_mode':'native'})
    v=p.v2();validate(asdict(v),SCHEMA)
    assert v.task_capabilities['coding']=='SUPPORTED'
    assert v.task_capabilities['code_generation']=='UNKNOWN'
    assert v.task_capabilities['explanation']=='UNKNOWN'
    assert v.task_capabilities['code_review']=='UNKNOWN'
    assert v.protocol_capabilities['native_tool_calling']=='SUPPORTED'
    assert v.protocol_capabilities['file_proposal']=='UNKNOWN'
    assert ModelProfile.from_dict(asdict(p)).v2()==v
    s=Settings();s.models=[p];s.save(tmp_path/'config.toml');assert Settings.load(tmp_path/'config.toml').models[0].v2()==v


def test_functiongemma_audit_and_no_invented_observations():
    p=ModelProfile('f','F','ollama','functiongemma:270m',capabilities=['general','coding','tools'],tool_mode='native')
    v=p.v2();assert v.supports('tool_selection','native_tool_calling')
    assert not eligible(p,'Erkläre Python code')
    assert v.task_capabilities['coding']=='UNKNOWN'
    assert 'observed' not in asdict(v)


@pytest.mark.parametrize('task_state,protocol_state,expected',[('SUPPORTED','SUPPORTED',True),('UNKNOWN','SUPPORTED',False),('UNSUPPORTED','SUPPORTED',False),('SUPPORTED','UNKNOWN',False),('SUPPORTED','UNSUPPORTED',False)])
def test_task_and_protocol_intersection(task_state,protocol_state,expected):
    p=profile(tasks=(),protocols=())
    p.profile_v2['task_capabilities']['explanation']=task_state
    p.profile_v2['protocol_capabilities']['plain_text']=protocol_state
    assert eligible(p,'explain',task_capability='explanation',protocol='plain_text') is expected


def test_single_tool_specialist_cannot_bypass_eligibility():
    p=profile(tasks=['tool_selection'],protocols=['native_tool_calling'],mode='native')
    router=ModelRouter(ModelRegistry([p]),Settings().routing)
    with pytest.raises(ValueError):router.select_model('Erkläre Python code')
    assert router.select_model('call',task_capability='tool_selection',protocol='native_tool_calling').profile.id==p.id
    with pytest.raises(ValueError):router.select_model('x',task_capability='explanation',protocol='plain_text',state={'manual_model':p.id})


def test_create_generation_requires_declared_subtype():
    p=profile(tasks=['coding'],protocols=['native_tool_calling'],mode='native')
    assert not eligible(p,'Erstelle Datei hello.py mit Python Code')
    p.profile_v2['task_capabilities']['code_generation']='SUPPORTED'
    assert eligible(p,'Erstelle Datei hello.py mit Python Code')


def test_fixed_versioned_catalog_no_duplicates():
    tasks=catalog_v2();assert tasks==catalog_v2();assert len({t.id for t in tasks})==len(tasks)
    assert all(t.benchmark_catalog_version==VERSION and t.capability and t.protocol_required and t.verification_mechanism for t in tasks)
    assert {t.language for t in tasks}=={'de','en','fr'}
    assert not any('dialect' in t.language or t.language=='gsw' for t in tasks)


@pytest.mark.parametrize('specialist',['tool','coder','general'])
def test_tiers_strictly_expand_relevant_plan(specialist):
    p={'tool':profile(tasks=['tool_selection'],protocols=['native_tool_calling'],mode='native'),
       'coder':profile(tasks=['coding','code_generation','code_debugging','code_review'],protocols=['plain_text']),
       'general':profile()}[specialist]
    plans=[plan_v2(p,catalog_v2(),l) for l in ['lite','medium','hard']]
    assert 0<len(plans[0])<len(plans[1])<len(plans[2])
    assert set(t.id for t in plans[0])<set(t.id for t in plans[1])<set(t.id for t in plans[2])
    if specialist=='tool':assert all(t.category=='TOOL_USE' for t in plans[2]) and len(plans[0])==3
    if specialist=='coder':assert all(t.capability in {'coding','code_generation','code_debugging','code_review'} for t in plans[2])


def test_unknown_not_benchmarked():
    p=profile(tasks=(),protocols=())
    assert plan_v2(p,catalog_v2(),'hard')==[]


def test_small_language_sample_only_declared_multilingual():
    p=profile();p.profile_v2['languages']={'primary_language':'de','declared_languages':['de','en','fr'],'declared_multilingual':'SUPPORTED'}
    langs=lambda level:{t.language for t in plan_v2(p,catalog_v2(),level)}
    assert langs('lite')=={'de'};assert langs('medium')=={'de','en'};assert langs('hard')=={'de','en','fr'}
    p.profile_v2['languages']['declared_multilingual']='UNKNOWN';assert langs('hard')=={'de'}


def test_eta_zero_one_samples_unknown_then_measured():
    assert eta([],10) is None and eta([2],10) is None
    assert eta([2,4],10)==24 and eta([2,4],1)==0


def test_run_metadata_and_history_and_language_sources(tmp_path):
    p=profile();settings=Settings();store=ExperienceStore(tmp_path/'experience.sqlite3');reg=ModelRegistry([p])
    fp=ModelFingerprint.for_profile(p,{'model_version':'demo-1','backend_version':'mock-1'},settings.agent.context_budget);store.register(p,fp)
    live=store.record(fp.id,'GENERAL_SIMPLE',S.VERIFIED_SUCCESS,source='live',model_calls=0)
    with store.connection() as db:old=dict(db.execute('SELECT * FROM model_experience WHERE id=?',(live,)).fetchone())
    runner=TrainingRunner(reg,settings,store,[p.id],scope='medium')
    events=[];runner.observe=lambda kind,details,*a:events.append((kind,details))
    asyncio.run(runner.run())
    assert len(runner.results)==2 and all(r['source']=='benchmark' for r in runner.results)
    with store.connection() as db:
        assert dict(db.execute('SELECT * FROM model_experience WHERE id=?',(live,)).fetchone())==old
        rows=[dict(r) for r in db.execute("SELECT * FROM model_experience WHERE source='benchmark'")]
    assert all(r['benchmark_level']=='medium' and r['benchmark_catalog_version']==VERSION and r['benchmark_run_id']==runner.run_id and r['language_v2']=='de' for r in rows)
    view=evidence_view(store,fp.id)
    assert view['capabilities']['general_chat']['benchmark']['samples']==2
    assert view['languages']['de']['benchmark']['samples']==2
    assert view['legacy']['GENERAL_SIMPLE']['live']['samples']==1
    assert 'live' not in view['languages']['de'] # no invented historical language
    assert 'estimated_remaining_seconds' in [d for k,d in events if k=='BENCHMARK_PROGRESS'][-1]
    # Constructing next runner does not run anything or delete history.
    other=TrainingRunner(reg,settings,store,[p.id],scope='hard')
    assert other.status=='PENDING' and other.results==[]
    with store.connection() as db:assert db.execute('SELECT count(*) FROM model_experience').fetchone()[0]==3


def test_profile_no_observed_results_and_schema_validation():
    p=profile();p.profile_v2['reasoning']={'reasoning_supported':'SUPPORTED','supported_efforts':['low','high']}
    v=p.v2();validate(asdict(v),SCHEMA)
    with pytest.raises(ValueError):ProfileV2.from_dict({**asdict(v),'task_capabilities':{'made_up':'SUPPORTED'}})
    with pytest.raises(ValueError):ProfileV2.from_dict({**asdict(v),'languages':{'declared_languages':['gsw-CH']}})


def test_separate_capability_language_sources(tmp_path):
    p=profile();store=ExperienceStore(tmp_path/'e.sqlite3');fp=ModelFingerprint.for_profile(p);store.register(p,fp)
    for source,lang,signal in [('live','de',S.VERIFIED_SUCCESS),('benchmark','en',S.VERIFIED_FAILURE),('benchmark','de',S.UNKNOWN)]:
        store.record(fp.id,'GENERAL_SIMPLE',signal,source=source,capability_v2='general_chat',language_v2=lang)
    view=evidence_view(store,fp.id)
    assert view['languages']['de']['live']['verified_successes']==1
    assert view['languages']['en']['benchmark']['verified_failures']==1
    assert view['capabilities']['general_chat']['benchmark']['unknown']==1


def test_default_ui_medium_and_no_automatic_run(tmp_path):
    import os
    os.environ['QT_QPA_PLATFORM']='offscreen'
    from PyQt6.QtWidgets import QApplication
    from alienape.experience_ui import BenchmarkDialog
    from alienape.model_dialogs import ProfileDialog
    app=QApplication.instance() or QApplication([])
    d=BenchmarkDialog(ModelRegistry([profile()]));assert d.scope.currentData()=='medium';assert d.selected_models()==[]
    p=ProfileDialog(profile());assert p.profile_tabs.count()==4
    assert all(c.currentText() in Support._value2member_map_ for c in p.v2_selectors.values())
    p.languages.setText('de, en');p.save();assert p.profile.profile_v2['languages']['declared_languages']==['de','en']
    d.close();p.close()


def test_timing_in_chat_real_direct_response_no_fabricated_splits(tmp_path):
    import os
    os.environ['QT_QPA_PLATFORM']='offscreen'
    from PyQt6.QtWidgets import QApplication
    from alienape.ui import MainWindow
    from alienape.agent import Agent
    from alienape.backends import MockBackend
    from alienape.tools import Workspace
    app=QApplication.instance() or QApplication([])
    window=MainWindow(Settings(),tmp_path/'data')
    agent=Agent(MockBackend(),Workspace(tmp_path),Settings())
    result=asyncio.run(agent.run('hallo'))
    worker=SimpleNamespace(agent=agent,accepted_at=time.monotonic()-0.02)
    timing=window.response_timing(worker)
    assert timing['model_calls']==0 and timing['total_seconds']>=0.02
    assert set(timing)=={'total_seconds','model_calls'}
    window.append_chat('AlienApe',result,timing)
    assert '0.02 s' in window.chat.toPlainText()
    assert window.settings.diagnostic_trace is False
    assert not (tmp_path/'data/diagnostics').exists()
    window.close()


def test_protocol_declaration_does_not_enable_disabled_transport():
    p=profile(tasks=['tool_selection'],protocols=['native_tool_calling'],mode='none')
    assert not eligible(p,'call',task_capability='tool_selection',protocol='native_tool_calling')


def test_escalation_does_not_bypass_capabilities():
    low=profile('low',tasks=['coding','code_debugging'],resource_class='low')
    high=profile('high',tasks=['reasoning'],resource_class='high')
    router=ModelRouter(ModelRegistry([low,high]),Settings().routing)
    assert router.escalate(low,{'low'},'Debug Python') is None
    high.profile_v2['task_capabilities']['code_debugging']='SUPPORTED'
    assert router.escalate(low,{'low'},'Debug Python').id=='high'


def test_legacy_single_profile_unknown_cannot_qualify_as_general():
    p=ModelProfile('one','one','mock','one',capabilities=['coding'])
    with pytest.raises(ValueError):ModelRouter(ModelRegistry([p]),Settings().routing).select_model('Hello')
