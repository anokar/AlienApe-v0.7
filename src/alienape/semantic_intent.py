"""Advisory local embedding classification. No tools, paths, scopes or worker selection."""
from dataclasses import dataclass,field,asdict
import asyncio,hashlib,json,math,time
import httpx
from .intent_resolution import IntentID as I
from .security import redactor,transport_redactor

CATALOG_VERSION='1.0.0'
DESCRIPTIONS={
 I.GENERAL_CHAT:'Have a general conversation or answer a question without workspace changes.',
 I.GREETING:'Greet AlienApe or say hello.',
 I.THANKS:'Thank AlienApe for its help.',
 I.GOODBYE:'Say goodbye or end a conversation.',
 I.IDENTITY_QUERY:'Ask who AlienApe is or what its identity is.',
 I.CAPABILITY_QUERY:'Ask what AlienApe can do or is permitted to do.',
 I.EXPLAIN_CODE:'Explain programming code or how to write code without changing workspace files.',
 I.READ_FILE:'Read and display the contents of an explicitly named workspace file.',
 I.FIND_FILE:'Find an existing file or implementation in the workspace.',
 I.CREATE_FILE:'Create a new workspace file, optionally generating or providing its contents.',
 I.MODIFY_FILE:'Change the contents of an existing explicitly identified workspace file.',
 I.CREATE_DIRECTORY:'Create a named directory in the workspace.',
 I.CREATE_PROJECT:'Create a new project from a project template.',
 I.ANALYZE_FILE:'Analyze the contents or behavior of an existing workspace file.',
 I.VERIFY_FILE:'Verify or test an explicitly identified existing file for errors.',
 I.VERIFY_PROJECT:'Verify or test the existing workspace project for errors.',
}
MUTATING={I.CREATE_FILE,I.MODIFY_FILE,I.CREATE_DIRECTORY,I.CREATE_PROJECT}

@dataclass
class SemanticSettings:
    enabled:bool=False
    backend:str='ollama'
    base_url:str='http://localhost:11434'
    model:str=''
    confidence_threshold:float=0.55
    ambiguity_margin:float=0.10
    timeout_seconds:float=5.0
    declared_multilingual:bool=False

    def validate(self):
        if type(self.enabled) is not bool or type(self.declared_multilingual) is not bool or self.backend!='ollama':raise ValueError('Semantic Resolver: boolescher Schalter und lokales Ollama erforderlich.')
        if not isinstance(self.model,str) or len(self.model)>200 or any(ord(c)<32 for c in self.model):raise ValueError('Ungültiger Embedding-Modellname.')
        for value in [self.confidence_threshold,self.ambiguity_margin]:
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1:raise ValueError('Semantische Schwellen müssen zwischen 0 und 1 liegen.')
        if isinstance(self.timeout_seconds,bool) or not isinstance(self.timeout_seconds,(int,float)) or not 0.25<=self.timeout_seconds<=20:raise ValueError('Semantic Timeout: 0.25 bis 20 Sekunden.')
        # Reuse the production endpoint restriction without sending a request.
        from .backends import OllamaBackend
        OllamaBackend(self.base_url)

@dataclass(frozen=True)
class IntentSuggestion:
    catalog_version:str=CATALOG_VERSION
    intent_id:str|None=None
    match_status:str='UNAVAILABLE'
    top_similarity:float|None=None
    second_similarity:float|None=None
    margin:float|None=None
    threshold_result:bool=False
    accepted:bool=False
    top_matches:tuple=()
    model:str=''
    fingerprint:str=''
    reason:str=''
    embedding_calls:int=0
    input_size:int=0
    elapsed_ms:float=0
    configured_confidence_threshold:float|None=None
    effective_confidence_threshold:float|None=None
    configured_ambiguity_margin:float|None=None
    effective_ambiguity_margin:float|None=None
    mutation_threshold_applied:bool|None=None

class ResolverUnavailable(ValueError):pass

