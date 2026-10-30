"""Optional leaf instrumentation. No decisions, credentials, DB access or network."""
from contextvars import ContextVar
from dataclasses import asdict,is_dataclass
from datetime import datetime,timezone
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
import hashlib,json,os,re,tempfile,threading,time,uuid
from functools import wraps
from .security import redactor,SensitiveFilePolicy

active_trace=ContextVar('alienape_diagnostic_trace',default=None)
MESSAGE_LIMIT=16384
TRACE_LIMIT=2*1024*1024
SESSION_LIMIT=32*1024*1024
STORE_LIMIT=128*1024*1024
EVENT_LIMIT=512
SECTIONS=['input','intent','task','routing','model_requests','model_completions','tools','validation','verification','final_response','experience','resources']
SCHEMA={'$schema':'https://json-schema.org/draft/2020-12/schema','title':'AlienApe Diagnostic Trace V1','type':'object',
 'required':['schema_version','trace_id',*SECTIONS,'events','truncated'],
 'properties':{'schema_version':{'const':1},'trace_id':{'type':'string','pattern':'^[a-f0-9]{32}$'},
 **{n:{'type':'array' if n in {'routing','model_requests','model_completions','validation','verification','events'} else 'object'} for n in [*SECTIONS,'events']},'truncated':{'type':'boolean'},**{n:{'type':kind} for n,kind in [('resolver_chain','object'),('task_slots','object'),('pending_state','array'),('last_generated_artifact','object')]}},'additionalProperties':False}


def timestamp():return datetime.now(timezone.utc).isoformat()


def observe_only(method):
    @wraps(method)
    def wrapped(self,*args,**kwargs):
        try:return method(self,*args,**kwargs)
        except Exception as exc:self.store.last_error=redactor.text(str(exc))[:300]
    return wrapped


def sanitize(value,depth=0,limit=MESSAGE_LIMIT):
    """Redact completely BEFORE bounding; never persist raw header/file content."""
    value=redactor.clean(value)
    if depth>12:return {'omitted':True,'reason':'depth_bound'}
    if isinstance(value,str):
        value=re.sub(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*', '<REDACTED>',value)
        value=re.sub(r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}', '<REDACTED>',value)
        # A TaskPacket/WORKSPACE_INPUTS may be JSON inside a message string.
        candidate=value.split('\n',1)[-1] if value.startswith(('WORKSPACE_INPUTS\n','PROJECT_MEMORY\n','WORKSPACE_DISCOVERY\n')) else value
        if candidate.lstrip().startswith(('{','[')):
            try:
                decoded=json.loads(candidate)
                prefix=value[:-len(candidate)] if candidate!=value else ''
                value=prefix+json.dumps(sanitize(decoded,depth+1),ensure_ascii=False)
            except (ValueError,RecursionError):pass
        raw=value.encode('utf-8')
        if len(raw)>limit:return {'text':raw[:limit].decode('utf-8',errors='ignore'),'truncated':True,'original_size':len(raw)}
        return value
    if isinstance(value,dict):
        paths=[value.get(k) for k in ['path','file','filename'] if isinstance(value.get(k),str)]
        for k in ['paths','referenced_paths','output_paths','target_paths','source_paths']:
            if isinstance(value.get(k),(list,tuple)):paths.extend(p for p in value[k] if isinstance(p,str))
        sensitive=any(SensitiveFilePolicy.sensitive(p) for p in paths)
        out={}
        for key,v in list(value.items())[:128]:
            key=redactor.text(key)
            if key.casefold().replace('-','_') in {'headers','http_headers','request_headers','response_headers','authorization','proxy_authorization'}:continue
            if (key in {'content','text','data','literal_content','generation_request','diff','stdout','stderr'} and sensitive):
                out[key]={'omitted':True,'reason':'sensitive_file'};continue
            if key in {'content','diff'} and paths and isinstance(v,str):
                out[key]={'omitted':True,'reason':'file_content_metadata','bytes':len(v.encode('utf-8')),'sha256':hashlib.sha256(v.encode('utf-8')).hexdigest()};continue
            out[key]=sanitize(v,depth+1,8192 if key=='error' else MESSAGE_LIMIT)
        if len(value)>128:out['_bounds']={'truncated':True,'original_size':len(value)}
        return out
    if isinstance(value,(tuple,list)):
        result=[sanitize(v,depth+1) for v in value[:128]]
        if len(value)>128:result.append({'truncated':True,'original_size':len(value)})
        return result
    if value is None or isinstance(value,(bool,int,float)):return value
    return sanitize(str(value),depth+1)


def _plain(value):return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)


