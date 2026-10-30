import asyncio, json
from alienape.evaluation_v069 import assess_completion,protocol_probe,semantic_evaluation
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.profiles import ModelProfile
from alienape.routing import ModelRegistry
from alienape.semantic_intent import SemanticSettings,DESCRIPTIONS

def test_protocol_independent_quality():
    result=assess_completion('```python\nprint("Hello World")\n```','hello.py')
    assert not result['valid_file_proposal'] and result['code_generation_quality']=='plausible'
    envelope=json.dumps({'payload':'ok','status':'ok','proposed_files':[{'path':'hello.py','content':'print("Hello World")'}]})
    result=assess_completion(envelope,'hello.py')
    assert result['valid_file_proposal'] and result['correct_target_path'] and not result['unexpected_extra_text']
    assert not assess_completion(envelope,'other.py')['correct_target_path']

def test_probe_multiple_models_explicit_and_bounded():
    class Backend(MockBackend):
        async def list_models(self):return ['a','b']
        async def model_identity(self,model):return {'digest':model}
        async def chat_with_mode(self,messages,tools=None,model='',mode='native'):
            self.calls.append(messages)
            return {'content':'```python\nprint("hi")\n```'}
    backend=Backend(); pool=type('Pool',(),{'get':lambda self,p:backend})()
    profiles=[ModelProfile(x,x,'mock',x,capabilities=['coding']) for x in ('a','b')]
    registry=ModelRegistry(profiles)
    out=asyncio.run(protocol_probe(registry,Settings(),['a','b'],pool))
    assert len(out['results'])==6 and len(backend.calls)==6
    assert all(r['model_calls']==1 and not r['valid_file_proposal'] for r in out['results'])
    assert len({r['model_fingerprint'] for r in out['results']})==2

def test_semantic_eval_keeps_settings_and_reports_scores():
    class Backend:
        embedding_calls=0;input_size=0
        async def identity(self):return 'fingerprint'
        async def embed(self,texts):
            self.embedding_calls+=1;self.input_size+=sum(map(len,texts))
            return [[1.,0.] if i==0 else [0.,1.] for i in range(len(texts))]
    settings=SemanticSettings(enabled=False,model='test',confidence_threshold=.45,ambiguity_margin=.1,timeout_seconds=20)
    fixture={'cases':[{'text':'test','expected':'GENERAL_CHAT'}]}
    result=asyncio.run(semantic_evaluation(settings,fixture,Backend()))['results'][0]
    assert settings.enabled is False and settings.confidence_threshold==.45 and settings.timeout_seconds==20
    assert result['top_intent'] and result['second_intent'] and result['configured_threshold']==.45
