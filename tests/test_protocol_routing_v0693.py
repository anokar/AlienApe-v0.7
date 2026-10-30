from alienape.config import Settings
from alienape.experience import ExperienceStore,ModelFingerprint,SuccessSignal
from alienape.routing import ModelRegistry,ModelRouter
from test_routing import profile

def test_file_content_evidence_does_not_punish_chat(tmp_path):
    settings=Settings();settings.routing.min_samples_for_experience=2
    a=profile('a','coding','low',tool_mode='none');b=profile('b','coding','medium',tool_mode='none')
    store=ExperienceStore(tmp_path/'e.db');fps={}
    for p in (a,b):
        f=ModelFingerprint.for_profile(p);store.register(p,f);fps[p.id]=f.id
    for _ in range(2):
        store.record(fps['a'],'PYTHON_CODING',SuccessSignal.VERIFIED_FAILURE,response_contract='FILE_CONTENT',failure_type='FORMAT_FAILURE',information_complete=True,protocol_status='VERIFIED_FAILURE')
        store.record(fps['b'],'PYTHON_CODING',SuccessSignal.VERIFIED_SUCCESS,response_contract='FILE_CONTENT',failure_type='SUCCESS',information_complete=True,protocol_status='VERIFIED_SUCCESS')
    router=ModelRouter(ModelRegistry([a,b]),settings.routing,store,fps)
    assert router.select_model('Write Python',state={'response_contract':'FILE_CONTENT'}).profile.id=='b'
    assert router.select_model('Write Python').profile.id=='a'

def test_no_protocol_evidence_preserves_fallback(tmp_path):
    settings=Settings();a=profile('a','coding','low',tool_mode='none');b=profile('b','coding','medium',tool_mode='none')
    store=ExperienceStore(tmp_path/'e.db');router=ModelRouter(ModelRegistry([a,b]),settings.routing,store,{})
    assert router.select_model('Write Python',state={'response_contract':'FILE_CONTENT'}).profile.id=='a'
