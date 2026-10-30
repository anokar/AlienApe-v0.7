"""Versioned declarations only. Observations belong to Experience, never this object."""
from enum import StrEnum
from dataclasses import dataclass, field, asdict

class Support(StrEnum):
    SUPPORTED='SUPPORTED'
    UNSUPPORTED='UNSUPPORTED'
    UNKNOWN='UNKNOWN'

TASKS=('general_chat','explanation','summarization','coding','code_generation','code_debugging','code_review','reasoning','tool_selection','information_synthesis','long_context_analysis')
PROTOCOLS=('plain_text','native_tool_calling','json_fallback','structured_json','file_proposal','analysis_result','tool_action')
MODALITIES=('text_input','image_input','audio_input','text_output','image_output')

def states(names):return dict.fromkeys(names,'UNKNOWN')

@dataclass
class ProfileV2:
    schema_version:int=2
    migration_version:str='legacy-v1-to-v2.1'
    task_capabilities:dict=field(default_factory=lambda:states(TASKS))
    protocol_capabilities:dict=field(default_factory=lambda:states(PROTOCOLS))
    modalities:dict=field(default_factory=lambda:states(MODALITIES))
    languages:dict=field(default_factory=lambda:{'declared_multilingual':'UNKNOWN','declared_languages':[],'primary_language':'de'})
    reasoning:dict=field(default_factory=lambda:{'reasoning_supported':'UNKNOWN','supported_efforts':[]})
    limits:dict=field(default_factory=dict)
    resources:dict=field(default_factory=dict)
    identity:dict=field(default_factory=dict)

    @classmethod
    def from_dict(cls,raw):
        obj=cls(**raw)
        for key,names in [('task_capabilities',TASKS),('protocol_capabilities',PROTOCOLS),('modalities',MODALITIES)]:
            setattr(obj,key,{**states(names),**getattr(obj,key)})
        obj.validate();return obj

    def validate(self):
        if self.schema_version!=2:raise ValueError('Model Profile schema_version muss 2 sein.')
        for values,names in [(self.task_capabilities,TASKS),(self.protocol_capabilities,PROTOCOLS),(self.modalities,MODALITIES)]:
            if set(values)!=set(names) or any(v not in Support._value2member_map_ for v in values.values()):raise ValueError('Ungültige V2 Capability / Support-Zustand.')
        for key,allowed in [('languages',{'declared_multilingual','declared_languages','primary_language'}),('reasoning',{'reasoning_supported','supported_efforts'}),('limits',{'context_tokens','max_output_tokens'}),('resources',{'resource_class','local_memory_estimate'}),('identity',{'quantization','parameter_class','classification'})]:
            if not isinstance(getattr(self,key),dict) or not set(getattr(self,key))<=allowed:raise ValueError('Ungültiges V2 Metadatenfeld: '+key)
        import re
        langs=self.languages.get('declared_languages',[])
        if self.languages.get('declared_multilingual','UNKNOWN') not in Support._value2member_map_ or not isinstance(langs,list) or len(langs)>16 or any(not isinstance(x,str) or not re.fullmatch('[a-z]{2,3}',x) for x in langs):raise ValueError('Sprachen: höchstens 16 ISO-Sprachcodes.')
        if not re.fullmatch('[a-z]{2,3}',self.languages.get('primary_language','de')):raise ValueError('Ungültige Primärsprache.')
        if self.reasoning.get('reasoning_supported','UNKNOWN') not in Support._value2member_map_ or not isinstance(self.reasoning.get('supported_efforts',[]),list) or any(x not in {'minimal','low','medium','high','xhigh'} for x in self.reasoning.get('supported_efforts',[])):raise ValueError('Ungültige Reasoning-Angabe.')
        for v in self.limits.values():
            if type(v) is not int or v<0:raise ValueError('Tokenlimits müssen positive Ganzzahlen oder 0 (unbekannt) sein.')
        if self.resources.get('resource_class','medium') not in {'low','medium','high'}:raise ValueError('Ungültige Ressourcenklasse.')
        for k,v in self.identity.items():
            if not isinstance(v,str) or len(v)>120:raise ValueError('Ungültige Identitätsangabe.')
        if self.identity.get('classification','local') not in {'local','cloud','unknown'}:raise ValueError('Ungültige Modellklassifikation.')
        if 'local_memory_estimate' in self.resources and (type(self.resources['local_memory_estimate']) is not int or self.resources['local_memory_estimate']<0):raise ValueError('Speicherschätzung in Bytes oder 0.')
        if not isinstance(self.migration_version,str) or len(self.migration_version)>80:raise ValueError('Ungültige Migrationsversion.')

    def supports(self,task=None,protocol=None):
        return (not task or self.task_capabilities.get(task)=='SUPPORTED') and (not protocol or self.protocol_capabilities.get(protocol)=='SUPPORTED')