def chat_block(value):
    text=_plain(value);fence='`'*max(3,1+max((len(m[0]) for m in re.finditer(r'`+',text)),default=0))
    return fence+'text\n'+text+'\n'+fence


def _plain_file(path):
    return not path.is_symlink() and not bool(getattr(path.stat(),'st_file_attributes',0)&0x400)


def compact(value):
    if isinstance(value,str) and len(value.encode())>512:return {'text':value.encode()[:512].decode('utf-8',errors='ignore'),'truncated':True,'original_size':len(value.encode())}
    if isinstance(value,dict):return {k:compact(v) for k,v in value.items()}
    if isinstance(value,list):return [compact(v) for v in value]
    return value


class DiagnosticStore:
    """Lazy, bounded per-session files; never reads config, credentials or databases."""
    def __init__(self,root):
        self.root=Path(root);self.lock=threading.RLock();self.last_error='';self.latest={}
    def begin(self,agent,text,session_id=None,project_id=None):
        try:
            session_id=session_id or getattr(self,'session_id',None) or uuid.uuid4().hex
            self.session_id=session_id
            project_id=project_id or hashlib.sha256(str(agent.workspace.root).encode()).hexdigest()[:24]
            folder=self.root/hashlib.sha256(session_id.encode()).hexdigest()[:32]
            folders=[p for p in self.root.glob('*') if re.fullmatch(r'[a-f0-9]{32}',p.name) and p.is_dir() and _plain_file(p)] if self.root.exists() else []
            if (len(folders)>=128 and folder not in folders) or sum(p.stat().st_size for d in folders for p in d.glob('*.jsonl') if re.fullmatch(r'[a-f0-9]{32}\.jsonl',p.name) and _plain_file(p))>=STORE_LIMIT:
                self.last_error='Diagnosespeicher-Limit erreicht; vorhandene Traces bleiben erhalten.';return None
            if folder.exists() and (not _plain_file(folder) or folder.resolve().parent!=self.root.resolve()):raise ValueError('Unsicheres Diagnoseverzeichnis.')
            if folder.exists() and (len(list(folder.glob('*.jsonl')))>=128 or sum(p.stat().st_size for p in folder.glob('*.jsonl'))>=SESSION_LIMIT):
                self.last_error='Diagnose-Sessionlimit erreicht.';return None
            folder.mkdir(parents=True,exist_ok=True)
            trace=DiagnosticTrace(self,folder,agent,text,session_id,project_id);self.latest[session_id]=trace.path;return trace
        except Exception as exc:self.last_error=redactor.text(str(exc))[:300];return None
    def records(self,session_id=None,last_only=False):
        session_id=session_id or getattr(self,'session_id','')
        folder=self.root/hashlib.sha256(session_id.encode()).hexdigest()[:32]
        if not folder.exists():return []
        if not _plain_file(folder) or folder.resolve().parent!=self.root.resolve():return []
        files=sorted((p for p in folder.glob('*.jsonl') if re.fullmatch(r'[a-f0-9]{32}\.jsonl',p.name) and _plain_file(p)),key=lambda p:p.stat().st_mtime_ns)
        if last_only:files=[self.latest[session_id]] if self.latest.get(session_id) in files else files[-1:]
        records=[]
        with self.lock:
            for p in files[:128]:
                if p.stat().st_size>TRACE_LIMIT+65536:continue
                events=[]
                for line in p.read_text(encoding='utf-8').splitlines():
                    try:events.append(sanitize(json.loads(line)))
                    except ValueError:continue # interrupted final append, never invent data
                if events:records.append(assemble(events))
        return records
    def export(self,destination,session_id=None,last_only=False):
        records=self.records(session_id,last_only)
        if not records:raise ValueError('Keine Diagnosetraces für diese Session vorhanden. Diagnostic Trace vor dem Test einschalten.')
        chat=['# AlienApe Diagnostic Chat','']
        for r in records:
            chat+=['## Trace '+r['trace_id'],'','**Du**',chat_block(r['input'].get('user_text','')),'','**AlienApe**',chat_block(r['final_response'].get('text','[Turn noch nicht abgeschlossen]')),'']
        totals={}
        for r in records:
            for k,v in r['resources'].get('turn_delta',{}).items():
                if isinstance(v,(float,int)):totals[k]=totals.get(k,0)+v
        summary={'trace_ids':[r['trace_id'] for r in records],'models':sorted({str(x.get('model_id')) for r in records for x in r['routing'] if x.get('model_id')}),
            'failures':[{'trace_id':r['trace_id'],'failure':r['final_response'].get('failure_classification'),'ambiguity_reason':r['intent'].get('ambiguity_reason')} for r in records if r['intent'].get('ambiguity_state') or r['final_response'].get('source')=='ERROR_HANDLER' or r['final_response'].get('failure_classification') not in {None,'SUCCESS','UNKNOWN'}],
            'model_requested_tool_calls':sum(r['resources'].get('model_requested_tool_calls',len(r['tools']['parsed'])) for r in records),'tool_requests':sum(r['resources'].get('turn_delta',{}).get('tool_calls',len(r['tools']['requests'])) for r in records),'actual_dispatches':sum(r['resources'].get('actual_tool_dispatches',len(r['tools']['dispatches'])) for r in records),'resource_totals':totals,'truncated':any(r['truncated'] for r in records)}
        from . import __version__
        from .build_identity import BUILD_ID
        contents={'CHAT.md':'\n'.join(chat),'TRACE.jsonl':'\n'.join(json.dumps(sanitize(r),ensure_ascii=False) for r in records)+'\n','SUMMARY.json':json.dumps(sanitize(summary),ensure_ascii=False,indent=2)}
        contents={name:redactor.text(value) for name,value in contents.items()}
        manifest={'alienape_version':__version__,'source_build_identifier':BUILD_ID,'diagnostic_schema_version':1,'redaction_enabled':True,'exported_at':timestamp(),'files':list(contents)+['MANIFEST.json'],'file_sha256':{n:hashlib.sha256(v.encode('utf-8')).hexdigest() for n,v in contents.items()}}
        contents['MANIFEST.json']=redactor.text(json.dumps(sanitize(manifest),ensure_ascii=False,indent=2))
        # Second redaction at export also covers values registered since recording.
        destination=Path(destination)
        # A same-directory hard link publishes the completed file atomically and
        # fails if the destination already exists, preserving the old 'x' policy.
        fd,temporary=tempfile.mkstemp(prefix='.alienape-diagnostic-',suffix='.tmp',dir=destination.parent)
        try:
            os.close(fd)
            with ZipFile(temporary,'w',ZIP_DEFLATED) as archive:
                for name,text in contents.items():archive.writestr(name,redactor.text(text))
            os.link(temporary,destination)
        except BaseException as exc:
            try:os.unlink(temporary)
            except FileNotFoundError:pass
            except OSError as cleanup:
                self.last_error='Diagnoseexport: temporäre Datei konnte nicht entfernt werden: '+redactor.text(str(cleanup))[:200]
                exc.add_note(self.last_error)
            raise
        try:os.unlink(temporary)
        except OSError as cleanup:
            # The final archive is complete; surface any leftover temporary file.
            self.last_error='Diagnoseexport: temporäre Datei konnte nicht entfernt werden: '+redactor.text(str(cleanup))[:200]
        return destination