class OllamaEmbeddingBackend:
    """Dedicated allowlisted embedding/metadata transport; never a generative endpoint."""
    def __init__(self,settings,transport=None):
        settings.validate();self.settings=settings;self.transport=transport
        from .backends import OllamaBackend
        self.base_url=OllamaBackend(settings.base_url).base_url.removesuffix('/v1')
        self.embedding_calls=0;self.input_size=0;self.metadata={}

    async def _json(self,method,path,body=None):
        if (method,path) not in {('GET','/api/tags'),('POST','/api/show'),('POST','/api/embed')}:raise PermissionError('Embedding-Requestziel nicht freigegeben.')
        kwargs={'json':transport_redactor.clean(body)} if body is not None else {}
        async with httpx.AsyncClient(timeout=self.settings.timeout_seconds,transport=self.transport,trust_env=False,follow_redirects=False) as client:
            async with client.stream(method,self.base_url+path,**kwargs) as response:
                response.raise_for_status();data=bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>2*1024*1024:raise ResolverUnavailable('Embedding response exceeds size bound.')
        result=json.loads(data)
        if not isinstance(result,dict) or result.get('error'):raise ResolverUnavailable('Semantic resolver model is not available.')
        return result

    async def installed(self):
        result=await self._json('GET','/api/tags')
        values=result.get('models',[])
        if not isinstance(values,list):raise ResolverUnavailable('Invalid local model listing.')
        return [v for v in values[:64] if isinstance(v,dict) and isinstance(v.get('name'),str)]

    async def identity(self):
        model=self.settings.model
        if not model:raise ResolverUnavailable('Semantic resolver model is not available.')
        entries=await self.installed()
        entry=next((e for e in entries if e['name'] in {model,model+':latest'}),None)
        if entry is None:raise ResolverUnavailable('Semantic resolver model is not available.')
        # No prompt/model outputs are retained here; only capability metadata.
        key=(entry['name'],entry.get('digest'))
        if key not in self.metadata:
            info=await self._json('POST','/api/show',{'model':entry['name']})
            caps=info.get('capabilities',[])
            if not isinstance(caps,list) or 'embedding' not in caps:raise ResolverUnavailable('Semantic resolver model is not available: configured model does not declare embedding support.')
            self.metadata={key:True}
        fp=hashlib.sha256(json.dumps({'backend':'ollama-embedding','endpoint':self.base_url,'model':entry['name'],'digest':entry.get('digest'),'catalog':CATALOG_VERSION},sort_keys=True).encode()).hexdigest()
        return fp if entry.get("digest") else ""

    async def embed(self,texts):
        if not 1<=len(texts)<=len(DESCRIPTIONS)+1:raise ResolverUnavailable('Embedding input count exceeds bound.')
        texts=transport_redactor.clean(texts)
        if any(not isinstance(t,str) or len(t.encode())>8192 for t in texts):raise ResolverUnavailable('Embedding input exceeds bound.')
        self.embedding_calls+=1;self.input_size+=sum(len(t.encode()) for t in texts)
        result=await self._json('POST','/api/embed',{'model':self.settings.model,'input':texts,'truncate':False})
        vectors=result.get('embeddings')
        if not isinstance(vectors,list) or len(vectors)!=len(texts):raise ResolverUnavailable('Invalid embedding count.')
        width=None
        for vector in vectors:
            if not isinstance(vector,list) or not 1<=len(vector)<=8192 or width is not None and len(vector)!=width:raise ResolverUnavailable('Invalid embedding shape.')
            width=len(vector)
            if any(type(n) not in {float,int} or not math.isfinite(n) or abs(n)>1e10 for n in vector) or not any(vector):raise ResolverUnavailable('Invalid embedding values.')
        return vectors

    async def health_check(self):
        names=[]
        for entry in (await self.installed())[:32]:
            info=await self._json('POST','/api/show',{'model':entry['name']})
            caps=info.get('capabilities',[])
            if isinstance(caps,list) and 'embedding' in caps:names.append(entry['name'])
        return names

    def get_capabilities(self):return {'embedding':True,'tool_calling':False,'structured_output':False,'context_length':None}