def migrate(profile):
    v=ProfileV2();c=set(profile.capabilities)
    mapping={'general':'general_chat','chat':'general_chat','coding':'coding','debugging':'code_debugging','code_review':'code_review','summarization':'summarization','deep_reasoning':'reasoning','planning':'reasoning','reasoning':'reasoning','tools':'tool_selection','tool_use':'tool_selection'}
    for old,new in mapping.items():
        if old in c:v.task_capabilities[new]='SUPPORTED'
    # Transport modes declare the configured wire protocol, not successful execution.
    v.modalities['text_input']='SUPPORTED'
    v.protocol_capabilities['native_tool_calling' if profile.tool_mode=='native' else 'json_fallback' if profile.tool_mode=='json_fallback' else 'plain_text']='SUPPORTED'
    if profile.tool_mode!='none':v.protocol_capabilities['tool_action']='SUPPORTED'
    if c&{'general','chat','coding'}:v.protocol_capabilities['plain_text']='SUPPORTED';v.modalities['text_output']='SUPPORTED'
    # Documented function-specialist audit, never a global model quality judgment.
    if profile.model_name.casefold().split(':')[0].split('/')[-1]=='functiongemma':
        v.task_capabilities['coding']='UNKNOWN'
        v.task_capabilities['general_chat']='UNKNOWN'
        v.task_capabilities['tool_selection']='SUPPORTED'
        v.protocol_capabilities['plain_text']='UNKNOWN'
        v.modalities['text_output']='UNKNOWN'
        v.migration_version='legacy-v1-to-v2.1-function-specialist-audit'
    v.limits={'context_tokens':profile.context_length}
    v.resources={'resource_class':profile.resource_class}
    v.identity={'classification':'local'}
    return v

STATE={'enum':list(Support._value2member_map_)}
SCHEMA={'$schema':'https://json-schema.org/draft/2020-12/schema','title':'AlienApe Model Profile V2 declarations','type':'object','additionalProperties':False,'required':list(asdict(ProfileV2())), 'properties':{
    'schema_version':{'const':2},'migration_version':{'type':'string','maxLength':80},
    **{key:{'type':'object','additionalProperties':False,'required':list(names),'properties':{n:STATE for n in names}} for key,names in [('task_capabilities',TASKS),('protocol_capabilities',PROTOCOLS),('modalities',MODALITIES)]},
    'languages':{'type':'object','additionalProperties':False,'properties':{'declared_multilingual':STATE,'declared_languages':{'type':'array','maxItems':16,'items':{'type':'string','pattern':'^[a-z]{2,3}$'}},'primary_language':{'type':'string','pattern':'^[a-z]{2,3}$'}}},
    'reasoning':{'type':'object','additionalProperties':False,'properties':{'reasoning_supported':STATE,'supported_efforts':{'type':'array','items':{'enum':['minimal','low','medium','high','xhigh']}}}},
    'limits':{'type':'object','additionalProperties':False,'properties':{k:{'type':'integer','minimum':0} for k in ['context_tokens','max_output_tokens']}},
    'resources':{'type':'object','additionalProperties':False,'properties':{'resource_class':{'enum':['low','medium','high']},'local_memory_estimate':{'type':'integer','minimum':0}}},
    'identity':{'type':'object','additionalProperties':False,'properties':{'quantization':{'type':'string','maxLength':120},'parameter_class':{'type':'string','maxLength':120},'classification':{'enum':['local','cloud','unknown']}}}}}
