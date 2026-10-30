"""Multi-model extension of Agent. Native tools keep the v0.1 execution pipeline."""
from copy import deepcopy
from dataclasses import dataclass, asdict, replace
import asyncio
import inspect
import json
import os
import re
import time
import httpx
from jsonschema import validate, ValidationError
from .agent import Agent, Session, LoopStopped, ToolDenied
from .backends import ModelBackend
from .context import size
from .identity import identity_system,controller_facts
from .routing import ModelRegistry, ModelRouter, BackendPool, classify_task, required_capabilities, referenced_paths, TaskType
from .task_packet import TaskPacket
from .file_actions import direct_read_path
from .intent_resolution import IntentID
from .task_execution import execute_controller_intent
from .experience import ModelFingerprint, SuccessSignal
from .taxonomy import task_category
from .completion import CompletionRejected, FailureType
from .contracts import contract_for_task, ResponseMode


@dataclass
class ModelUsage:
    model_calls: int = 0
    input_tokens_estimate: int = 0
    output_tokens_estimate: int = 0
    elapsed_time: float = 0.0
    successful_tasks: int = 0
    failed_tasks: int = 0
    tool_calls_requested: int = 0
    delegated_tool_calls: int = 0
    invalid_tool_calls: int = 0
    invalid_structured_outputs: int = 0
    escalations: int = 0
    context_bytes: int = 0
    cancelled_tasks: int = 0
    actual_input_tokens: int = 0
    actual_output_tokens: int = 0
    measured_usage_calls: int = 0
    resource_failures: int = 0
    resource_retries: int = 0
    resource_retry_successes: int = 0


class ModelOutputError(ValueError):
    pass


class VerifiedModelFailure(RuntimeError):
    pass


class ObjectiveFailure(VerifiedModelFailure):
    """An external validator failed, rather than the model declaring its own failure."""


class ModeBackend(ModelBackend):
    def __init__(self, backend, profile):
        self.backend, self.profile = backend, profile

    async def list_models(self):
        return await self.backend.list_models()

    async def chat(self, messages, tools=None, model=""):
        if tools == [] and self.profile.tool_mode!='json_fallback':
            return await self.backend.chat(messages, tools=[], model=self.profile.model_name)
        return await self.backend.chat_with_mode(messages, tools, self.profile.model_name, self.profile.tool_mode)

    def cancel(self):
        self.backend.cancel()

    def output_budget(self,limit):
        return self.backend.output_budget(limit)

    @property
    def last_completion(self):
        return getattr(self.backend,'last_completion',None)


PROPOSAL_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string"},
    "status": {"enum": ["ok", "cannot_complete", "verification_failed"]},
    "reason": {"type": "string"},
    "proposed_files": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
        "path": {"type": "string", "minLength": 1, "maxLength": 1024},
        "content": {"type": "string", "maxLength": 262144}}, "required": ["path", "content"], "additionalProperties": False}}},
    "required": ["answer"], "additionalProperties": False}