def cosine(a,b):
    if len(a)!=len(b):raise ResolverUnavailable('Embedding dimensions changed.')
    norm_a=math.sqrt(sum(n*n for n in a));norm_b=math.sqrt(sum(n*n for n in b))
    if not norm_a or not norm_b:raise ResolverUnavailable('Empty embedding vector.')
    return max(-1.,min(1.,sum(x*y for x,y in zip(a,b))/(norm_a*norm_b)))

class SemanticIntentResolver:
    def __init__(self,settings,backend=None):
        self.settings=settings;self.backend=backend or OllamaEmbeddingBackend(settings)
        self.anchor_fingerprint='';self.anchors=()

    async def suggest(self,text):
        started=time.monotonic();before=self.backend.embedding_calls;size=self.backend.input_size
        common={'model':self.settings.model,'configured_confidence_threshold':self.settings.confidence_threshold,'configured_ambiguity_margin':self.settings.ambiguity_margin};fingerprint=''
        try:
            if not self.settings.enabled or not self.settings.model:raise ResolverUnavailable('Semantic resolver model is not available.')
            text=transport_redactor.text(text)
            if len(text.encode())>8192:raise ResolverUnavailable('Input exceeds deterministic resolver bound.')
            fingerprint=await self.backend.identity()
            if fingerprint==self.anchor_fingerprint and self.anchors:
                query=(await self.backend.embed([text]))[0];anchors=self.anchors
            else:
                vectors=await self.backend.embed([*DESCRIPTIONS.values(),text]);anchors=vectors[:-1];query=vectors[-1]
                self.anchor_fingerprint=fingerprint;self.anchors=tuple(anchors)
            scored=sorted([(i,cosine(v,query)) for i,v in zip(DESCRIPTIONS,anchors)],key=lambda item:-item[1])
            first,second=scored[:2];mutating=first[0] in MUTATING
            threshold=min(1.,self.settings.confidence_threshold+(0.10 if mutating else 0))
            required_margin=min(2.,self.settings.ambiguity_margin+(0.05 if mutating else 0))
            margin=first[1]-second[1];high=first[1]>=threshold and margin>=required_margin
            status='HIGH_MATCH' if high else 'AMBIGUOUS' if first[1]>=threshold else 'NO_MATCH'
            result=IntentSuggestion(intent_id=first[0],match_status=status,top_similarity=first[1],second_similarity=second[1],margin=margin,threshold_result=high,accepted=high,
                top_matches=tuple({'intent_id':i,'similarity':score} for i,score in scored[:3]),fingerprint=fingerprint,reason='Heuristic cosine/margin test; not a probability.',
                effective_confidence_threshold=threshold,effective_ambiguity_margin=required_margin,mutation_threshold_applied=mutating,**common)
        except asyncio.CancelledError:raise
        except Exception as exc:
            result=IntentSuggestion(fingerprint=fingerprint,reason=redactor.text(str(exc))[:300],**common)
        return replace_result(result,embedding_calls=self.backend.embedding_calls-before,input_size=self.backend.input_size-size,elapsed_ms=round((time.monotonic()-started)*1000,3))


def replace_result(result,**kwargs):
    from dataclasses import replace
    return replace(result,**kwargs)