class DiagnosticTrace:
    def __init__(self,store,folder,agent,text,session_id,project_id):
        self.store=store;self.trace_id=uuid.uuid4().hex;self.path=folder/(self.trace_id+'.jsonl')
        self.size=0;self.count=0;self.dropped=0;self.call_index=None;self.started=time.monotonic()
        self.initial=asdict(agent.resources.counters);self.tool_requests=0;self.dispatches=0
        self.model_requested_calls=0
        self.response_source=None
        self.record('INPUT',{'timestamp':timestamp(),'session_id':session_id,'project_id':project_id,'trace_id':self.trace_id,'user_text':text})
    def activate(self):return active_trace.set(self)
    def deactivate(self,token):active_trace.reset(token)
    def record(self,kind,data,force=False):
        try:
            if not force and (self.count>=EVENT_LIMIT or self.size>=TRACE_LIMIT-262144):self.dropped+=1;return
            event={'timestamp':timestamp(),'trace_id':self.trace_id,'event_type':kind,'tool_call_index':self.call_index,'data':sanitize(data)}
            encoded=json.dumps(event,ensure_ascii=False)
            if len(encoded.encode())>65536:
                original_size=len(encoded.encode());event['data']=compact(event['data']);event['data']['_event_bounds']={'truncated':True,'original_size':original_size};encoded=json.dumps(event,ensure_ascii=False)
                if len(encoded.encode())>65536:
                    event['data']={'truncated':True,'original_size':original_size,'reason':'event_bound'};encoded=json.dumps(event,ensure_ascii=False)
            with self.store.lock:
                if self.path.exists() and not _plain_file(self.path):raise ValueError('Unsichere Diagnosedatei.')
                with self.path.open('a',encoding='utf-8') as stream:stream.write(encoded+'\n')
                self.count+=1;self.size+=len(encoded.encode())+1
        except Exception as exc:self.store.last_error=redactor.text(str(exc))[:300]
    def event(self,agent,kind,details,step,resources):
        try:
            self.record('EVENT',{'kind':kind,'details':details,'step':step,'resources':resources})
            if kind=='DIRECT_RESPONSE':self.response_source='CONTROLLER' if details.get('source')=='CONTROLLER' else 'DIRECT_RESPONSE'
            if kind in {'CREATE_TOOL_COMPLETED','TOOL_DENIED'}:self.response_source='CONTROLLER'
            if kind=='CONTROLLER_ACTION':self.response_source='TOOL_RESULT' if details.get('action')=='read_file' else 'CONTROLLER'
            if kind in {'INTENT_RESOLVED','INTENT_AMBIGUITY'}:self.intent(agent)
            if kind=='RESPONSE_CONTRACT':self.task(agent)
            if kind=='MODEL_ROUTED':
                p=getattr(agent,'current_profile',None)
                self.record('ROUTING',{'model_id':getattr(p,'id',details.get('to_model')),'model':getattr(p,'model_name',None),'provider':getattr(p,'provider',None),'backend_type':None,
                    'fingerprint':getattr(agent,'fingerprints',{}).get(getattr(p,'id','')),'tool_mode':getattr(p,'tool_mode',None),'reason':details.get('reason'),'category':str(getattr(agent,'category','UNKNOWN')),
                    'declared_capabilities':getattr(p,'capabilities',[]),'resource_class':getattr(p,'resource_class',None),'observed_samples':None,'observed_capability_evidence':'Only existing MODEL_SELECTED_FROM_EXPERIENCE events; absent evidence is unknown.'})
            if kind=='VALIDATION_RESULT':
                self.record('NORMALIZED_COMPLETION',{'text':getattr(agent,'last_response',{}).get('content'),'proposed_files':getattr(getattr(agent,'last_response',None),'proposed_files',[]),'validation':details,'contract':asdict(agent.response_contract) if agent.response_contract else None,'validator':type(agent.validator).__name__ if agent.validator else 'existing automatic/contract validator'})
        except Exception as exc:self.store.last_error=redactor.text(str(exc))[:300]
    def intent(self,agent):
        r=getattr(agent,'intent_resolution',None)
        if not r:return
        from .routing import referenced_paths
        d=asdict(r);d['content_mode']='UNKNOWN' if r.ambiguity_reason else 'EMPTY' if r.literal_content=='' else 'LITERAL' if r.literal_content is not None else 'GENERATE' if r.intent_id.value=='CREATE_FILE' else 'UNKNOWN'
        if r.slots:d.update(content_mode=r.slots.content_mode,generation_request=r.slots.generation_request)
        d['canonical_intent']=r.intent_id.value;d['ambiguity_state']=bool(r.ambiguity_reason)
        d['lexical_path_candidates']=referenced_paths(getattr(agent,'resolution_task','') or '')
        d['generation_request']=getattr(agent,'resolution_task','') if r.intent_id.value=='CREATE_FILE' and r.literal_content is None else None
        self.record('INTENT',d)
    def task(self,agent):
        self.record('TASK',{'domain':str(getattr(agent,'category','UNKNOWN')),'response_contract':asdict(agent.response_contract) if agent.response_contract else None,'required_files':getattr(agent,'required_input_paths',[]),
            'proposed_output_files':getattr(agent.intent_resolution,'output_paths',()),'write_scope':asdict(agent.write_scope) if agent.write_scope else None,'permission_requirement':{k:agent.settings.permissions.get(k,'ask') for k in ['write_file','make_directory']},'validator':type(agent.validator).__name__ if agent.validator else 'automatic/contract'})
    @observe_only
    def request(self,agent,messages,schemas,limit):
        self.record('MODEL_REQUEST',{'provider':getattr(getattr(agent,'current_profile',None),'provider',agent.settings.model.provider),'model':agent.resources.model,'tool_mode':getattr(getattr(agent,'current_profile',None),'tool_mode','native'),'output_limit':limit,'message_count':len(messages),'messages':messages,'tool_schemas':schemas,'backend_type':type(agent.backend).__name__})
    def received(self,response):
        try:
            self.call_index=None
            self.response_source='MODEL'
            if isinstance(response.get('tool_calls'),list):self.model_requested_calls+=len(response['tool_calls'])
            self.record('MODEL_RECEIVED',{'raw_text':response.get('content'),'parsed_tool_calls':parse_calls(response.get('tool_calls') or [])})
        except Exception as exc:self.store.last_error=redactor.text(str(exc))[:300]
    @observe_only
    def wire_request(self,provider,payload):self.record('WIRE_REQUEST',{'provider':provider,**{k:payload[k] for k in ['model','messages','max_tokens','temperature','tools','response_format','stream'] if k in payload}})
    @observe_only
    def wire_response(self,provider,result):
        if not isinstance(result,dict):return
        choices=[]
        for c in result.get('choices',[])[:4]:
            m=c.get('message',{});choices.append({'finish_reason':c.get('finish_reason'),'message':{k:m.get(k) for k in ['role','content','tool_calls']}})
        self.record('WIRE_RESPONSE',{'provider':provider,'model':result.get('model'),'choices':choices,'usage':{k:v for k,v in result.get('usage',{}).items() if k in {'prompt_tokens','completion_tokens','total_tokens'}}})
    def dispatch(self,function,name,arguments):
        self.dispatches+=1;self.record('TOOL_DISPATCH',{'name':name,'arguments':arguments,'dispatch':True})
        try:result=function(name,arguments)
        except Exception as exc:
            self.record('TOOL_DISPATCH_RESULT',{'name':name,'status':'ERROR','error':str(exc)});raise
        self.record('TOOL_DISPATCH_RESULT',{'name':name,'status':'RETURNED','result':result});return result
    def finish(self,agent,text,source=None,error=False):
        try:
            self.intent(agent);self.task(agent)
            snapshot=agent.resources.snapshot();delta={k:snapshot[k]-v for k,v in self.initial.items() if isinstance(v,(int,float)) and isinstance(snapshot.get(k),(int,float))}
            self.record('RESOURCES',{'existing_snapshot':snapshot,'turn_delta':delta,'actual_tool_dispatches':self.dispatches,'model_requested_tool_calls':self.model_requested_calls,'trace_elapsed_seconds':round(time.monotonic()-self.started,6),'semantic_resolver':getattr(agent,'semantic_usage',{})},force=True)
            self.record('EXPERIENCE_STATUS',{'produced':bool(getattr(agent,'experience_ids',[])),'record_ids':getattr(agent,'experience_ids',[]),'reason':'See existing attribution events.' if getattr(agent,'experience_ids',[]) else 'No Experience store attached.' if not getattr(agent,'experience',None) else 'No existing Experience attribution event for this turn.'},force=True)
            self.record('FINAL',{'text':text,'source':source or ('ERROR_HANDLER' if error else self.response_source or ('MODEL' if delta.get('model_calls') else 'CONTROLLER')),'failure_classification':str(agent.last_failure_type),'failure_origin':'CONTROLLER' if error and not delta.get('model_calls') else 'UNKNOWN','error':error},force=True)
            self.record('TRACE_END',{'truncated':bool(self.dropped),'dropped_events':self.dropped},force=True)
        except Exception as exc:self.store.last_error=redactor.text(str(exc))[:300]


