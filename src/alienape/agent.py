import asyncio
import json
import re
import os
import threading
import hashlib,difflib
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import httpx
from .context import ContextManager, size
from .identity import controller_facts
from .intent_resolution import IntentID, DeterministicIntentResolver, bind_recent
from .task_execution import apply_resolution, execute_controller_intent, provide_explicit_inputs, find_explicit_targets
from .security import redactor, transport_redactor, SensitiveFilePolicy
from .controller import ResourceController
from .tools import ToolRegistry, ToolArgumentInvalid
from .file_actions import direct_read_path, format_file_contents
from .completion import ModelResponse, CompletionMetadata, CompletionRejected, FailureType
from .validators import ValidationResult, validator_for_task, JSONValidator
from .contracts import contract_for_task, normalize_response, WriteScope, ResponseMode


class Session:
    def __init__(self):
        self.history = []
        from .task_state import SessionTaskState
        self.task_state=SessionTaskState()
        self.semantic_resolver=None
        self.semantic_signature=None

    def clear(self):
        self.history.clear()
        from .task_state import SessionTaskState
        self.task_state=SessionTaskState()
        self.semantic_resolver=None
        self.semantic_signature=None


class Agent:
    def __init__(self, backend, workspace, settings, session=None, memory=None,
                 permission=None, observe=None, stopped=None, validator=None, intent_resolver=None, semantic_resolver=None):
        self.intent_resolver=intent_resolver or DeterministicIntentResolver()
        self.intent_resolution=None;self.resolution_task=None
        self.semantic_resolver=semantic_resolver
        self.semantic_usage={"semantic_resolver_calls":0,"semantic_resolver_elapsed_ms":0.0,"semantic_resolver_model":"","semantic_resolver_input_size":0}
        self.resolver_chain={}
        self.diagnostic_store=None;self.diagnostic_trace=None
        self.backend, self.workspace, self.settings = backend, workspace, settings
        self.session = session or Session()
        self.memory, self.permission = memory, permission
        self.observe = observe or (lambda *args: None)
        self.stopped = stopped or threading.Event()
        self.resources = ResourceController(settings.model.model)
        self.tools = ToolRegistry(workspace, self.stopped, self.emit)
        self.step = 0
        self.written_paths = []
        self.changed_paths=[]
        self.validator = validator
        self.last_validation = ValidationResult()
        self.last_completion = CompletionMetadata()
        self.last_failure_type = FailureType.UNKNOWN
        self.validation_history = []
        self.known_context_limit = None
        self.model_call_limit = settings.agent.max_steps
        self.response_contract = None
        self.write_scope = None
        self.information_complete = True
        self.protocol_normalized = False
        self.information_errors = set()
        self.active_tool_names = None
        self.dependency_failure = None
        self.workspace_operation=None
        self.workspace_discovery=None
        self.verification=None
        self.task_id=''
        self.project_findings=[]
        self.project_verifications=[]
        self.resolved_finding_ids=[]
        self.resolved_from_task_id=''
        self.resolved_paths=[]

    def prepare_task(self,task):
        from .routing import referenced_paths
        from .discovery import workspace_intent
        if self.resolution_task!=task:
            self.intent_resolution=self.intent_resolver.resolve(task);self.resolution_task=task
        self.response_contract=contract_for_task(task,self.validator)
        self.write_scope=WriteScope.for_task(task)
        self.scope_task=task
        if self.resolved_finding_ids:
            self.scope_task='Fix only '+', '.join('"'+p+'"' for p in self.resolved_paths)+'. Do not change tests.'
            self.response_contract=contract_for_task(self.scope_task,self.validator)
            self.write_scope=WriteScope(False,list(self.resolved_paths),True,False,False,True)
        self.current_task=task
        self.file_content_protocol_status=None
        self.tools.sensitive_read_path=None
        self.written_paths=[]
        self.changed_paths=[]
        from .verification import VerificationRunner
        self.verification=VerificationRunner(self.workspace,self.settings.verification,self.check_stop,
            lambda name,args,path:self.permission(name,args,path) if self.permission else asyncio.sleep(0,result=False),self.emit)
        self.workspace_operation=workspace_intent(task)
        if self.write_scope.pending_source_targets:self.workspace_operation='analysis'
        self.workspace_discovery=None
        self.information_errors=set()
        self.dependency_failure=None
        self.information_complete=True
        self.protocol_normalized=False
        self.required_input_paths=[]
        # Canonical inputs/scope are applied below; never validate payload words
        # against a legacy scope extracted from the entire raw message.
        for p in (self.resolved_paths if self.resolved_finding_ids else () if self.intent_resolution.handled else referenced_paths(task)):
            target=self.workspace.path(p)
            new_target=(not self.write_scope.read_only and self.write_scope.permits(p,self.workspace) and not target.exists()
                        and bool(re.search(r'\b(?:neu\w*|erstell\w*|create|new|schreib\w*|write)\b',task,re.I)))
            if not new_target:self.required_input_paths.append(p)
        if not self.resolved_finding_ids:apply_resolution(self,self.intent_resolution)
        # Standalone agents use native tools when the selected backend declares them.
        if not hasattr(self,'registry') and self.response_contract.mode==ResponseMode.FILE_CONTENT and self.backend.get_capabilities().get('tool_calling') is True:
            self.response_contract=__import__('dataclasses').replace(self.response_contract,mode=ResponseMode.TOOL_ACTION,expects_file_proposal=False,expects_plain_text=True)
        self.canonical_contract=self.response_contract
        self.emit('RESPONSE_CONTRACT',{'mode':str(self.response_contract.mode),'write_scope':asdict(self.write_scope)})
        if self.write_scope.planned_new_paths:
            if any(self.workspace.path(p).exists() for p in self.write_scope.planned_new_paths):
                self.information_complete=False
                raise CompletionRejected(FailureType.INFORMATION_MISSING,'Geplanter neuer Zielpfad existiert bereits; keine implizite Überschreibfreigabe.')
            self.emit('NEW_FILE_PLAN',{'planned_new_paths':self.write_scope.planned_new_paths,'write_scope':asdict(self.write_scope)})

    def task_schemas(self,task):
        if self.intent_resolution and self.intent_resolution.handled and self.intent_resolution.intent_id in {IntentID.GENERAL_CHAT,IntentID.EXPLAIN_CODE} and not self.intent_resolution.referenced_paths:
            self.active_tool_names=set();return []
        from .routing import referenced_paths
        read=bool(self.workspace_operation or referenced_paths(task) or re.search(r'\b(?:datei\w*|ordner|workspace|verzeichnis|tool|werkzeug\w*|read_file|search_files|list_files)\b',task,re.I))
        names={'read_file','search_files','list_files'} if read or not self.write_scope.read_only else set()
        if self.workspace_discovery:names={'read_file','list_files'}
        if not self.write_scope.read_only:
            names.add('write_file')
            if self.write_scope.allow_directory:names.add('make_directory')
        self.active_tool_names=names
        schemas=self.tools.schemas(names,self.write_scope)
        if self.workspace_discovery:
            for item in schemas:
                name=item['function']['name']
                if name in {'read_file','list_files'}:
                    item['function']['parameters']['properties']['path']['enum']=([f['path'] for f in self.workspace_discovery.files] if name=='read_file' else list(self.workspace_discovery.listings))
        return schemas

    def audit_information(self,task,messages):
        """Only count context actually present in the outgoing request, not cached reads."""
        from .routing import referenced_paths
        delivered=set();memories=[];discovery_provided=False
        for m in messages:
            text=m.get('content') or ''
            if text.startswith('WORKSPACE_DISCOVERY\n'):
                try:
                    discovery=json.loads(text.split('\n',1)[1])
                    delivered.update(f['path'] for f in discovery.get('files',[]) if not f.get('truncated'))
                    discovery_provided=bool(discovery.get('manifest',{}).get('data_available'))
                except (ValueError,TypeError):pass
            if m.get('role')=='tool':
                try:v=json.loads(text)
                except (ValueError,TypeError):continue
                if isinstance(v,dict) and 'content' in v and 'path' in v:delivered.add(v['path'])
            if text.startswith('WORKSPACE_INPUTS\n'):
                try:delivered.update(f['path'] for f in json.loads(text.split('\n',1)[1]).get('files',[]))
                except (ValueError,TypeError):pass
            if text.startswith('PROJECT_MEMORY\n'):
                try:memories.extend(json.loads(text.split('\n',1)[1]))
                except ValueError:pass
            if m.get('role')=='user':
                try:packet=json.loads(text)
                except (ValueError,TypeError):continue
                if isinstance(packet,dict) and 'task_type' in packet:
                    delivered.update(f['path'] for f in packet.get('relevant_files_or_contents',[]) if not f.get('truncated'))
                    memories.extend(packet.get('relevant_memory',[]))
                    discovery_provided=bool(packet.get('workspace_discovery',{}).get('data_available'))
        required=self.required_input_paths
        canonical=lambda p:os.path.normcase(self.workspace.path(p).as_posix())
        missing=[p for p in required if canonical(p) not in {canonical(x) for x in delivered}]
        keys=re.findall(r'(?:Projektregel|memory.key|Memory-Schlüssel)\s+["`]?([\w-]+)',task,re.I)
        missing_memory=[k for k in keys if k not in {m.get('key') for m in memories}]
        self.information_complete=not (missing or missing_memory or self.information_errors) and (self.workspace_operation!='analysis' or discovery_provided)
        self.emit('INFORMATION_AUDIT',{'information_complete':self.information_complete,'required_paths':required,
            'missing_paths':missing,'missing_memory_keys':missing_memory,'memory_keys':[m.get('key') for m in memories],
            'discovery_provided':discovery_provided,'project_complete':False if self.workspace_operation=='analysis' else None,
            'scope':'bounded_workspace' if self.workspace_operation=='analysis' else 'explicit_dependencies'})
        return self.information_complete

    def emit(self, kind, details=None):
        details = redactor.clean(details or {})
        if kind=='MODEL_CALL':
            profile=getattr(self,'current_profile',None)
            if profile:getattr(self,'_status_task_state',self.session.task_state).last_worker_model=profile.model_name
        counts = self.resources.counters
        if kind == "FILE_READ":
            counts.file_reads += 1
            counts.bytes_read += details.get("bytes", 0)
        elif kind == "FILE_WRITE":
            counts.file_writes += 1
        elif kind == "MEMORY_STORE":
            counts.memory_writes += 1
        elif kind == "MEMORY_RETRIEVE":
            counts.memory_reads += 1
        snapshot=self.resources.snapshot()
        snapshot.update(self.semantic_usage)
        if self.diagnostic_trace:self.diagnostic_trace.event(self,kind,details,self.step,snapshot)
        self.observe(kind, details, self.step, snapshot)

    def check_stop(self):
        if self.stopped.is_set():
            raise asyncio.CancelledError()

    def on_resource_failure(self):
        """RoutedAgent attaches compact per-attempt experience here."""

    async def call_model(self,task,messages,schemas,send,*,backend=None,structured_protocol=False):
        if self.response_contract is None:self.prepare_task(task)
        if self.workspace_operation=='analysis' and not self.audit_information(task,messages):
            self.last_failure_type=FailureType.INFORMATION_MISSING
            raise CompletionRejected(FailureType.INFORMATION_MISSING,'Benötigte Discovery-Dateien fehlen im ausgehenden Kontext.')
        messages[:]=transport_redactor.clean(messages)
        backend = backend or self.backend
        policy = self.settings.resources.output
        limit = policy.default_limit if self.response_contract.mode==ResponseMode.FILE_CONTENT or self.resources.needs_tools(task) else min(128,policy.default_limit)
        retrying = False
        while True:
            self.check_stop()
            if self.response_contract.mode==ResponseMode.FILE_CONTENT:self.file_content_protocol_status=None
            self.last_failure_type = FailureType.UNKNOWN
            counts = self.resources.counters
            if counts.model_calls >= self.model_call_limit:
                raise LoopStopped('Gesamtes Modell-Schrittlimit erreicht.')
            counts.model_calls += 1
            counts.context_size = size(messages)
            counts.input_tokens_estimate += (size(messages)+size(schemas or [])+2)//3
            self.emit('MODEL_CALL',{'model':self.resources.select_model(task),'context_bytes':size(messages),'requested_output_limit':limit})
            started = time.monotonic()
            try:
                with backend.output_budget(limit):
                    if self.diagnostic_trace:self.diagnostic_trace.request(self,messages,schemas,limit)
                    response = await send()
            except (Exception,asyncio.CancelledError) as exc:
                response = getattr(exc,'model_response',None)
                if response is None:
                    self.last_completion = getattr(backend,'last_completion',None) or CompletionMetadata(requested_output_limit=limit)
                    self.last_failure_type = FailureType.TIMEOUT if isinstance(exc,(httpx.TimeoutException,TimeoutError)) else FailureType.UNKNOWN
                    self.emit('MODEL_COMPLETION',self.last_completion.to_dict())
                    self.emit('FAILURE_CLASSIFIED',{'failure_type':str(self.last_failure_type),'status':str(self.last_failure_type)})
                    if retrying:
                        self.emit('RESOURCE_RETRY_FAILED',{'status':'Resource-Retry abgebrochen' if isinstance(exc,asyncio.CancelledError) else 'Resource-Retry fehlgeschlagen','failure_type':str(self.last_failure_type)})
                    raise
            self.check_stop()
            if not isinstance(response,dict):
                raise ValueError('Modell hat eine ungültige Antwort geliefert.')
            typed = isinstance(response,ModelResponse)
            response = response if typed else ModelResponse(response,completion=CompletionMetadata(requested_output_limit=limit))
            if self.diagnostic_trace:self.diagnostic_trace.received(response)
            raw_proposal_hashes=None
            if (structured_protocol or self.response_contract.mode==ResponseMode.FILE_CONTENT) and isinstance(response.get('content'),str):
                try:
                    from .contracts import remove_json_fence
                    raw_value=json.loads(remove_json_fence(response['content'])[0])
                    if isinstance(raw_value,dict) and self.response_contract.mode==ResponseMode.FILE_CONTENT and isinstance(raw_value.get('content'),str):
                        raw_proposal_hashes=[hashlib.sha256(raw_value['content'].encode('utf-8')).hexdigest()]
                    elif isinstance(raw_value,dict) and isinstance(raw_value.get('proposed_files'),list):
                        raw_proposal_hashes=[hashlib.sha256(p['content'].encode('utf-8')).hexdigest() for p in raw_value['proposed_files'] if isinstance(p,dict) and isinstance(p.get('content'),str)]
                except (ValueError,TypeError):pass
            response.update(transport_redactor.clean(dict(response)))
            self.last_completion = response.completion
            self.last_response = response
            self.emit('MODEL_COMPLETION',response.completion.to_dict())
            for kind in getattr(response,'normalizations',[]):
                self.protocol_normalized=True
                self.emit('PROTOCOL_NORMALIZED',{'normalization_type':kind})
            if response.get('content') is not None and not isinstance(response.get('content'),str):
                raise ValueError('Modell hat statt einer Textantwort ein ungültiges Format geliefert.')
            counts.output_tokens_estimate += (size(response)+2)//3
            calls = response.get('tool_calls') or []
            if not isinstance(calls,list) or any(not isinstance(c,dict) or not isinstance(c.get('id'),str) for c in calls):
                raise ValueError('Modell hat ungültige Werkzeugaufrufe geliefert.')
            self.emit('MODEL_RESPONSE',{'tool_count':len(calls),'answer':response.get('content') or '',
                                      'duration':round(time.monotonic()-started,4)})
            if calls and self.response_contract.mode==ResponseMode.FILE_CONTENT:
                self.file_content_protocol_status='VERIFIED_FAILURE'
                if len(calls)>8:
                    self.last_failure_type=FailureType.TOOL_FAILURE
                    self.emit('FAILURE_CLASSIFIED',{'failure_type':'TOOL_FAILURE','subreason':'TOOL_CALL_LIMIT','requested_calls':len(calls),'executed_calls':0,'status':'Zu viele Werkzeugaufrufe in einem Modellschritt (maximal 8).'})
                    raise CompletionRejected(FailureType.TOOL_FAILURE,'Zu viele Werkzeugaufrufe in einem Modellschritt (maximal 8).')
                raise CompletionRejected(FailureType.FORMAT_FAILURE,'FILE_CONTENT erlaubt keine Modelltools oder Zielpfade.')
            if calls:
                # Tool calls are intermediate, never final validated successes.
                return response
            # Normalize decoration and extract payload before either contract/task validator.
            protocol_error=None;controller_integrity_error=False
            try:
                if self.response_contract.mode==ResponseMode.FILE_CONTENT:
                    from .contracts import NormalizedResponse
                    def unique(pairs):
                        out={}
                        for key,value in pairs:
                            if key in out:raise ValueError('duplicate key')
                            out[key]=value
                        return out
                    if len(self.write_scope.allowed_paths)!=1:
                        controller_integrity_error=True
                        raise CompletionRejected(FailureType.INFORMATION_MISSING,'Invalid controller FILE_CONTENT target scope; no write.')
                    try:
                        value=json.loads(response.content or '',object_pairs_hook=unique)
                        if not isinstance(value,dict) or set(value)!={'content'} or not isinstance(value['content'],str) or not value['content'].strip() or len(value['content'].encode('utf-8'))>262144 or value['content'].lstrip().startswith('```'):raise ValueError('invalid content')
                        normalized=NormalizedResponse('Dateiinhalt vorbereitet.',[{'path':self.write_scope.allowed_paths[0],'content':value['content']}])
                    except (ValueError,TypeError):raise CompletionRejected(FailureType.FORMAT_FAILURE,'FILE_CONTENT benötigt ausschließlich JSON content ohne weitere Felder.')
                else:normalized=normalize_response(response.content or '',self.response_contract,allow_proposals=structured_protocol)
                response['content']=normalized.payload
                response.proposed_files=normalized.proposed_files
                canonical_hashes=[hashlib.sha256(p['content'].encode('utf-8')).hexdigest() for p in response.proposed_files]
                if canonical_hashes and raw_proposal_hashes!=canonical_hashes:
                    controller_integrity_error=True
                    raise CompletionRejected(FailureType.INFORMATION_MISSING,'Controller changed proposal content before validation; no write.')
                if canonical_hashes:
                    # An independently displayed code block must not contradict the accepted file.
                    blocks=re.findall(r'```[^\n]*\n([\s\S]*?)\n```',normalized.payload)
                    if blocks and (len(blocks)!=len(response.proposed_files) or any(x.rstrip('\r\n')!=p['content'].rstrip('\r\n') for x,p in zip(blocks,response.proposed_files))):
                        raise CompletionRejected(FailureType.FORMAT_FAILURE,'Angezeigter Code widerspricht dem Dateivorschlag; kein Write.')
                    response.proposal_hashes=canonical_hashes
                    for p,h in zip(response.proposed_files,canonical_hashes):
                        self.emit('PROPOSAL_INTEGRITY',{'path':p['path'],'raw_proposed_file_content_sha256':h,'canonical_parsed_content_sha256':h,'encoding':'UTF-8','newline_normalization':'none'})
                response.envelope_status=normalized.status
                self.protocol_normalized |= normalized.protocol_normalized
                for kind in normalized.normalizations:self.emit('PROTOCOL_NORMALIZED',{'normalization_type':kind})
            except CompletionRejected as exc:
                protocol_error=exc
            contract_validation=JSONValidator().validate(task,response) if self.response_contract.expects_json else ValidationResult()
            validator=self.validator or validator_for_task(task)
            validation=(contract_validation if contract_validation.valid is False else validator.validate(task,response) if validator else response.protocol_validation or contract_validation)
            if protocol_error:validation=ValidationResult(False,False,True,FailureType.FORMAT_FAILURE,{'protocol_error':str(protocol_error)},'protocol')
            # A backend protocol error still describes its outer envelope, not domain payload.
            if response.protocol_validation and response.protocol_validation.valid is False:validation=response.protocol_validation
            if response.protocol_validator:
                validation=response.protocol_validator.validate(task,response)
            complete=self.audit_information(task,messages) and not controller_integrity_error
            if self.response_contract.mode==ResponseMode.FILE_CONTENT:
                reason=(response.finish_reason or '').casefold()
                if not complete or controller_integrity_error:self.file_content_protocol_status=None
                elif response.protocol_validation and response.protocol_validation.valid is False:self.file_content_protocol_status=None
                elif response.protocol_validator and validation.valid is False:self.file_content_protocol_status=None
                elif protocol_error is None:self.file_content_protocol_status='VERIFIED_SUCCESS'
                elif reason in {'stop','end_turn'}:self.file_content_protocol_status='VERIFIED_FAILURE'
                else:self.file_content_protocol_status=None
            if controller_integrity_error:self.information_complete=False
            action=bool(self.workspace_discovery or not self.write_scope.read_only)
            disclaimer=bool(re.search(r"\b(?:(?:i (?:cannot|can't)|i(?: am|'m) unable to)\s+(?:directly\s+)?(?:access|modify|edit|use|execute)|i(?:'m| am)\s+(?:only\s+)?(?:a\s+)?text.based|ich bin.{0,35}textbasiert|ich kann.{0,35}(?:keine dateien|nicht.{0,12}dateien))",response.content or '',re.I))
            if action and complete and validation.valid is not False and disclaimer and len(response.content or '')<1200 and not response.proposed_files:
                validation=ValidationResult(False,True,True,FailureType.MODEL_CAPABILITY_FAILURE,{'controller_path_available':True,'capability_disclaimer':True},'action')
            if self.intent_resolution and self.intent_resolution.handled and self.intent_resolution.intent_id==IntentID.CREATE_FILE and not self.written_paths and not getattr(response,'proposed_files',[]) and validation.valid is not False:
                validation=ValidationResult(False,True,False,FailureType.TOOL_FAILURE,{'subreason':'CREATE_REQUIRES_ACTUAL_WRITE'},'tool')
            if self.response_contract.mode==ResponseMode.TOOL_ACTION and not self.resources.counters.tool_calls:
                validation=ValidationResult(False,True,False,FailureType.TOOL_FAILURE,{'subreason':'TOOL_ACTION_MISSING'},'tool')
            if not complete and self.last_failure_type not in {FailureType.TOOL_FAILURE,FailureType.PERMISSION_FAILURE}:
                validation=ValidationResult(False,True,False,FailureType.INFORMATION_MISSING,{'information_complete':False},'information')
            response.payload_validation=validation
            if self.diagnostic_trace:self.diagnostic_trace.record('VALIDATOR',{'declared_task_validator':type(validator).__name__ if validator else None,'response_protocol_validator':type(response.protocol_validator).__name__ if response.protocol_validator else None,'input_type':type(response.get('content')).__name__,'contract_result':asdict(contract_validation),'payload_result':asdict(validation)})
            self.last_validation = validation
            failure = FailureType.INFORMATION_MISSING if not complete else self.resources.classify_completion(response,validation)
            self.last_failure_type = failure
            self.validation_history.append({'validation':asdict(validation),'completion':response.completion.to_dict(),'failure_type':str(failure)})
            self.emit('VALIDATION_RESULT',{**asdict(validation),'status':'Ergebnis vollständig' if validation.valid else 'Antwort ist unvollständig' if validation.complete is False else 'Antwort ohne objektive Prüfung'})
            self.emit('FAILURE_CLASSIFIED',{'failure_type':str(failure),'status':str(failure)})
            if failure==FailureType.SUCCESS:
                if response.finish_reason.casefold()=='length':
                    self.emit('OUTPUT_BUDGET_NEAR_LIMIT',{'requested_limit':limit,'actual_output_tokens':response.completion.output_tokens})
                if retrying:
                    self.emit('RESOURCE_RETRY_SUCCESS',{'same_model':True,'successful_limit':limit,
                        'actual_success_output_tokens':response.completion.output_tokens,'status':'Ergebnis vollständig · Resource-Retry erfolgreich'})
                return response
            if failure==FailureType.RESOURCE_OUTPUT_LIMIT:
                self.emit('RESOURCE_OUTPUT_LIMIT',{'model':response.completion.model or self.resources.model,'finish_reason':response.finish_reason,
                    'requested_limit':limit,'actual_output_tokens':response.completion.output_tokens,'validator_complete':validation.complete,
                    'status':f'Modell erreichte Output-Limit: {limit} Tokens · Antwort ist unvollständig'})
                next_limit,blocked,reason = self.resources.next_output_budget(limit,policy,
                    input_tokens=response.completion.input_tokens,context_limit=self.known_context_limit)
                if counts.model_calls >= self.model_call_limit:
                    next_limit,blocked,reason = None,FailureType.RESOURCE_OUTPUT_LIMIT,'Gesamtes Modell-Schrittlimit erreicht.'
                if next_limit is not None:
                    # Retain original task/context, not the truncated candidate. Validate cached file stamps.
                    for key in self.resources.cache:
                        try:
                            relative = self.tools.workspace.path(str(Path(key).relative_to(self.workspace.root)))
                            current = self.resources.cached_read(relative)
                        except OSError:
                            current = None
                        if current is None:
                            self.last_failure_type = FailureType.INFORMATION_MISSING
                            raise CompletionRejected(FailureType.INFORMATION_MISSING,'Gelesene Datei hat sich vor dem Retry verändert.')
                    self.check_stop()
                    self.on_resource_failure()
                    self.check_stop()
                    counts.resource_retries += 1
                    retrying = True
                    self.emit('RESOURCE_RETRY_STARTED',{'old_limit':limit,'new_limit':next_limit,'same_model':True,
                        'status':f'Erhöhe Output-Budget einmalig auf {next_limit} · gleiches Modell wird erneut verwendet'})
                    self.check_stop()
                    limit = next_limit
                    continue
                self.last_failure_type = blocked
                self.emit('FAILURE_CLASSIFIED',{'failure_type':str(blocked),'status':reason})
                if retrying:
                    self.emit('RESOURCE_RETRY_FAILED',{'failure_type':str(blocked),'status':'Resource-Retry fehlgeschlagen'})
                raise CompletionRejected(blocked,reason)
            if retrying:
                self.emit('RESOURCE_RETRY_FAILED',{'failure_type':str(failure),'status':'Resource-Retry fehlgeschlagen'})
            if validation.valid is False or failure==FailureType.TOOL_FAILURE:
                raise CompletionRejected(failure,'Vorgegebenes Ausgabeformat bzw. Validator nicht erfüllt.')
            return response

    def finish(self, task, answer):
        if self.resources.counters.model_calls>getattr(self,'turn_model_calls_before',0):
            false_claim=re.search(r"(?i)\b(?:i\s+(?:can(?:not|'t)|am unable to)\s+(?:create|write|edit|modify)\s+(?:any\s+)?(?:files?|directories|folders?)|i\s+can\s+only\s+(?:read|show)\s+files?|ich\s+kann\s+(?:keine|nicht)\s+(?:dateien|ordner)\s+(?:erstellen|schreiben|ändern))\b",answer)
            if false_claim and {'write_file','make_directory'} <= set(self.tools.tools):
                from .direct_responses import capability_answer
                self.emit('FAILURE_CLASSIFIED',{'source':'MODEL_OUTPUT','subreason':'CONTROLLER_GUARD'})
                answer='Die Modellaussage über globale Dateifähigkeiten war unzutreffend.\n\n'+capability_answer(self)
        if self.resources.counters.model_calls>getattr(self,'turn_model_calls_before',0) and self.resources.counters.memory_writes<=getattr(self,'turn_memory_writes_before',0) and (re.search(r'(?i)\b(?:memory|merke|merk|erinner|remember|gedächtnis)\w*',task+' '+answer) or re.search(r'(?i)\b(?:ich merke mir|habe mir gemerkt)\b',answer)) and re.search(r'(?i)\b(?:gespeichert|gemerkt|notiert|remembered|saved|ich merke mir|habe mir gemerkt)\b',answer):
            answer='Für diese Anfrage wurde keine dauerhafte Memory-Speicherung bestätigt.'
        if any(p.casefold().endswith(('.c','.cpp','.cc','.cxx')) for p in self.written_paths):
            from .i18n import localize
            answer+='\n\n'+localize('VERIFICATION_CPP_NOT_RUN',self.settings.ui.language)
        if self.workspace_discovery and not self.response_contract.expects_json:
            paths=', '.join(f['path'] for f in self.workspace_discovery.files)
            from .i18n import localize
            footer='\n\n'+localize('ANALYSIS_SCOPE_FOOTER',self.settings.ui.language,paths=paths)
            if not answer.endswith(footer):answer+=footer
        self.session.history.extend([{"role": "user", "content": task}, {"role": "assistant", "content": answer}])
        self.session.history = self.session.history[-12:]
        return answer

    async def read_file_direct(self, task, relative):
        self.emit("CONTROLLER_ACTION", {"action": "read_file", "path": relative,
            "status": "Dateiinhalt direkt anzeigen · kein Modell erforderlich"})
        result = await self.execute_tool("read_file", {"path": relative}, origin="controller")
        self.emit("TOOL_RESULT", {"name": "read_file", "result": result, "origin": "controller"})
        self.check_stop()
        from .i18n import localize
        warning=localize('SENSITIVE_FILE_WARNING',self.settings.ui.language)+'\n\n' if SensitiveFilePolicy.sensitive(relative) else ''
        return self.finish(task, warning+format_file_contents(relative, result["content"]))

    async def list_workspace_direct(self,task):
        self.check_stop()
        self.emit('CONTROLLER_ACTION',{'action':'list_files','path':'.','status':'Workspace-Dateiliste direkt anzeigen · kein Modell erforderlich'})
        try:
            result=await self.execute_tool('list_files',{'path':'.'},origin='controller')
        except (OSError,ValueError) as exc:
            self.information_complete=False
            failure=FailureType.PERMISSION_FAILURE if isinstance(exc,PermissionError) else FailureType.INFORMATION_MISSING if isinstance(exc,FileNotFoundError) else FailureType.TOOL_FAILURE
            self.last_failure_type=failure
            raise CompletionRejected(failure,str(exc)) from exc
        self.emit('TOOL_RESULT',{'name':'list_files','result':result,'origin':'controller'})
        self.check_stop()
        lines=[entry['path']+(' /' if entry['directory'] else '') for entry in result['entries']]
        from .i18n import localize
        answer=localize('DIRECTORY_LIST_HEADER',self.settings.ui.language)+'\n'+('\n'.join(lines) if lines else localize('DIRECTORY_LIST_EMPTY',self.settings.ui.language))
        if result.get('truncated'):answer+='\n'+localize('DIRECTORY_LIST_TRUNCATED',self.settings.ui.language)
        self.information_complete=not result.get('truncated',False)
        self.last_failure_type=FailureType.SUCCESS
        self.last_validation=ValidationResult(True,self.information_complete,True,FailureType.SUCCESS,{'listing_executed':True},'tool')
        self.emit('VALIDATION_RESULT',{**asdict(self.last_validation),'status':'Reale Dateiliste direkt ausgegeben'})
        self.emit('FAILURE_CLASSIFIED',{'failure_type':'SUCCESS','status':'Dateiliste ohne Modellaufruf vollständig ausgegeben'})
        return self.finish(task,answer)

    async def ensure_workspace_discovery(self):
        if self.workspace_operation!='analysis' or self.workspace_discovery:return
        from .discovery import discover
        self.information_complete=False
        self.emit('WORKSPACE_DISCOVERY_STARTED',{'status':'Untersuche begrenzt Struktur und relevante Textdateien'})
        async def execute(name,arguments):
            result=await self.execute_tool(name,arguments,origin='controller')
            self.emit('TOOL_RESULT',{'name':name,'result':result,'origin':'controller'})
            return result
        try:
            self.workspace_discovery=await discover(self.workspace,execute,self.check_stop,self.settings.agent.context_budget,task=self.current_task,start=getattr(self,'discovery_root','.'))
        except (OSError,ValueError,CompletionRejected) as exc:
            failure=exc.failure_type if isinstance(exc,CompletionRejected) else FailureType.PERMISSION_FAILURE if isinstance(exc,PermissionError) else FailureType.INFORMATION_MISSING if isinstance(exc,FileNotFoundError) else FailureType.TOOL_FAILURE
            self.last_failure_type=failure
            self.emit('FAILURE_CLASSIFIED',{'failure_type':str(failure),'status':str(exc)})
            raise CompletionRejected(failure,str(exc)) from exc
        self.check_stop()
        self.required_input_paths.extend(f['path'] for f in self.workspace_discovery.files)
        if self.write_scope.pending_source_targets:
            self.write_scope.resolve_sources(self.workspace_discovery.files)
            if not self.write_scope.allowed_paths:
                self.information_complete=False
                raise CompletionRejected(FailureType.INFORMATION_MISSING,'Keine relevante gelesene Source-Datei als Schreibziel gefunden.')
            self.emit('WRITE_SCOPE_RESOLVED',{'write_scope':asdict(self.write_scope),'status':'Schreibziele auf tatsächlich gelesene Source-Dateien begrenzt'})
        self.emit('WORKSPACE_DISCOVERY_COMPLETED',{**self.workspace_discovery.metadata(),'status':'Begrenzte Projektdaten für die Analyse beschafft'})

    async def prepare_verification(self):
        if self.verification and not self.write_scope.read_only:
            await self.verification.prepare(self.write_scope.allowed_paths)

    async def verify_written(self):
        if self.verification:return await self.verification.post(self.changed_paths)

    async def project_check(self):
        from .verification import VerificationRunner
        from .task_state import findings_from_verification
        await self.ensure_workspace_discovery()
        discovery=self.workspace_discovery;names=[f['path'] for f in discovery.files]
        markers=set(discovery.discovered);types=[]
        if 'project.godot' in markers or any(p.endswith(('.gd','.tscn','.tres')) for p in names):types.append('Godot')
        if any(p.endswith('.py') for p in names) or markers&{'pyproject.toml','pytest.ini'}:types.append('Python')
        if 'package.json' in markers:types.append('JavaScript (kein Verifier in dieser Version)')
        results=[];findings=[];plans=[]
        for suffixes in [('.py',),('.gd','.tscn','.tres')]:
            selected=[p for p in names if p.endswith(suffixes)]
            if not selected:continue
            self.check_stop()
            runner=VerificationRunner(self.workspace,self.settings.verification,self.check_stop,
                lambda name,args,path:self.permission(name,args,path) if self.permission else asyncio.sleep(0,result=False),self.emit)
            result=await runner.inspect(selected);results.append(result)
            if runner.plan:plans.append(asdict(runner.plan))
            findings.extend(findings_from_verification(result,self.workspace,self.task_id,len(findings)+1))
            if self.verification.plan is None:self.verification=runner
        self.project_findings=findings;self.project_verifications=results
        self.information_complete=True
        self.last_failure_type=FailureType.UNKNOWN
        lines=['Projektprüfung — begrenzter Umfang, keine vollständige Projektprüfung.',
               'Projektart: '+(', '.join(types) or 'nicht eindeutig erkannt'),'Verifizierte Probleme:']
        lines+=['- ['+f.finding_id+'] '+f.summary for f in findings] or ['- Keine durch die ausgeführten Checks belegten Probleme. Das bedeutet nicht, dass das Projekt bugfrei ist.']
        lines+=['Unbestätigte Verdachtsstellen: keine zusätzliche freie Modellanalyse ausgeführt.','Ausgeführte Checks:']
        lines+=['- '+r.verifier_type+': '+r.verification_status+' — '+r.message for r in results] or ['- UNAVAILABLE: kein unterstützter kleiner Prüfumfang gefunden.']
        lines+=['Untersuchte Dateien: '+', '.join(names),'Schreibtools: keine; Quell- und Projektdateien bleiben unverändert.']
        self.emit('PROJECT_CHECK_COMPLETED',{'task_id':self.task_id,'intent':'VERIFY_EXISTING_PROJECT','workspace':str(self.workspace.root),'discovered_files':discovery.discovered[:40],'inspected_files':names,'verification_plans':plans,'verification_results':[r.compact() for r in results],'findings':[asdict(f) for f in findings],'project_complete':False,'task_state_generation':self.session.task_state.generation+1,'status':'Projektprüfung abgeschlossen — '+str(len(findings))+' verifizierte Befunde'})
        return self.finish(self.current_task,'\n'.join(lines))

    def update_task_state(self,task,answer):
        from .task_state import Finding,findings_from_verification
        from .intents import information_need
        findings=list(self.project_findings);verification=[r.compact() for r in self.project_verifications]
        files=[f['path'] for f in self.workspace_discovery.files] if self.workspace_discovery else list(self.required_input_paths)
        if self.changed_paths and self.verification:
            findings=findings_from_verification(self.verification.result,self.workspace,self.task_id)
            verification=[self.verification.result.compact()]
        elif information_need(task)!='VERIFY_EXISTING_PROJECT' and self.write_scope.read_only and self.response_contract.mode in {ResponseMode.ANALYSIS_RESULT,ResponseMode.PLAIN_TEXT} and (self.response_contract.mode==ResponseMode.ANALYSIS_RESULT or self.workspace_discovery):
            findings=[Finding('SUS-001',answer[:500],files[:5],'MODEL_ANALYSIS',self.task_id,'SUSPECTED',self.task_id)]
            answer='Unbestätigte Modellanalyse / Verdachtsstellen — nicht objektiv als Bugs verifiziert:\n\n'+answer
        files=list(dict.fromkeys(files+self.written_paths))[:8]
        self.session.task_state.commit(self.workspace,self.task_id,information_need(task) or ('FOLLOWUP_FIX' if self.resolved_finding_ids else 'TASK'),files,verification,findings,asdict(self.write_scope))
        self.emit('SESSION_TASK_STATE',{'task_id':self.task_id,'generation':self.session.task_state.generation,'finding_ids':[f.finding_id for f in findings],'finding_status':[f.status for f in findings],'project_complete':False})
        return answer

    async def execute_tool(self, name, arguments, origin="model"):
        """Shared pipeline for native calls AND controller-delegated tools."""
        self.check_stop()
        self.resources.counters.tool_calls += 1
        self.emit("TOOL_REQUEST", {"name": name, "arguments": arguments, "origin": origin})
        try:
            if name not in self.tools.tools:
                if self.diagnostic_trace:self.diagnostic_trace.record('TOOL_GUARD',{'name':name,'dispatch':False,'reason':'INVALID_TOOL_NAME'})
                raise ValueError(f'Unbekanntes Werkzeug: {name}')
            if origin=='model' and self.active_tool_names is not None and name not in self.active_tool_names:
                raise ToolArgumentInvalid(f'{name}: Werkzeug für diese Aufgabe nicht angeboten')
            if name=="read_file" and origin=="controller" and direct_read_path(getattr(self,"current_task",""))==arguments.get("path") and SensitiveFilePolicy.sensitive(arguments["path"]):
                self.tools.sensitive_read_path=self.workspace.path(arguments["path"]).relative_to(self.workspace.root).as_posix()
                self.emit("SECRET_POLICY_REDACTED_READ",{"path":arguments["path"],"status":"Warnung: sensible Datei, ausschließlich redaktierte Struktur sichtbar."})
            path = self.tools.validate(name, arguments)
            if origin=='model' and name=='read_file':
                read_paths=set(self.required_input_paths)
                if self.write_scope:read_paths.update(self.write_scope.allowed_paths)
                if self.workspace_discovery:read_paths.update(f['path'] for f in self.workspace_discovery.files)
                if read_paths and path not in {self.workspace.path(p) for p in read_paths}:
                    self.last_failure_type=FailureType.PERMISSION_FAILURE
                    raise PermissionError('Datei außerhalb des vom Userauftrag/Discovery begrenzten ReadScope.')
            if self.tools.tools[name].risk_level!='read' and self.write_scope is not None:
                if not self.write_scope.permits(arguments['path'],self.workspace,directory=name=='make_directory'):
                    self.last_failure_type=FailureType.PERMISSION_FAILURE
                    self.emit('TOOL_DENIED',{'name':name,'path':str(path),'subreason':'WRITE_SCOPE'})
                    raise ToolDenied('Schreibziel außerhalb des erlaubten WriteScope; keine Ausführung.')
        except PermissionError:
            if self.diagnostic_trace:self.diagnostic_trace.record('TOOL_GUARD',{'name':name,'dispatch':False,'reason':'PATH_OR_SCOPE_POLICY'})
            self.last_failure_type=FailureType.PERMISSION_FAILURE
            self.dependency_failure=FailureType.PERMISSION_FAILURE
            raise
        except FileNotFoundError:
            if self.diagnostic_trace:self.diagnostic_trace.record('TOOL_GUARD',{'name':name,'dispatch':False,'reason':'INFORMATION_MISSING'})
            self.last_failure_type=FailureType.INFORMATION_MISSING
            self.information_complete=False
            raise
        except ToolArgumentInvalid as exc:
            if self.diagnostic_trace:self.diagnostic_trace.record('TOOL_GUARD',{'name':name,'dispatch':False,'reason':'INVALID_ARGUMENTS','subreason':exc.subreason})
            self.last_failure_type=FailureType.TOOL_FAILURE
            self.dependency_failure=FailureType.TOOL_FAILURE
            self.emit('FAILURE_CLASSIFIED',{'failure_type':'TOOL_FAILURE','subreason':exc.subreason,'status':str(exc)})
            raise
        allowed = self.tools.tools[name].risk_level == "read"
        if not allowed and self.permission:
            self.emit("AGENT_STEP", {"status": "Warte auf Erlaubnis"})
            allowed = await self.permission(name, arguments, path)
        self.check_stop()
        if not allowed:
            self.last_failure_type = FailureType.PERMISSION_FAILURE
            self.emit("TOOL_DENIED", {"name": name, "path": str(path)})
            self.emit('FAILURE_CLASSIFIED',{'failure_type':'PERMISSION_FAILURE','status':'Aktion nicht freigegeben'})
            result = {"error": "Aktion vom Benutzer oder der Richtlinie abgelehnt. Nicht erneut versuchen."}
            self.emit("TOOL_RESULT", {"name": name, "result": result})
            previous = " Bereits zuvor geschrieben: " + ", ".join(self.written_paths) + "." if self.written_paths else ""
            raise ToolDenied(f"Aktion abgelehnt: {name} für {arguments.get('path', '')}. Diese Aktion wurde nicht ausgeführt." + previous)
        self.emit("TOOL_APPROVED", {"name": name, "path": str(path)})
        before=None
        if name=='write_file':
            await self.prepare_verification()
            before_hash=hashlib.sha256(b'').hexdigest()
            before=b''
            if path.exists():
                with path.open('rb') as stream:before_hash=hashlib.file_digest(stream,'sha256').hexdigest()
                with path.open('rb') as stream:before=stream.read(262144)
        if name=='write_file' and not path.parent.exists():
            # Only ancestors of an explicitly allowed new file; normal permissions apply.
            await self.execute_tool('make_directory',{'path':path.parent.relative_to(self.workspace.root).as_posix()},origin='controller')
        label = {"read_file": "Lese Datei", "write_file": "Schreibe Datei", "list_files": "Liste Ordner", "search_files": "Durchsuche Dateien", "make_directory": "Erstelle Ordner"}[name]
        self.emit("AGENT_STEP", {"status": f"{label}: {arguments.get('path', arguments.get('directory',''))}"})
        if origin=='model' and self.workspace_discovery:
            if name=='list_files':
                try:result=self.workspace_discovery.cached_listing(arguments['path'],self.workspace)
                except ValueError as exc:raise ToolArgumentInvalid(str(exc)) from exc
                self.resources.counters.cache_hits+=1
                return result
            if name=='read_file':
                allowed_paths={self.workspace.path(f['path']) for f in self.workspace_discovery.files}
                if path not in allowed_paths:raise ToolArgumentInvalid('Datei außerhalb des begrenzten Discovery-Snapshots')
                cached=self.resources.cached_read(path)
                if cached is None:raise FileNotFoundError('Discovery-Datei ist nicht mehr unverändert verfügbar.')
                return cached
        result = self.resources.cached_read(path) if name == "read_file" else None
        if result is None:
            result = self.diagnostic_trace.dispatch(self.tools.execute,name,arguments) if self.diagnostic_trace else self.tools.execute(name, arguments)
            if name=='write_file':
                after=path.read_bytes();self.emit('PROPOSAL_WRITTEN_HASH',{'path':arguments['path'],'written_bytes_sha256':hashlib.sha256(after).hexdigest()});diff=''.join(difflib.unified_diff(before.decode('utf-8',errors='replace').splitlines(keepends=True),after.decode('utf-8',errors='replace').splitlines(keepends=True),fromfile='before/'+arguments['path'],tofile='after/'+arguments['path']))
                if before_hash!=hashlib.sha256(after).hexdigest():self.changed_paths.append(arguments['path'])
                self.emit('FILE_CHANGE',{'path':arguments['path'],'before_sha256':before_hash,'after_sha256':hashlib.sha256(after).hexdigest(),'diff':diff[:8192],'diff_sha256':hashlib.sha256(diff.encode()).hexdigest(),'diff_truncated':len(diff)>8192})
            if name == "read_file":
                self.resources.remember_read(path, result)
            elif name in {"write_file", "make_directory"}:
                self.resources.cache.clear()
            if name == "write_file":
                self.written_paths.append(arguments["path"])
        return result

    async def run(self, task):
        memory_request=bool(re.match(r'(?i)^\s*(?:merk(?:e)?\s+dir|remember)\b',task))
        if memory_request:
            from .compound_tasks import memory_payload
            task=memory_payload(task)
        sensitive=memory_request and redactor.sensitive_memory(task)
        self.semantic_usage={"semantic_resolver_calls":0,"semantic_resolver_elapsed_ms":0.0,"semantic_resolver_model":"","semantic_resolver_input_size":0}
        self.turn_memory_writes_before=self.resources.counters.memory_writes
        self.turn_model_calls_before=self.resources.counters.model_calls
        trace=None;token=None
        if self.diagnostic_store and self.settings.diagnostic_trace:
            trace=self.diagnostic_store.begin(self,task,getattr(self,'diagnostic_session_id',None),getattr(self,'diagnostic_project_id',None))
        self.diagnostic_trace=trace
        if trace:token=trace.activate()
        try:
            if memory_request:self.emit('MEMORY_SECURITY',{'memory_write_requested':True,'rejected_sensitive':sensitive})
            if sensitive:
                from .i18n import localize
                answer=localize('MEMORY_SENSITIVE_REJECTED',self.settings.ui.language)
                self.emit('DIRECT_RESPONSE',{'source':'CONTROLLER','intent_id':'MEMORY_SECURITY','model_calls':0})
            else:
                from .compound_tasks import split_compound
                plan=split_compound(task) if not memory_request else None
                if plan and plan.clarification:
                    self.emit('FAILURE_CLASSIFIED',{'source':'CONTROLLER','subreason':'TASK_DECOMPOSITION'})
                    answer=plan.clarification
                elif plan:
                    self.emit('COMPOUND_PLAN',{'steps':[{'task':x.task,'independent':x.independent} for x in plan.steps],'max_steps':3})
                    answers=[];failed=False
                    for index,step in enumerate(plan.steps):
                        if failed and not step.independent:break
                        self.turn_memory_writes_before=self.resources.counters.memory_writes
                        self.turn_model_calls_before=self.resources.counters.model_calls
                        self.emit('COMPOUND_STEP',{'index':index+1,'status':'started'})
                        try:
                            part=await self._run_with_session(step.task)
                            failure=getattr(self,'last_failure_type',FailureType.UNKNOWN)
                            failed=failure not in {FailureType.SUCCESS,FailureType.UNKNOWN}
                            if self.intent_resolution and self.intent_resolution.ambiguity_reason:failed=True
                            if step.expected_path:
                                target=self.workspace.path(step.expected_path)
                                failed=failed or not (target.is_dir() if step.expected_kind=='directory' else target.is_file())
                            if part.startswith(('Ordner/Projekt nicht gefunden:','Datei nicht gefunden:')):failed=True
                            answers.append(part)
                            self.emit('COMPOUND_STEP',{'index':index+1,'status':'failed' if failed else 'complete'})
                        except asyncio.CancelledError:raise
                        except Exception as exc:
                            failed=True;answers.append('Schritt '+str(index+1)+' konnte nicht abgeschlossen werden: '+redactor.text(str(exc)))
                            self.emit('COMPOUND_STEP',{'index':index+1,'status':'failed'})
                    answer='\n\n'.join(answers)
                else:answer=await self._run_with_session(task)
            if trace:trace.finish(self,answer)
            return answer
        except BaseException as exc:
            if trace:trace.finish(self,str(exc),error=True)
            raise
        finally:
            if trace:trace.deactivate(token)

    async def resolve_semantic(self,task,resolution):
        from .semantic_intent import SemanticIntentResolver,accept_suggestion,IntentSuggestion
        chain={'deterministic_result':asdict(resolution),'fallback_reason':'','semantic_used':False,'suggestion':None,'controller_accepted_intent':False}
        config=self.settings.semantic_resolver
        # Known intent with missing lexical slots needs a question, not path invention.
        if resolution.handled and (resolution.confidence>=0.9 or resolution.ambiguity_reason):
            chain['fallback_reason']='DETERMINISTIC_CLARIFICATION' if resolution.ambiguity_reason else 'DETERMINISTIC_INTENT_KNOWN'
        elif not config.enabled:
            chain['fallback_reason']='SEMANTIC_DISABLED'
        elif not config.model:
            chain['fallback_reason']='SEMANTIC_NOT_CONFIGURED'
            self.emit('SEMANTIC_RESOLVER',{'status':'Semantic resolver model is not available. Deterministic fallback active.','match_status':'UNAVAILABLE'})
        else:
            self.check_stop();chain['fallback_reason']='DETERMINISTIC_LOW_CONFIDENCE';chain['semantic_used']=True
            signature=tuple(asdict(config).items())
            resolver=self.semantic_resolver
            if resolver is None:
                if self.session.semantic_signature!=signature:
                    self.session.semantic_resolver=SemanticIntentResolver(config);self.session.semantic_signature=signature
                resolver=self.session.semantic_resolver
            started=time.monotonic()
            try:suggestion=await resolver.suggest(task)
            except asyncio.CancelledError:raise
            except Exception:
                suggestion=IntentSuggestion(model=config.model,reason='Semantic resolver model is not available.')
            self.check_stop()
            if isinstance(suggestion,IntentSuggestion):
                self.semantic_usage['semantic_resolver_calls']+=max(0,min(1,suggestion.embedding_calls)) if type(suggestion.embedding_calls) is int else 0
                self.semantic_usage['semantic_resolver_input_size']+=max(0,min(131072,suggestion.input_size)) if type(suggestion.input_size) is int else 0
                self.semantic_usage['semantic_resolver_model']=config.model
                self.semantic_usage['semantic_resolver_elapsed_ms']+=round((time.monotonic()-started)*1000,3)
                chain['suggestion']=asdict(suggestion)
            resolved,accepted,reason=accept_suggestion(task,resolution,suggestion)
            chain['controller_accepted_intent']=accepted;chain['controller_decision']=reason
            if accepted:resolution=resolved
            elif isinstance(suggestion,IntentSuggestion) and suggestion.match_status=='AMBIGUOUS':
                from dataclasses import replace
                resolution=replace(resolution,handled=True,ambiguity_reason='Die gewünschte Aktion ist mehrdeutig. Bitte Auftrag und Ziel präzisieren.')
            status='Semantic resolver model is not available. Deterministic fallback active.' if not isinstance(suggestion,IntentSuggestion) or suggestion.match_status=='UNAVAILABLE' else 'Semantic Resolver: '+suggestion.match_status+' (heuristische Ähnlichkeit).'
            self.emit('SEMANTIC_RESOLVER',{'status':status,**self.semantic_usage})
        self.resolver_chain=chain
        from .diagnostics import sanitize
        self.emit('RESOLVER_CHAIN',sanitize(chain))
        return resolution

    async def _run_with_session(self, task):
        # One immediately preceding user-facing failure, ephemeral and workspace-bound.
        state=self.session.task_state;state.bind(self.workspace);self._status_task_state=state
        self.previous_failure_context=state.previous_failure_context;state.previous_failure_context={}
        self.last_failure_type=FailureType.UNKNOWN
        try:answer=await self._run_task(task)
        except asyncio.CancelledError:raise
        except Exception as exc:
            state.previous_failure_context={'task':redactor.text(task)[:200],'reason':redactor.text(str(exc))[:600]}
            raise
        from .task_slots import remember_artifact
        remember_artifact(self,answer)
        r=self.intent_resolution
        if r and r.intent_id!=IntentID.REASON_QUERY and (r.intent_id==IntentID.CREATE_PROJECT or r.ambiguity_reason or self.last_failure_type not in {FailureType.SUCCESS,FailureType.UNKNOWN}):
            state.previous_failure_context={'task':redactor.text(task)[:200],'reason':redactor.text(answer)[:600]}
        return answer

    async def _run_task(self, task):
        task=transport_redactor.text(task)
        task=re.sub(r'(?i)^\s*merk\s+dir(?=\s*:)', 'merke dir', task, count=1)
        if re.match(r"(?i)\s*(?:merke\s+dir|remember)\b",task) and ("<REDACTED>" in task or re.search(r"(?i)api[-_ ]?key|access[-_ ]?token|passwort|password|client_secret|private[-_ ]?key",task)):
            from .i18n import localize
            return localize("MEMORY_SECRET_REFERENCE",self.settings.ui.language)
        self.emit("USER_MESSAGE", {"text": task})
        from .task_state import followup_intent,resolve_followup
        import uuid
        self.task_id=uuid.uuid4().hex;state=self.session.task_state;state.bind(self.workspace)
        self.project_findings=[];self.project_verifications=[];self.resolved_finding_ids=[];self.resolved_from_task_id='';self.resolved_paths=[]
        from .task_slots import continuity
        from .discovery import directory_candidate
        directory=directory_candidate(task);self.discovery_root='.'
        if directory:
            candidate=self.workspace.path(directory)
            self.emit('DIRECTORY_SOURCE',{'lexical_candidate':directory,'exists':candidate.is_dir(),'bounded_discovery':True})
            if not candidate.is_dir():return 'Ordner/Projekt nicht gefunden: '+directory
            self.discovery_root=candidate.relative_to(self.workspace.root).as_posix()
        from .task_slots import lexical_paths
        pending_candidate=bool(state.pending_task and re.fullmatch(r'[\w./\\+-]{1,200}',task.strip()) and lexical_paths(task)==(task.strip(),))
        if state.pending_task and state.pending_task.slots.missing_slots==('content',) and re.fullmatch(r'(?is)\s*(?:ausdrücklicher\s+text|ausdruecklicher\s+text|text|literal\s+text)\s+["“„][^"“”]*["“”]\s*[.!?]?\s*',task):pending_candidate=True
        if state.pending_task and state.pending_task.slots.missing_slots==('change_description',) and re.fullmatch(r'(?is)\s*(?:ersetze(?:\s+den\s+inhalt)?\s+(?:durch|mit)|replace(?:\s+the\s+content)?\s+with)\s+["“„][^"“”]*["“”]\s*[.!?]?\s*',task):pending_candidate=True
        if state.pending_task and state.pending_task.slots.missing_slots==('content',):
            gm=re.fullmatch(r'(?is)\s*(?:generiere|generate)\s*:\s*(.{1,500})\s*',task)
            if gm and not lexical_paths(gm.group(1)):pending_candidate=True
        if state.pending_task and state.pending_task.slots.missing_slots==('content',) and (re.fullmatch(r'(?i)\s*(?:leer|empty|generiert|generated|ausdrücklich|ausdruecklich|literal)\s*[.!?]?\s*',task) or re.fullmatch(r'''(?s)\s*["'“„].*["'”]\s*[.!?]?\s*''',task)):pending_candidate=True
        choice_candidate=bool(state.pending_choice and ' '.join(task.casefold().strip().rstrip('.!?').split()) in {'den inhalt','inhalt','inhalt ändern','den text','content','the content','ganze datei','die ganze datei','die datei','alles','datei','nur text','the whole file','whole file','beides','weiß nicht','weiss nicht','both','ich weiß nicht','ja','nein','das','yes','no'})
        self.emit('PENDING_PRECHECK',{'checked_before_semantic':True,'candidate':pending_candidate or choice_candidate})
        if directory:
            from .intent_resolution import IntentResolution
            r=IntentResolution(IntentID.ANALYZE_DIRECTORY,1.,source='controller-directory',handled=True,project_scope='workspace')
        elif pending_candidate or choice_candidate:
            from .intent_resolution import IntentResolution
            r=IntentResolution()
        else:r=self.intent_resolver.resolve(task)
        # Previous clarification is consumed once, separately from operational findings.
        state.previous_failure_context=self.previous_failure_context
        r,task=continuity(state,task,r,self.workspace,self.task_id,self.emit)
        state.previous_failure_context={}
        r=await self.resolve_semantic(task,r)
        self.intent_resolution=bind_recent(r,state,self.workspace);self.resolution_task=task
        if self.intent_resolution.followup_reference and self.intent_resolution.slots:
            from dataclasses import replace
            self.intent_resolution=replace(self.intent_resolution,slots=replace(self.intent_resolution.slots,source_paths=self.intent_resolution.referenced_paths,slot_sources={'source_paths':'SESSION_RECENT_FILE'}))
        self.intent_resolution=find_explicit_targets(self,self.intent_resolution)
        from .diagnostics import sanitize
        self.emit('TASK_SLOTS',sanitize(asdict(self.intent_resolution.slots)) if self.intent_resolution.slots else {})
        if self.intent_resolution.ambiguity_reason:
            self.emit('INTENT_AMBIGUITY',{'reason':self.intent_resolution.ambiguity_reason})
            # Presentation is localized only after the controller has fixed the slots.
            from .task_slots import clarify,ContentMode
            from .i18n import localize
            slots=self.intent_resolution.slots
            if slots and self.intent_resolution.ambiguity_reason==clarify(slots):
                key=('AMBIGUOUS_TARGET' if 'target_path' in slots.ambiguous_slots else 'MISSING_TARGET_PATH' if 'target_path' in slots.missing_slots else 'MISSING_MUTATION' if 'explicit_mutation_request' in slots.missing_slots else 'MISSING_ARTIFACT' if slots.content_mode==ContentMode.EXISTING else 'MISSING_CONTENT_MODE')
                return localize(key,self.settings.ui.language)
            return self.intent_resolution.ambiguity_reason
        if self.intent_resolution.followup_reference and self.intent_resolution.missing_change_description:
            return 'Welche Änderung soll ich an '+', '.join(self.intent_resolution.referenced_paths)+' vornehmen?'
        self.emit('INTENT_RESOLVED',asdict(self.intent_resolution))
        from .direct_responses import direct_response
        direct=direct_response(self,task)
        if direct is not None:return direct
        if followup_intent(task):
            effective,message,ids=resolve_followup(state,self.workspace)
            self.emit('FOLLOWUP_RESOLVED' if effective else 'FOLLOWUP_UNRESOLVED',{'task_id':self.task_id,'resolved_from_task_id':state.last_task_id,'resolved_finding_ids':ids,'status':message})
            if not effective:return message
            self.resolved_from_task_id=state.last_task_id;self.resolved_finding_ids=ids
            self.resolved_paths=list(dict.fromkeys(p for f in state.current_findings if f.finding_id in ids for p in f.repair_targets))[:3]
            task=effective
        else:state.clear_findings()
        try:
            answer=await self._run(task)
            await self.verify_written()
            return self.update_task_state(task,answer)
        except asyncio.CancelledError:
            self.emit("STOP", {"reason": "Vom Benutzer gestoppt. Weitere Tools gesperrt."})
            raise
        except Exception as exc:
            self.emit("ERROR", {"message": str(exc), "type": type(exc).__name__})
            raise

    async def _run(self, task):
        if self.response_contract is None or self.current_task!=task:self.prepare_task(task)
        self.check_stop()
        handled,answer=await execute_controller_intent(self)
        if handled:return answer
        if self.workspace_operation=='listing':return await self.list_workspace_direct(task)
        await self.ensure_workspace_discovery()
        from .intents import information_need
        if information_need(task)=='VERIFY_EXISTING_PROJECT':return await self.project_check()
        await self.prepare_verification()
        direct_path = direct_read_path(task)
        if direct_path is not None:
            return await self.read_file_direct(task, direct_path)
        if self.resources.wants_memory_overview(task):
            from .i18n import localize
            if not self.memory or not self.settings.memory.enabled:
                return self.finish(task, localize("MEMORY_OVERVIEW_DISABLED",self.settings.ui.language))
            self.emit("AGENT_STEP", {"status": "Lese gespeicherte Projektinformationen"})
            records, total = self.memory.overview()
            self.emit("MEMORY_RETRIEVE", {"mode": "overview", "count": len(records), "total": total,
                                          "keys": [m["logical_key"] for m in records]})
            self.check_stop()
            if not records:
                return self.finish(task, localize("MEMORY_OVERVIEW_EMPTY",self.settings.ui.language))
            lines = [localize("MEMORY_OVERVIEW_HEADER",self.settings.ui.language)]
            shortened = False
            for record in records:
                content = record["content"]
                if len(content) > 1200:
                    content = content[:1200] + " … [gekürzt]"
                    shortened = True
                lines.append(f"• {record['logical_key']}: {content}")
            if total > len(records):
                lines.append(f"Gezeigt: {len(records)} von {total} aktiven Einträgen. Alle Einträge findest du im Reiter Memory.")
            elif shortened:
                lines.append(localize("MEMORY_OVERVIEW_FULL",self.settings.ui.language))
            return self.finish(task, "\n\n".join(lines))
        remember = re.match(r"\s*merke\s+dir\s*(?:\[([^\]]+)\])?\s*:\s*(.+)", task, re.I | re.S)
        if remember:
            if not self.memory or not self.settings.memory.enabled:
                from .i18n import localize
                return localize("MEMORY_DISABLED",self.settings.ui.language)
            content = remember.group(2).strip()
            if len(content) > 8000:
                raise ValueError("Memory-Eintrag ist zu lang (maximal 8000 Zeichen).")
            key = remember.group(1) or ("godot-version" if "godot" in content.casefold() else " ".join(re.findall(r"[^\W\d_]+", content.casefold())[:5]))
            record = self.memory.put(key, "USER_DECISION", content, "Explizite Benutzerangabe")
            if record.get("changed", True):
                self.emit("MEMORY_STORE", {"key": key, "id": record["id"]})
                if record.get("superseded_ids"):
                    self.emit("MEMORY_SUPERSEDE", {"ids": record["superseded_ids"], "key": key})
            from .i18n import localize
            answer = localize("MEMORY_STORED",self.settings.ui.language,key=key,content=content)
            self.session.history.extend([{"role": "user", "content": task}, {"role": "assistant", "content": answer}])
            return answer
        memories = []
        if self.memory and self.settings.memory.enabled and self.resources.needs_memory(task) and not (self.intent_resolution.handled and self.intent_resolution.intent_id==IntentID.EXPLAIN_CODE and not self.intent_resolution.referenced_paths):
            self.emit("AGENT_STEP", {"status": "Suche Memory"})
            records = self.memory.retrieve(task, self.settings.memory.retrieval_limit)
            memories = [{"key": m["logical_key"], "type": m["type"], "content": m["content"]} for m in records]
            self.emit("MEMORY_RETRIEVE", {"count": len(memories), "keys": [m["key"] for m in memories]})
        rounds, seen, consecutive_errors = [], Counter(), 0
        schemas = [] if self.response_contract.mode==ResponseMode.FILE_CONTENT else self.task_schemas(task) if self.resources.needs_tools(task) else []
        context = ContextManager(self.settings.agent.context_budget)
        explicit_inputs=[{'role':'system','content':controller_facts(self)}]+await provide_explicit_inputs(self)
        if self.response_contract.mode==ResponseMode.FILE_CONTENT:explicit_inputs.append({'role':'system','content':'FILE_CONTENT: Antworte nur als JSON mit genau einem Feld content und dem vollständigen Dateiinhalt als String. Keine weiteren Felder, Pfade, Markdown-Zäune oder Erklärungen.'})
        if self.intent_resolution.handled and self.intent_resolution.intent_id==IntentID.EXPLAIN_CODE and not self.intent_resolution.referenced_paths:
            explicit_inputs.append({"role":"system","content":"KNOWLEDGE_TASK: Answer the educational question directly with an explanation/example. No workspace task or file access is required. Do not replace the answer with a description of your capabilities."})
        for self.step in range(1, self.settings.agent.max_steps + 1):
            self.check_stop()
            self.emit("AGENT_STEP", {"status": "Frage Modell", "step": self.step})
            try:
                required_information=([self.workspace_discovery.context_message()] if self.workspace_discovery else [])+explicit_inputs
                if not self.write_scope.read_only:
                    required_information.append({'role':'system','content':'WRITE_SCOPE\n'+json.dumps(asdict(self.write_scope),ensure_ascii=False)})
                messages = context.build(task, self.session.history, memories, rounds,
                    required_information=required_information)
            except ValueError as exc:
                self.last_failure_type = FailureType.RESOURCE_CONTEXT_LIMIT
                self.emit('FAILURE_CLASSIFIED',{'failure_type':'RESOURCE_CONTEXT_LIMIT','status':str(exc)})
                raise
            response = await self.call_model(task,messages,schemas,
                lambda: self.backend.chat(messages,tools=schemas,model=self.resources.select_model(task)))
            calls = response.get("tool_calls") or []
            if not calls:
                answer = response.get("content") or "Das Modell hat keine Textantwort geliefert. Bitte ein anderes Modell versuchen."
                if getattr(response,'proposed_files',[]):
                    for proposal in response.proposed_files:
                        self.tools.validate('write_file',proposal)
                        if not self.write_scope.permits(proposal['path'],self.workspace):raise ToolDenied('Vorschlag außerhalb des Schreibbereichs.')
                    for proposal in response.proposed_files:
                        await self.execute_tool('write_file',proposal,origin='controller')
                        digest=hashlib.sha256(proposal['content'].encode('utf-8')).hexdigest()
                        actual=hashlib.sha256(self.workspace.path(proposal['path']).read_bytes()).hexdigest()
                        if digest!=actual:raise CompletionRejected(FailureType.INFORMATION_MISSING,'Geschriebene Bytes stimmen nicht mit dem Vorschlag überein.')
                        self.emit('PROPOSAL_DISPLAY_HASH',{'path':proposal['path'],'canonical_parsed_content_sha256':digest,'written_bytes_sha256':actual,'displayed_accepted_content_sha256':digest,'encoding':'UTF-8','newline_normalization':'none'})
                        answer+='\n\nAkzeptierter Dateiinhalt '+proposal['path']+':\n'+proposal['content']
                return self.finish(task,answer)
            if not schemas:
                raise ValueError("Für diese Chatnachricht wurden keine Werkzeuge angefordert oder freigegeben.")
            if len(calls) > 8:
                self.last_failure_type=FailureType.TOOL_FAILURE
                self.emit('FAILURE_CLASSIFIED',{'failure_type':'TOOL_FAILURE','subreason':'TOOL_CALL_LIMIT','requested_calls':len(calls),'executed_calls':0,'status':'Zu viele Werkzeugaufrufe in einem Modellschritt (maximal 8).'})
                raise CompletionRejected(FailureType.TOOL_FAILURE,'Zu viele Werkzeugaufrufe in einem Modellschritt (maximal 8).')
            group = [{"role": "assistant", "content": response.get("content"), "tool_calls": calls}]
            step_seen=set();failure_state='NONE'
            for call_index,call in enumerate(calls):
                if self.diagnostic_trace:self.diagnostic_trace.call_index=call_index
                self.check_stop()
                name, arguments = "?", {}
                try:
                    name = call["function"]["name"]
                    raw = call["function"]["arguments"]
                    arguments = json.loads(raw) if isinstance(raw, str) else raw
                    stamp = json.dumps([name, arguments], sort_keys=True)
                    progress_key=(stamp,failure_state)
                    if progress_key in step_seen:
                        self.last_failure_type=FailureType.TOOL_FAILURE
                        self.emit('FAILURE_CLASSIFIED',{'failure_type':'TOOL_FAILURE','subreason':'NO_PROGRESS','tool':name,'request_origin':'model','preceding_failure':failure_state,'status':'Identischer Tool-Aufruf ohne Zustandsfortschritt gestoppt.'})
                        raise CompletionRejected(FailureType.TOOL_FAILURE,'NO_PROGRESS: identischer Tool-Aufruf im selben Modellschritt.')
                    step_seen.add(progress_key)
                    seen[stamp] += 1
                    if seen[stamp] >= 3:
                        raise LoopStopped("Wiederholte identische Werkzeugaufrufe erkannt. Bitte Aufgabe präzisieren.")
                    result = await self.execute_tool(name, arguments)
                    consecutive_errors = consecutive_errors + 1 if "error" in result else 0
                except ToolDenied as exc:
                    return self.finish(task, str(exc))
                except (LoopStopped, CompletionRejected, asyncio.CancelledError):
                    raise
                except Exception as exc:
                    result = {"error": str(exc)[:2000]}
                    consecutive_errors += 1
                    self.emit("ERROR", {"tool": name, "message": str(exc)[:2000]})
                    self.last_failure_type = FailureType.INFORMATION_MISSING if isinstance(exc,FileNotFoundError) else FailureType.TOOL_FAILURE
                    self.dependency_failure=self.last_failure_type
                    if isinstance(exc,FileNotFoundError):
                        self.information_errors.add(arguments.get('path',arguments.get('directory','unknown')))
                        self.information_complete=False
                    self.emit('FAILURE_CLASSIFIED',{'failure_type':str(self.last_failure_type),'status':str(exc)[:300]})
                self.emit("TOOL_RESULT", {"name": name, "result": result})
                group.append({"role": "tool", "tool_call_id": call.get("id", "invalid"), "content": json.dumps(result, ensure_ascii=False)})
                failure_state=json.dumps(result.get('error'),sort_keys=True) if 'error' in result else 'NONE'
                if (name=='write_file' and 'written_bytes' in result and self.intent_resolution.intent_id==IntentID.CREATE_FILE
                    and set(self.intent_resolution.output_paths or self.intent_resolution.referenced_paths)<=set(self.written_paths)
                    and self.information_complete and self.dependency_failure is None
                    and not self.response_contract.expects_json and self.validator is None and validator_for_task(task) is None):
                    self.emit('CREATE_TOOL_COMPLETED',{'paths':list(self.written_paths),'remaining_batch_calls_skipped':len(calls)-len(group)+1,'status':'Angeforderte Datei geschrieben; keine wiederholte Modellausführung.'})
                    return self.finish(task,'Datei erstellt: '+', '.join(self.written_paths))
                if consecutive_errors >= 3:
                    raise LoopStopped("Drei Werkzeugfehler oder Ablehnungen hintereinander. Agent wurde gestoppt.")
            rounds.append(group)
        raise LoopStopped(f"Schrittlimit erreicht ({self.settings.agent.max_steps}). Agent wurde gestoppt.")


class LoopStopped(RuntimeError):
    pass


class ToolDenied(RuntimeError):
    pass
