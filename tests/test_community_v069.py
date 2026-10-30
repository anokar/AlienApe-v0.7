import hashlib,json
from datetime import datetime,timezone
import pytest
from alienape.community_evidence import CARD_SCHEMA,card_state,canonical_payload,TrustState,community_evidence_for_routing
from alienape.config import Settings
from alienape.experience import ExperienceStore, ModelFingerprint, SuccessSignal
from alienape.routing import ModelRouter,ModelRegistry
from test_routing import profile
from PyQt6.QtWidgets import QApplication
from alienape.ui import MainWindow


def card():
    c={'schema_version':'1.0.0','card_id':'card1','model_fingerprint':'a'*64,'model_name':'model:7b','model_digest':'b'*64,'quantization':'Q4','backend_identity':'ollama-v1','capability_v2':'code_generation','protocol_v2':'file_proposal','language_v2':'de','sample_count':1,'verified_successes':1,'verified_failures':0,'unknown_results':0,'created_at':'2025-01-01T00:00:00Z','stale_after':'2099-01-01T00:00:00Z','issuer_id':'issuer','signature_scheme':'none','signature':''}
    c['payload_hash']=hashlib.sha256(canonical_payload(c)).hexdigest();return c

def test_card_fail_closed():
    c=card();assert card_state(c,'a'*64)==TrustState.UNVERIFIED
    assert not community_evidence_for_routing(c)
    assert card_state(c,'b'*64)==TrustState.FINGERPRINT_MISMATCH
    assert card_state(c,'a'*64,datetime(2100,1,1,tzinfo=timezone.utc))==TrustState.STALE
    for key in ('prompt','chat','source_code','workspace_path','username','credentials','secret','tool_call'):
        bad={**c,key:'__import__("os").system("anything")'}
        assert card_state(bad,'a'*64)==TrustState.UNVERIFIED
    assert card_state({**c,'schema_version':'2.0.0'},'a'*64)==TrustState.UNSUPPORTED_SCHEMA
    assert card_state({**c,'sample_count':-1},'a'*64)==TrustState.UNVERIFIED
    assert not community_evidence_for_routing(object())

def test_policy_filter_and_data_remains(tmp_path):
    s=Settings();p=profile('coder','coding','medium');s.models=[p];store=ExperienceStore(tmp_path/'e.db')
    fp=ModelFingerprint.for_profile(p);store.register(p,fp)
    store.record(fp.id,'PYTHON_DEBUG',SuccessSignal.VERIFIED_SUCCESS,source='live')
    store.record(fp.id,'PYTHON_DEBUG',SuccessSignal.VERIFIED_FAILURE,source='benchmark',suite_version=3)
    assert store.summary(fp.id,'PYTHON_DEBUG')['sample_count']==2
    assert store.summary(fp.id,'PYTHON_DEBUG',include_local=False)['sample_count']==1
    assert store.summary(fp.id,'PYTHON_DEBUG')['sample_count']==2
    r=ModelRouter(ModelRegistry([p]),s.routing,store,{p.id:fp.id},source_policy=s.experience_sources)
    before=r.select_model('Debug Python').profile.id;s.experience_sources.community_enabled=True
    assert r.select_model('Debug Python').profile.id==before
    s.experience_sources.local_live_enabled=False;assert r.select_model('Debug Python').profile.id==before
    assert store.summary(fp.id,'PYTHON_DEBUG')['sample_count']==2

def test_ui_save_reload(tmp_path):
    app=QApplication.instance() or QApplication([])
    settings=Settings();assert settings.experience_sources.local_live_enabled and not settings.experience_sources.community_enabled
    window=MainWindow(settings,tmp_path/'data')
    # Locate the actual Experience panel via its source controls.
    from alienape.experience_ui import ExperiencePanel
    panel=window.findChild(ExperiencePanel)
    assert panel is not None
    panel.local_source.setChecked(False);panel.community_source.setChecked(True);panel.save_routing()
    reloaded=Settings.load(tmp_path/'data'/'config.toml')
    assert not reloaded.experience_sources.local_live_enabled and reloaded.experience_sources.community_enabled
    window.close()