def parse_calls(calls):
    if not isinstance(calls,list):return [{'parse_valid':False,'error':'tool_calls is not a list'}]
    parsed=[]
    for index,c in enumerate(calls[:128]):
        try:
            f=c.get('function',{});raw=f.get('arguments');args=json.loads(raw) if isinstance(raw,str) else raw
            parsed.append({'index':index,'function_name':f.get('name'),'arguments':args,'parse_valid':isinstance(args,dict) and isinstance(f.get('name'),str) and bool(f.get('name'))})
        except Exception as exc:parsed.append({'index':index,'parse_valid':False,'arguments':str(c),'error':str(exc)})
    if len(calls)>128:parsed.append({'truncated':True,'original_size':len(calls)})
    return parsed


def assemble(events):
    trace={'schema_version':1,'trace_id':events[0]['trace_id'],'input':{},'intent':{},'task':{},'routing':[],'model_requests':[],'model_completions':[],
        'tools':{'parsed':[],'guards':[],'requests':[],'dispatches':[],'results':[]},'validation':[],'verification':[],'final_response':{},'experience':{'produced':False,'records':[],'reason':'No EXPERIENCE_RECORDED event observed.'},'resources':{},'resolver_chain':{},'task_slots':{},'pending_state':[],'last_generated_artifact':{'present':False},'events':events,'truncated':False}
    direct=False;durations=[];token_usage=[]
    for e in events:
        kind=e['event_type'];d=e['data'];index=e.get('tool_call_index')
        if kind=='INPUT':trace['input']=d
        elif kind=='INTENT':trace['intent'].update(d)
        elif kind=='TASK':trace['task']=d
        elif kind=='ROUTING':trace['routing'].append(d)
        elif kind in {'MODEL_REQUEST','WIRE_REQUEST'}:trace['model_requests'].append({'stage':kind,**d})
        elif kind in {'MODEL_RECEIVED','WIRE_RESPONSE','NORMALIZED_COMPLETION'}:
            trace['model_completions'].append({'stage':kind,**d})
            if kind=='MODEL_RECEIVED':trace['tools']['parsed'].extend(d.get('parsed_tool_calls',[]))
        elif kind=='TOOL_DISPATCH':trace['tools']['dispatches'].append({'tool_call_index':index,**d})
        elif kind=='TOOL_DISPATCH_RESULT':trace['tools']['results'].append({'tool_call_index':index,**d})
        elif kind in {'TOOL_GUARD','PERMISSION_WAIT','PERMISSION_DECISION','PERMISSION_RESULT'}:trace['tools']['guards'].append({'tool_call_index':index,'event':kind,**d})
        elif kind=='RESOURCES':trace['resources']=d
        elif kind=='FINAL':trace['final_response']=d
        elif kind=='VALIDATOR':trace['validation'].append(d)
        elif kind=='EXPERIENCE_STATUS':trace['experience'].update(d)
        elif kind=='TRACE_END':trace['truncated']=d.get('truncated',False)
        elif kind=='EVENT':
            name=d.get('kind');details=d.get('details',{})
            if name=='RESOLVER_CHAIN':trace['resolver_chain']=details
            if name=='TASK_SLOTS':trace['task_slots']=details
            if name=='PENDING_STATE':trace['pending_state'].append(details)
            if name=='GENERATED_ARTIFACT':trace['last_generated_artifact']=details
            if name=='TASK_CLASSIFIED':trace['intent']['task_type']=details.get('task_type')
            if name=='DIRECT_RESPONSE' and details.get('source')!='CONTROLLER':direct=True
            if name=='MODEL_RESPONSE' and isinstance(details.get('duration'),(int,float)):durations.append(details['duration'])
            if name=='MODEL_COMPLETION':token_usage.append(details)
            if name=='TOOL_REQUEST':trace['tools']['requests'].append({'tool_call_index':index,**details})
            if name in {'TOOL_DENIED','FAILURE_CLASSIFIED','ERROR','CREATE_TOOL_COMPLETED','TOOL_APPROVED'}:trace['tools']['guards'].append({'tool_call_index':index,'event':name,**details})
            if name=='FAILURE_CLASSIFIED' and details.get('subreason')=='TOOL_CALL_LIMIT':
                trace['tools']['guards'].extend({'tool_call_index':i,'dispatch':False,'reason':'MAX_TOOL_CALLS'} for i in range(min(details.get('requested_calls',0),128)))
            if name in {'VALIDATION_RESULT','RESPONSE_CONTRACT','FAILURE_CLASSIFIED'}:trace['validation'].append({'event':name,**details})
            if name.startswith('VERIFICATION_') or name=='PROJECT_CHECK_COMPLETED':trace['verification'].append({'event':name,**details})
            if name in {'MODEL_SELECTED_FROM_EXPERIENCE','MODEL_SKIPPED_FROM_EXPERIENCE'}:trace['routing'].append({'event':name,**details})
            if name=='EXPERIENCE_RECORDED':trace['experience']['produced']=True;trace['experience']['records'].append(details);trace['experience']['reason']='Existing controller Experience event; no diagnostic relabeling.'
    if direct and trace['final_response'].get('source')!='ERROR_HANDLER':trace['final_response']['source']='DIRECT_RESPONSE'
    for route in trace['routing']:
        if 'declared_capabilities' not in route:continue
        route['declared_capabilities_origin']='configured_profile'
        for evidence in trace['routing']:
            if evidence.get('event')=='MODEL_SELECTED_FROM_EXPERIENCE' and evidence.get('model_id')==route.get('model_id'):
                samples=evidence.get('candidates',{}).get(route['model_id'],{}).get('samples')
                route['observed_samples']=samples;route['observed_capability_evidence']=evidence
    if not trace['experience']['produced'] and not trace['resources'].get('turn_delta',{}).get('model_calls'):trace['experience']['reason']='Deterministic controller/direct turn; no model performance evidence.'
    calls=trace['resources'].get('turn_delta',{}).get('model_calls',0)
    model_elapsed=sum(durations) if len(durations)==calls else None
    trace['resources']['model_elapsed_seconds']=model_elapsed
    elapsed=trace['resources'].get('trace_elapsed_seconds')
    trace['resources']['controller_elapsed_seconds']=max(0,elapsed-model_elapsed) if model_elapsed is not None and elapsed is not None else None
    for field in ['input_tokens','output_tokens']:
        trace['resources']['actual_'+field]=sum(d[field] for d in token_usage) if len(token_usage)==calls and all(isinstance(d.get(field),int) for d in token_usage) else None
    trace['intent'].setdefault('task_type',None)
    trace['truncated']=trace['truncated'] or len(events)>128 or any('"truncated": true' in json.dumps(e,ensure_ascii=False) for e in events)
    return sanitize(trace)