SCHEMA={'$schema':'https://json-schema.org/draft/2020-12/schema','title':'AlienApe Semantic Intent Suggestion V1','type':'object','additionalProperties':False,'required':[k for k in asdict(IntentSuggestion()) if k not in {'configured_confidence_threshold','effective_confidence_threshold','configured_ambiguity_margin','effective_ambiguity_margin','mutation_threshold_applied'}], 'properties':{
 'catalog_version':{'const':CATALOG_VERSION},'intent_id':{'enum':[None,*[str(i) for i in DESCRIPTIONS]]},'match_status':{'enum':['HIGH_MATCH','AMBIGUOUS','NO_MATCH','UNAVAILABLE']},
 **{k:{'type':['number','null'],'minimum':-2,'maximum':2} for k in ['top_similarity','second_similarity','margin']},
 **{k:{'type':['number','null'],'minimum':0,'maximum':2} for k in ['configured_confidence_threshold','effective_confidence_threshold','configured_ambiguity_margin','effective_ambiguity_margin']},
 'mutation_threshold_applied':{'type':['boolean','null']},
 'threshold_result':{'type':'boolean'},'accepted':{'type':'boolean'},'top_matches':{'type':'array','maxItems':3,'items':{'type':'object','additionalProperties':False,'required':['intent_id','similarity'],'properties':{'intent_id':{'enum':[str(i) for i in DESCRIPTIONS]},'similarity':{'type':'number','minimum':-1,'maximum':1}}}},
 **{k:{'type':'string','maxLength':300} for k in ['model','fingerprint','reason']},'embedding_calls':{'type':'integer','minimum':0,'maximum':1},'input_size':{'type':'integer','minimum':0},'elapsed_ms':{'type':'number','minimum':0}}}


def accept_suggestion(task,legacy,suggestion):
    """Advisory intent only. All actionable fields are reconstructed lexically."""
    from dataclasses import replace
    from .task_slots import slots_for,with_slots,TaskSlots,lexical_paths,language_hint
    from jsonschema import validate
    if not isinstance(suggestion,IntentSuggestion):return legacy,False,'INVALID_ADVISORY_RESULT'
    try:
        validate(json.loads(json.dumps(asdict(suggestion))),SCHEMA)
        if any(v is not None and not math.isfinite(v) for v in [suggestion.top_similarity,suggestion.second_similarity,suggestion.margin]):raise ValueError('non-finite')
    except Exception:return legacy,False,'INVALID_ADVISORY_RESULT'
    if not suggestion.accepted or suggestion.match_status!='HIGH_MATCH' or suggestion.intent_id not in DESCRIPTIONS:return legacy,False,suggestion.match_status
    intent=I(suggestion.intent_id)
    if intent in {I.CREATE_FILE,I.CREATE_DIRECTORY,I.CREATE_PROJECT}:
        slots=slots_for(task,legacy,suggested=intent)
        slots=replace(slots,resolution_source='SEMANTIC_FALLBACK')
        return with_slots(legacy,slots),True,'INTENT_ONLY_SLOTS_REVALIDATED'
    paths=lexical_paths(task) if intent in {I.READ_FILE,I.FIND_FILE,I.MODIFY_FILE,I.ANALYZE_FILE,I.VERIFY_FILE,I.EXPLAIN_CODE} else ()
    mutation=intent==I.MODIFY_FILE
    # Similarity never supplies the user's authority to mutate.
    explicit=bool(__import__('re').match(r'^\s*(?:(?:bitte|please)\s+)?(?:ändere|aendere|change|modify|edit|fix|repariere|modifie|modifiez|répare|repare)\b',task,__import__('re').I))
    missing=[]
    if intent in {I.READ_FILE,I.FIND_FILE,I.MODIFY_FILE,I.ANALYZE_FILE,I.VERIFY_FILE} and not paths:missing.append('target_path' if mutation else 'source_path')
    if mutation and not explicit:missing.append('explicit_mutation_request')
    slots=TaskSlots(intent,target_paths=paths if mutation else (),source_paths=paths,content_mode='EXISTING' if paths else 'UNKNOWN',mutation_requested=mutation and explicit,
        missing_slots=tuple(missing),resolution_source='SEMANTIC_FALLBACK',language_hint=language_hint(task,paths),slot_sources={'target_path' if mutation else 'source_path':'RAW_LEXICAL'} if paths else {})
    ambiguity='Bitte den konkreten Pfad und die gewünschte Aktion ausdrücklich nennen.' if missing else ''
    r=replace(legacy,intent_id=intent,handled=True,source='SEMANTIC_FALLBACK',referenced_paths=paths,output_paths=paths if mutation and explicit else (),mutation_requested=mutation and explicit,
        ambiguity_reason=ambiguity,slots=slots,operations=(intent,))
    return r,True,'INTENT_ONLY_SLOTS_REVALIDATED'