class RoutedAgent(Agent):
    def __init__(self, workspace, settings, session=None, memory=None, permission=None, observe=None,
                 stopped=None, registry=None, pool=None, manual_model=None, verify=None,
                 experience=None, session_id=None, validator=None, intent_resolver=None, semantic_resolver=None):
        self.registry = registry or ModelRegistry.from_settings(settings)
        self.pool = pool or BackendPool(settings.model.timeout)
        self.experience, self.session_id = experience, session_id
        self.fingerprints = {}
        self.experience_ids = []
        self.router = ModelRouter(self.registry, settings.routing, experience, self.fingerprints, self.emit, settings.experience_sources)
        self.manual_model = manual_model if manual_model is not None else (settings.routing.manual_model if settings.routing.mode == "manual" else "")
        self.verify = verify
        self.current_profile = None
        self.packets = []
        self.last_input = self.last_output = 0
        self.call_started = None
        self.pending_context = self.pending_input = None
        self.task_denied = False
        self.native_tool_successes = 0
        self.known_context_limits = {}
        super().__init__(None, workspace, settings, session, memory, permission, observe, stopped, validator, intent_resolver, semantic_resolver)

    def close_call(self):
        if self.call_started is not None and self.current_profile:
            usage = self.resources.per_model[self.current_profile.id]
            raw_usage = getattr(self.pool.get(self.current_profile), 'last_usage', {})
            if all(k in raw_usage for k in ('prompt_tokens', 'completion_tokens')):
                usage.actual_input_tokens += raw_usage['prompt_tokens']
                usage.actual_output_tokens += raw_usage['completion_tokens']
                usage.measured_usage_calls += 1
            actual = getattr(self.pool.get(self.current_profile), "last_context_bytes", None)
            if actual is not None and self.pending_context is not None:
                usage.context_bytes += actual - self.pending_context
                estimate = (actual + 2) // 3
                delta = estimate - self.pending_input
                usage.input_tokens_estimate += delta
                self.resources.counters.input_tokens_estimate += delta
                self.resources.counters.context_size = actual
                self.last_input = self.resources.counters.input_tokens_estimate
            usage.elapsed_time = round(usage.elapsed_time + time.monotonic() - self.call_started, 4)
            self.call_started = None
            self.pending_context = self.pending_input = None

    def emit(self, kind, details=None):
        details = dict(details or {})
        if self.current_profile:
            key = self.current_profile.id
            usage = self.resources.per_model.setdefault(key, ModelUsage())
            details.setdefault("model_id", key)
            if kind == "MODEL_CALL":
                usage.model_calls += 1
                self.pending_input = self.resources.counters.input_tokens_estimate - self.last_input
                usage.input_tokens_estimate += self.pending_input
                self.last_input = self.resources.counters.input_tokens_estimate
                usage.context_bytes += details.get("context_bytes", 0)
                self.pending_context = details.get("context_bytes", 0)
                self.pool.get(self.current_profile).last_context_bytes = None
                self.pool.get(self.current_profile).last_usage = {}
                self.call_started = time.monotonic()
            elif kind == "MODEL_RESPONSE":
                usage.output_tokens_estimate += self.resources.counters.output_tokens_estimate - self.last_output
                self.last_output = self.resources.counters.output_tokens_estimate
                usage.tool_calls_requested += details.get("tool_count", 0)
                self.close_call()
            elif kind == "TOOL_REQUEST":
                if details.get("origin", "model") == "controller":
                    usage.delegated_tool_calls += 1
            elif kind == "ERROR" and details.get("tool"):
                usage.invalid_tool_calls += 1
            elif kind == "TOOL_DENIED":
                self.task_denied = True
                self.last_failure_type = FailureType.PERMISSION_FAILURE
            elif kind == 'RESOURCE_RETRY_STARTED':
                usage.resource_retries += 1
            elif kind == 'RESOURCE_OUTPUT_LIMIT':
                usage.resource_failures += 1
            elif kind == 'RESOURCE_RETRY_SUCCESS':
                usage.resource_retry_successes += 1
        super().emit(kind, details)

    async def _run(self, task):
        self.prepare_task(task)
        task_type, classification_reason = classify_task(self.scope_task)
        self.emit("TASK_CLASSIFIED", {"task_type": str(task_type), "reason": classification_reason,
                                    "status": f"Aufgabe als {task_type} erkannt"})
        self.category=task_category(self.scope_task,task_type)
        handled,answer=await execute_controller_intent(self)
        if handled:return answer
        if self.workspace_operation=='listing':return await self.list_workspace_direct(task)
        from .intents import information_need
        if information_need(task)=='VERIFY_EXISTING_PROJECT':return await self.project_check()
        direct_path = direct_read_path(task)
        if direct_path is not None:
            return await self.read_file_direct(task, direct_path)
        # Keep proven deterministic memory commands; they need no intelligence resource.
        if self.resources.wants_memory_overview(task) or re.match(r"\s*merke\s+dir\s*(?:\[[^\]]+\])?\s*:", task, re.I):
            return await super()._run(task)
        self.category = task_category(self.scope_task, task_type)
        from .task_slots import attribution
        self.category,self.capability_v2=attribution(self.intent_resolution,self.category)
        if self.experience:
            for p in self.registry.enabled():
                try:
                    metadata = await asyncio.wait_for(self.pool.get(p).model_identity(p.model_name), timeout=5)
                except (TimeoutError, httpx.HTTPError):
                    metadata = {}
                fp = ModelFingerprint.for_profile(p, metadata, self.settings.agent.context_budget)
                fp = replace(fp,max_output_tokens=self.settings.resources.output.default_limit)
                reported_context = metadata.get('context_length')
                if type(reported_context) is int and reported_context > 0:
                    self.known_context_limits[p.id] = reported_context
                if not self.resources.needs_tools(task) and self.canonical_contract.mode!=ResponseMode.FILE_CONTENT:
                    fp = replace(fp, max_output_tokens=128)
                self.fingerprints[p.id] = self.experience.register(p, fp)
            self.router.fingerprints = self.fingerprints
        route = self.router.select_model(self.scope_task, state={"manual_model": self.manual_model,"category_override":self.category,"response_contract":str(self.canonical_contract.mode)})
        profile, reason = route.profile, route.reason
        visited, escalations, format_errors = set(), 0, 0
        previous = ""
        original_session, original_settings, original_backend = self.session, self.settings, self.backend
        initial_writes = self.resources.counters.file_writes
        while True:
            self.check_stop()
            self.current_profile = profile
            self.resources.per_model.setdefault(profile.id, ModelUsage())
            usage = self.resources.per_model[profile.id]
            self.attempt_before = asdict(usage)
            self.objective_verified = False
            self.file_content_protocol_status=None
            self.native_tools_before = self.native_tool_successes
            if profile.id not in visited:
                visited.add(profile.id)
                self.emit("MODEL_ROUTED", {"from_model": previous, "to_model": profile.id, "reason": reason,
                    "status": f"Modell gewählt: {profile.display_name} · {reason}", "tool_mode": profile.tool_mode})
            remaining = original_settings.agent.max_steps - self.resources.counters.model_calls
            if remaining <= 0:
                raise LoopStopped("Gesamtes Modell-Schrittlimit erreicht. Kein weiterer Modellwechsel.")
            try:
                self.settings = deepcopy(original_settings)
                self.settings.model.model = profile.model_name
                self.settings.agent.max_steps = remaining
                self.resources.model = profile.model_name
                self.response_contract=self.canonical_contract
                if profile.tool_mode!='none' and self.response_contract.mode in {ResponseMode.FILE_PROPOSAL,ResponseMode.FILE_CONTENT}:
                    self.response_contract=replace(self.response_contract,mode=ResponseMode.TOOL_ACTION,expects_file_proposal=False,expects_plain_text=True)
                self.session = Session()  # No blind old history passed to specialists.
                backend = self.pool.get(profile)
                self.known_context_limit = profile.context_length or self.known_context_limits.get(profile.id) or backend.get_capabilities().get('context_length')
                if profile.tool_mode == "none":
                    answer = await self.specialist(task, task_type, profile, backend, previous)
                else:
                    self.backend = ModeBackend(backend, profile)
                    answer = await super()._run(task)
                    if not self.task_denied:
                        self.explicit_failure(answer)
                        if self.verify and not await self.verifies(profile, answer, None):
                            raise ObjectiveFailure("Verifikationsfunktion meldet fehlgeschlagene Prüfung")
                        if self.verify:
                            self.objective_verified = True
                        if self.last_validation.valid and self.last_validation.verified and (self.last_validation.scope=='exact_value' or str(self.category)=='STRUCTURED_OUTPUT'):
                            self.objective_verified = True
                verification=await self.verify_written()
                signal=None
                if self.written_paths and verification:
                    signal=verification.task_signal if self.information_complete and not self.task_denied and not self.dependency_failure else 'UNKNOWN'
                    self.objective_verified=signal=='VERIFIED_SUCCESS'
                    if signal=='VERIFIED_FAILURE':self.last_failure_type=FailureType.VALIDATION_FAILURE
                    elif signal=='VERIFIED_SUCCESS':self.last_failure_type=FailureType.SUCCESS
                    else:self.last_failure_type=FailureType.UNKNOWN
                    if verification.verification_status=='FAIL':answer+='\n\nÄnderung wurde geschrieben, aber Verifikation ist fehlgeschlagen. '+verification.message
                    else:answer+='\n\nVerification: '+verification.verification_status+' · '+verification.message
                if self.task_denied or verification and verification.verification_status=='FAIL':
                    usage.failed_tasks += 1
                else:
                    usage.successful_tasks += 1
                self.session = original_session
                self.emit("MODEL_STATS", {"status": f"Aufgabe beendet: {profile.display_name}"})
                self.record_experience(SuccessSignal(signal) if signal else SuccessSignal.VERIFIED_SUCCESS if self.objective_verified and not self.task_denied else SuccessSignal.UNKNOWN)
                return self.finish(task, answer)
            except ToolDenied as exc:
                await self.verify_written()
                usage.failed_tasks += 1
                self.session = original_session
                self.emit("MODEL_STATS", {"reason": "Benutzerfreigabe abgelehnt; keine Eskalation"})
                self.record_experience(SuccessSignal.UNKNOWN)
                return self.finish(task, str(exc))
            except asyncio.CancelledError:
                if self.verification and not self.verification.finished:self.verification.abort()
                self.close_call()
                usage.cancelled_tasks += 1
                self.record_experience(SuccessSignal.UNKNOWN)
                raise
            except Exception as exc:
                await self.verify_written()
                self.close_call()
                if isinstance(exc,CompletionRejected):
                    self.last_failure_type = exc.failure_type
                elif isinstance(exc,ObjectiveFailure):
                    self.last_failure_type = FailureType.VALIDATION_FAILURE
                elif isinstance(exc,httpx.TimeoutException):
                    self.last_failure_type = FailureType.TIMEOUT
                elif isinstance(exc,ModelOutputError):
                    self.last_failure_type = FailureType.FORMAT_FAILURE
                elif isinstance(exc,ValidationError):
                    self.last_failure_type = FailureType.VALIDATION_FAILURE
                elif isinstance(exc,VerifiedModelFailure):
                    self.last_failure_type = FailureType.MODEL_CAPABILITY_FAILURE
                elif isinstance(exc,FileNotFoundError):
                    self.last_failure_type=FailureType.INFORMATION_MISSING
                    self.information_complete=False
                elif isinstance(exc,PermissionError):
                    self.last_failure_type=FailureType.PERMISSION_FAILURE
                # Server/model availability is a fallback, not a claim of higher intelligence.
                unavailable = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) or (
                    isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {404, 503})
                if unavailable and not self.manual_model:
                    usage.failed_tasks += 1
                    self.record_experience(SuccessSignal.UNKNOWN)
                    self.registry.availability[profile.id] = False
                    try:
                        replacement = self.router.select_model(task, state={"excluded": visited,"category_override":self.category})
                    except ValueError:
                        raise exc
                    previous, profile, reason = profile.id, replacement.profile, "Modellserver/Modell nicht verfügbar; " + replacement.reason
                    format_errors = 0
                    continue
                invalid_format = isinstance(exc, (ModelOutputError, ValidationError)) or (
                    isinstance(exc, ValueError) and any(w in str(exc).lower() for w in ("json", "modellantw", "modell hat", "werkzeugaufruf")))
                if invalid_format:
                    usage.invalid_structured_outputs += 1
                    format_errors += 1
                    if self.write_scope.read_only and format_errors < 2 and not self.resources.counters.resource_retries and self.resources.counters.model_calls < original_settings.agent.max_steps:
                        self.emit("ERROR", {"message": str(exc), "status": "Ungültige Modellstruktur; einmaliger erneuter Versuch"})
                        self.record_experience(SuccessSignal.VERIFIED_FAILURE, subcategory='invalid_output')
                        continue
                usage.failed_tasks += 1
                verifiable = invalid_format or isinstance(exc, (VerifiedModelFailure, LoopStopped))
                allowed = (verifiable and not (invalid_format and not self.write_scope.read_only) and not self.manual_model and original_settings.routing.allow_escalation and
                           not self.resources.counters.resource_retries and
                           escalations < original_settings.routing.max_escalations_per_task and
                           self.resources.counters.model_calls < original_settings.agent.max_steps and
                           self.resources.counters.file_writes == initial_writes)
                stronger = self.router.escalate(profile, visited, task) if allowed else None
                resource_failure = self.last_failure_type in {FailureType.RESOURCE_OUTPUT_LIMIT,FailureType.RESOURCE_CONTEXT_LIMIT}
                signal = SuccessSignal.RESOURCE_FAILURE if resource_failure else SuccessSignal.VERIFIED_FAILURE if invalid_format or isinstance(exc,ObjectiveFailure) or (isinstance(exc,CompletionRejected) and exc.failure_type in {FailureType.FORMAT_FAILURE,FailureType.VALIDATION_FAILURE,FailureType.MODEL_CAPABILITY_FAILURE}) else SuccessSignal.UNKNOWN
                self.record_experience(signal,
                                       escalated=bool(stronger), subcategory='invalid_output' if invalid_format else None)
                if not stronger:
                    self.emit("MODEL_STATS", {"reason": str(exc)})
                    raise
                escalations += 1
                usage.escalations += 1
                self.emit("MODEL_ESCALATED", {"from_model": profile.id, "to_model": stronger.id, "reason": str(exc),
                    "status": f"Wechsle von {profile.display_name} zu {stronger.display_name}: {exc}"})
                previous, profile, reason = profile.id, stronger, "Eskalation nach überprüfbarem Fehlschlag"
                format_errors = 0
            finally:
                self.session, self.settings, self.backend = original_session, original_settings, original_backend

    def record_experience(self, signal, escalated=False, subcategory=None):
        if not self.experience or not self.current_profile:
            return
        usage = asdict(self.resources.per_model[self.current_profile.id])
        if self.dependency_failure:
            self.last_failure_type=self.dependency_failure
        if not self.information_complete:
            if self.last_failure_type not in {FailureType.TOOL_FAILURE,FailureType.PERMISSION_FAILURE}:
                self.last_failure_type=FailureType.INFORMATION_MISSING
            signal=SuccessSignal.UNKNOWN
        if self.last_failure_type in {FailureType.INFORMATION_MISSING,FailureType.TOOL_FAILURE,FailureType.PERMISSION_FAILURE,FailureType.TIMEOUT}:
            signal=SuccessSignal.UNKNOWN
        delta = {key: usage[key] - self.attempt_before[key] for key in usage}
        if not delta['model_calls']:
            return
        exact = delta['measured_usage_calls'] == delta['model_calls']
        successes = self.native_tool_successes - self.native_tools_before
        if signal == SuccessSignal.UNKNOWN and str(self.category) == 'TOOL_USE' and successes and not self.task_denied and not delta['invalid_tool_calls'] and self.information_complete and not self.dependency_failure:
            signal, subcategory = SuccessSignal.VERIFIED_SUCCESS, 'native_tool_execution'
        identifier = self.experience.record(self.fingerprints[self.current_profile.id], self.category, signal,
            verification=self.verification.result.compact() if self.verification and self.written_paths else None,
            subcategory=subcategory, latency_ms=delta['elapsed_time'] * 1000,
            input_tokens=delta['actual_input_tokens'] if exact else delta['input_tokens_estimate'],
            output_tokens=delta['actual_output_tokens'] if exact else delta['output_tokens_estimate'], tokens_estimated=not exact,
            context_bytes=delta['context_bytes'], model_calls=delta['model_calls'],
            tool_calls=delta['tool_calls_requested'], invalid_tool_calls=delta['invalid_tool_calls'],
            escalated=escalated, session_id=self.session_id,
            failure_type=str(self.last_failure_type),finish_reason=self.last_completion.finish_reason,
            requested_output_limit=self.last_completion.requested_output_limit,
            actual_output_tokens=self.last_completion.output_tokens,
            resource_retry_used=bool(self.resources.counters.resource_retries),
            resource_retry_success=bool(self.resources.counters.resource_retries and self.last_failure_type==FailureType.SUCCESS),
            required_output_budget_observed=self.last_completion.requested_output_limit if signal==SuccessSignal.VERIFIED_SUCCESS and self.resources.counters.resource_retries else None,
            protocol_status=getattr(self,'file_content_protocol_status',None) if self.response_contract.mode==ResponseMode.FILE_CONTENT else None,
            task_domain=str(self.category),response_contract=str(self.response_contract.mode),
            information_complete=self.information_complete,protocol_normalized=self.protocol_normalized,attribution_version='v0.5',
            capability_v2=getattr(self,'capability_v2',None),protocol_v2={'PLAIN_TEXT':'plain_text','ANALYSIS_RESULT':'analysis_result','FILE_PROPOSAL':'file_proposal','STRUCTURED_JSON':'structured_json','FILE_CONTENT':'structured_json','TOOL_ACTION':'native_tool_calling' if self.current_profile.tool_mode=='native' else 'json_fallback'}.get(str(self.response_contract.mode)))
        self.experience_ids.append(identifier)
        self.emit('EXPERIENCE_RECORDED', {'id': identifier, 'fingerprint': self.fingerprints[self.current_profile.id],
                                        'category': str(self.category), 'signal': str(signal), 'source': 'live','capability_v2':getattr(self,'capability_v2',None),'protocol_v2':str(self.response_contract.mode),'language_v2':None})
        self.emit('EXPERIENCE_AGGREGATED', {'category': str(self.category)})
        if successes and str(self.category) != 'TOOL_USE':
            # Evidence of tool execution, never evidence that an entire code/analysis task is correct.
            tool_id = self.experience.record(self.fingerprints[self.current_profile.id], 'TOOL_USE', SuccessSignal.VERIFIED_SUCCESS,
                subcategory='native_tool_execution', model_calls=0, tool_calls=successes,
                session_id=self.session_id, validation_reason='TOOL_SCHEMA_PERMISSION_AND_EXECUTION_PASSED',
                task_domain='TOOL_USE',response_contract='TOOL_ACTION',information_complete=True,
                protocol_normalized=False,attribution_version='v0.5')
            self.emit('EXPERIENCE_RECORDED', {'id': tool_id, 'category': 'TOOL_USE', 'signal': 'VERIFIED_SUCCESS',
                                            'source': 'live', 'scope': 'tool_execution_only'})
            self.emit('EXPERIENCE_AGGREGATED', {'category': 'TOOL_USE'})

    def on_resource_failure(self):
        usage = self.resources.per_model[self.current_profile.id]
        self.record_experience(SuccessSignal.RESOURCE_FAILURE,subcategory='output_budget')
        self.attempt_before = asdict(usage)
        self.native_tools_before = self.native_tool_successes

    async def execute_tool(self, name, arguments, origin='model'):
        result = await super().execute_tool(name, arguments, origin)
        if origin == 'model':
            self.native_tool_successes += 1
        return result

    async def verifies(self, profile, answer, packet):
        result = self.verify(profile, answer, packet)
        return await result if inspect.isawaitable(result) else result

    def explicit_failure(self, text):
        try:
            result = json.loads(text)
        except (ValueError, TypeError):
            return
        if isinstance(result, dict) and isinstance(result.get("status"), str) and result["status"] in {"cannot_complete", "verification_failed"}:
            raise VerifiedModelFailure(result.get("reason") or result.get("answer") or "Modell meldet fehlende Fähigkeit/Kontext")

    async def delegate(self, name, arguments):
        self.emit("TOOL_DELEGATED", {"name": name, "path": arguments.get("path", ""),
            "status": f"Modell ohne Tools: Controller führt {name} aus · {arguments.get('path', '')}"})
        result = await self.execute_tool(name, arguments, origin="controller")
        self.emit("TOOL_RESULT", {"name": name, "result": result, "origin": "controller"})
        return result

    async def specialist(self, task, task_type, profile, backend, source_model):
        await self.ensure_workspace_discovery()
        await self.prepare_verification()
        memories = []
        if self.memory and self.settings.memory.enabled and not (self.intent_resolution.handled and self.intent_resolution.intent_id==IntentID.EXPLAIN_CODE and not self.intent_resolution.referenced_paths):
            records = self.memory.retrieve(task, self.settings.memory.retrieval_limit)
            memories = [{"key": m["logical_key"], "type": m["type"], "content": m["content"]} for m in records]
            self.emit("MEMORY_RETRIEVE", {"count": len(memories), "keys": [m["key"] for m in memories]})
        paths=(self.resolved_paths if self.resolved_finding_ids else list(self.intent_resolution.referenced_paths) if self.intent_resolution.handled else referenced_paths(task))
        files=deepcopy(self.workspace_discovery.files) if self.workspace_discovery else []
        results=[]
        create = bool(re.search(r"\b(erstell\w*|schreib\w*|create|write|new|neu\w*)\b", task, re.I))
        permitted = set()
        for relative in paths:
            self.check_stop()
            path = self.workspace.path(relative)
            permitted.add(os.path.normcase(path.relative_to(self.workspace.root).as_posix()))
            if not path.exists() and (self.intent_resolution.handled and self.intent_resolution.intent_id==IntentID.CREATE_FILE or not self.intent_resolution.handled and create):
                continue
            try:result = await self.delegate("read_file", {"path": relative})
            except FileNotFoundError as exc:
                self.information_complete=False
                self.last_failure_type=FailureType.INFORMATION_MISSING
                self.emit('FAILURE_CLASSIFIED',{'failure_type':'INFORMATION_MISSING','status':f'Benötigte Datei fehlt: {relative}'})
                raise CompletionRejected(FailureType.INFORMATION_MISSING,f'Benötigte Datei fehlt: {relative}') from exc
            files.append({"path": relative, "content": result["content"]})
            results.append({"name": "read_file", "path": relative, "bytes": len(result["content"].encode("utf-8"))})
        if not paths and task_type == TaskType.FILE_TASK and re.search(r"\b(liste|auflisten|list|zeige)\b", task, re.I):
            results.append(await self.delegate("list_files", {"path": "."}))
        budget = self.settings.agent.context_budget
        if profile.context_length:
            budget = min(budget, max(512, (profile.context_length - 1536) * 2))
        target_constraint = "Erlaubte Änderungsziele (keine zusätzlichen Pfade): " + ", ".join(self.write_scope.allowed_paths)
        packet = TaskPacket(task, str(task_type), sorted(required_capabilities(task_type, task)), memories, files,
            results, ["Dateiänderungen nur als Vorschlag, keine Ausführung durch das Modell",
                      target_constraint],
            {"context_bytes": budget, "max_model_steps": self.settings.agent.max_steps, "max_output_tokens": 1024}, source_model)
        packet.response_contract=asdict(self.response_contract)
        packet.task_domain=str(self.category)
        packet.write_scope=asdict(self.write_scope)
        packet.workspace_discovery=self.workspace_discovery.metadata() if self.workspace_discovery else {}
        packet.constraints.append(controller_facts(self))
        try:packet=packet.bounded(budget)
        except ValueError as exc:
            if self.workspace_discovery:
                self.information_complete=False
                raise CompletionRejected(FailureType.RESOURCE_CONTEXT_LIMIT,str(exc)) from exc
            raise
        self.packets.append(packet)
        messages = packet.messages()
        if self.intent_resolution.handled and self.intent_resolution.intent_id==IntentID.EXPLAIN_CODE and not paths:
            messages.insert(0,{"role":"system","content":"KNOWLEDGE_TASK: Answer the educational question directly with explanation and example; no workspace action is required. Do not substitute a capability statement."})
        self.emit("TASK_PACKET", {"paths": [f["path"] for f in packet.relevant_files_or_contents],
            "memory_keys": [m["key"] for m in packet.relevant_memory], "bytes": size(messages),
            "status": f"{', '.join(paths) or 'Aufgabe'} → {size(messages)} Bytes an {profile.display_name}"})
        if not self.resources.needs_tools(task) and self.response_contract.mode!=ResponseMode.FILE_CONTENT:
            short_messages = [{'role':'system','content':identity_system(controller_facts(self))}, {'role':'user','content':task}]
            response = await self.call_model(task,short_messages,[],
                lambda:backend.chat(short_messages,tools=[],model=profile.model_name),backend=backend)
        else:
            response = await self.call_model(task,messages,None,
                lambda:backend.chat_with_mode(messages,tools=None,model=profile.model_name,mode='none'),
                backend=backend,structured_protocol=True)
        self.check_stop()
        if isinstance(response, dict) and response.get("tool_calls"):
            calls = response["tool_calls"]
            count = len(calls) if isinstance(calls, list) else 1
            self.resources.per_model[profile.id].invalid_tool_calls += count
        if not isinstance(response, dict) or response.get("tool_calls") or not isinstance(response.get("content"), str):
            raise ModelOutputError("Tool-loses Modell muss eine Textantwort oder einen gültigen Änderungsvorschlag liefern.")
        text = response["content"]
        proposals = getattr(response,'proposed_files',[])
        if getattr(response,'envelope_status',None) in {'cannot_complete','verification_failed'}:
            raise VerifiedModelFailure(text)
        if self.verify and not await self.verifies(profile, text, packet):
            raise ObjectiveFailure("Verifikationsfunktion meldet fehlgeschlagene Prüfung")
        if self.verify:
            self.objective_verified = True
        if self.last_validation.valid and self.last_validation.verified and (self.last_validation.scope=='exact_value' or str(self.category)=='STRUCTURED_OUTPUT'):
            self.objective_verified = True
        # Validate every proposal before asking for the first mutation.
        for proposal in proposals:
            path = self.tools.validate("write_file", proposal)
            if not self.write_scope.read_only and not self.write_scope.permits(proposal['path'],self.workspace):
                raise CompletionRejected(FailureType.PERMISSION_FAILURE,"Änderungsvorschlag betrifft keinen erlaubten Zielpfad.")
        applied = []
        read_only = self.write_scope.read_only
        if proposals and read_only:
            return text + "\n\nVorschlag, nicht angewendet:\n" + "\n\n".join(p["path"] + ":\n" + p["content"] for p in proposals)
        if proposals and (__import__('re').search(r'```|(?:^|\n)\s*(?:#include|def\s+|class\s+|(?:int|void|char|float|double)\s+\w+\s*\(|<html|<!DOCTYPE|<script)',text,__import__('re').I)):
            text=''  # Never display a second independently rendered version of code.
        for proposal in proposals:
            await self.delegate("write_file", proposal)
            applied.append(proposal["path"])
            digest=__import__('hashlib').sha256(proposal['content'].encode('utf-8')).hexdigest()
            actual=__import__('hashlib').sha256(self.workspace.path(proposal['path']).read_bytes()).hexdigest()
            if digest!=actual:raise CompletionRejected(FailureType.INFORMATION_MISSING,'Geschriebene Bytes stimmen nicht mit dem Vorschlag überein.')
            self.emit('PROPOSAL_DISPLAY_HASH',{'path':proposal['path'],'canonical_parsed_content_sha256':digest,'written_bytes_sha256':actual,'displayed_accepted_content_sha256':digest,'encoding':'UTF-8','newline_normalization':'none'})
            text+='\n\nAkzeptierter Dateiinhalt '+proposal['path']+':\n'+proposal['content']
        if applied:
            text += "\n\nNach Freigabe geschrieben: " + ", ".join(applied)
        return text or "Das Modell hat keine inhaltliche Antwort geliefert."
