"""Small deterministic router and a shared pool of existing backend connections."""
from dataclasses import dataclass
from enum import StrEnum
from types import SimpleNamespace
import asyncio
import re
from .backends import create_backend
from .profiles import ModelProfile


class TaskType(StrEnum):
    CODE_TASK = "CODE_TASK"
    GENERAL_TASK = "GENERAL_TASK"
    REASONING_TASK = "REASONING_TASK"
    MEMORY_TASK = "MEMORY_TASK"
    FILE_TASK = "FILE_TASK"


CODE_EXTENSIONS = r"py|gd|js|jsx|ts|tsx|cs|cpp|c|h|java|rs|go|lua|html|css|sql|sh|ps1"
FILE_EXTENSIONS = CODE_EXTENSIONS + r"|godot|tscn|tres|txt|md|json|toml|yaml|yml|csv|log|ini|cfg"


def referenced_paths(task):
    quoted = re.findall(r'["`\u201e]([^"`\u201c\n]+)["`\u201c]', task)
    paths = [p for p in quoted if re.search(r"\.(?:" + FILE_EXTENSIONS + r")$", p, re.I)]
    # Quoted strings with spaces are kept as a whole, not split into fake paths.
    unquoted = re.sub(r'["`\u201e][^"`\u201c\n]+["`\u201c]', ' ', task)
    paths += re.findall(r"(?<![\w])(?:[\w./\\:-]+\.(?:" + FILE_EXTENSIONS + r"))(?!\w)", unquoted, re.I)
    return list(dict.fromkeys(paths))[:4]


def classify_task(task):
    from .discovery import workspace_intent
    if workspace_intent(task):
        from .intents import information_need
        if information_need(task) in {'DEBUG_EXISTING_CODE','REFACTOR_EXISTING_CODE','MODIFY_EXISTING_CODE'}:
            return TaskType.CODE_TASK, "Bestehende Implementierung benötigt Source-/Testinformationen"
        return TaskType.FILE_TASK, "Workspace-/Projektordner ohne konkreten Dateipfad"
    lower = task.casefold()
    if lower.strip().startswith("merke dir") or re.search(r"\b(memory|gemerkt|erinnerungen|memorized)\b", lower):
        return TaskType.MEMORY_TASK, "Explizite Memory-Anforderung"
    if re.search(r"\.(?:" + CODE_EXTENSIONS + r")\b", lower) or re.search(r"\b(code|coding|debug|debugging|bug|funktion|programmiere|programmieren|traceback|refactor|python|godot)\b", lower):
        return TaskType.CODE_TASK, "Code-Datei oder ausdrückliche Programmieraufgabe"
    if re.search(r"\b(plan|plane|planung|strategie|reasoning|durchdenken|abwägen|beweise|beweis|architektur)\b", lower):
        return TaskType.REASONING_TASK, "Explizite Planung oder vertiefte Begründung"
    if referenced_paths(task) or re.search(r"\b(datei|dateien|ordner|workspace|verzeichnis)\b", lower):
        return TaskType.FILE_TASK, "Datei- oder Workspace-Anforderung"
    return TaskType.GENERAL_TASK, "Allgemeine Aufgabe ohne Spezialanforderung"


def required_capabilities(task_type, task=""):
    if task_type == TaskType.CODE_TASK:
        return {"coding"}
    if task_type == TaskType.REASONING_TASK:
        return {"planning"} if re.search(r"\b(plan|plane|planung)\b", task.casefold()) else {"deep_reasoning"}
    return {"general"}


def eligible(profile, task, required=None, protocol=None, task_capability=None):
    """Strict V2 intersection. Legacy broad fields remain broad, never subtype evidence."""
    v=profile.v2()
    if protocol=='native_tool_calling' and profile.tool_mode!='native':return False
    if protocol=='json_fallback' and profile.tool_mode!='json_fallback':return False
    if task_capability:return v.supports(task_capability,protocol)
    task_type,_=classify_task(task)
    required=required or required_capabilities(task_type,task)
    mapping={'general':'general_chat','coding':'coding','planning':'reasoning','deep_reasoning':'reasoning'}
    from .contracts import contract_for_task,WriteScope
    mode=str(contract_for_task(task).mode)
    requested={mapping.get(c,c) for c in required}
    if task.startswith('EXPLAIN_CODE'):requested={'coding'}
    if profile.profile_v2:
        from .taxonomy import task_category
        from .intent_resolution import DeterministicIntentResolver,IntentID
        category=str(task_category(task))
        narrow={'PYTHON_DEBUG':'code_debugging','CODE_REVIEW':'code_review','SUMMARIZATION':'summarization'}.get(category)
        if task.startswith('EXPLAIN_CODE') or DeterministicIntentResolver().resolve(task).intent_id==IntentID.EXPLAIN_CODE:requested={'explanation','coding'}
        elif classify_task(task)[0]==TaskType.CODE_TASK and (task.startswith(('CREATE_FILE ','MODIFY_FILE ','CREATE_PROJECT ')) or not WriteScope.for_task(task).read_only):requested={'code_generation'}
        elif narrow:requested={narrow}
    if mode=='TOOL_ACTION' and (profile.profile_v2 or v.task_capabilities['tool_selection']=='SUPPORTED'):requested={'tool_selection'}
    if not all(v.task_capabilities.get(c)=='SUPPORTED' for c in requested):return False
    if protocol:return v.supports(protocol=protocol)
    wire={'PLAIN_TEXT':'plain_text','ANALYSIS_RESULT':'analysis_result','STRUCTURED_JSON':'structured_json','FILE_PROPOSAL':'file_proposal','TOOL_ACTION':'tool_action'}[mode]
    if mode=='FILE_PROPOSAL' and profile.tool_mode!='none':wire='native_tool_calling' if profile.tool_mode=='native' else 'json_fallback'
    # Old cards only declared the transport and broad task. The current controller
    # may adapt legacy structured contracts; this is not recorded as V2 support.
    if not profile.profile_v2 and wire in {'structured_json','analysis_result','file_proposal'}:wire='plain_text' if profile.tool_mode=='none' else 'native_tool_calling' if profile.tool_mode=='native' else 'json_fallback'
    return v.supports(protocol=wire)


class ModelRegistry:
    def __init__(self, profiles):
        self.profiles = {}
        self.availability = {}
        self.errors = {}
        self.identities = {}
        for profile in profiles:
            self.add(profile)

    def add(self, profile):
        profile.validate()
        if profile.id in self.profiles:
            raise ValueError(f"Doppelte Modell-ID: {profile.id}")
        self.profiles[profile.id] = profile

    @classmethod
    def from_settings(cls, settings):
        if settings.models:
            return cls(settings.models)
        legacy = settings.model
        return cls([ModelProfile("legacy", "Bisheriges Modell", legacy.provider, legacy.model or "mock-demo",
                                 legacy.base_url, tool_mode="json_fallback" if legacy.provider == "ollama" else "native")])

    def enabled(self):
        return [p for p in self.profiles.values() if p.enabled and self.availability.get(p.id) is not False]

    async def check_availability(self, timeout=5):
        async def probe(connection_profiles):
            first = connection_profiles[0]
            try:
                backend = create_backend(SimpleNamespace(provider=first.provider, base_url=first.base_url, timeout=timeout, secret_reference=first.secret_reference))
                names = await backend.list_models()
                for profile in connection_profiles:
                    present = profile.provider == "mock" or profile.model_name in names
                    self.availability[profile.id] = present
                    self.errors[profile.id] = "" if present else "Modell wird vom Server nicht angeboten"
                    if present:
                        self.identities[profile.id] = await backend.model_identity(profile.model_name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                for profile in connection_profiles:
                    self.availability[profile.id] = False
                    self.errors[profile.id] = str(exc)
        connections = {}
        for profile in self.profiles.values():
            if profile.enabled:
                connections.setdefault((profile.provider, profile.base_url, profile.secret_reference), []).append(profile)
        await asyncio.gather(*(probe(group) for group in connections.values()))
        return [p.id for p in self.enabled()]


class BackendPool:
    def __init__(self, timeout=120, factory=create_backend):
        self.timeout, self.factory, self.connections = timeout, factory, {}

    def get(self, profile):
        key = (profile.provider, profile.base_url, profile.secret_reference)
        if key not in self.connections:
            self.connections[key] = self.factory(SimpleNamespace(provider=profile.provider, base_url=profile.base_url, timeout=self.timeout, secret_reference=profile.secret_reference))
        return self.connections[key]


class RegistryProbe:
    def __init__(self, registry):
        self.registry = registry

    async def health_check(self):
        return await self.registry.check_availability()

    def get_capabilities(self):
        return {"tool_calling": None, "structured_output": None, "context_length": None,
                "registry": True, "availability": dict(self.registry.availability), "errors": dict(self.registry.errors)}


@dataclass
class Route:
    profile: ModelProfile
    reason: str


RESOURCE_ORDER = {"low": 0, "medium": 1, "high": 2}
SPEED_ORDER = {"fast": 0, "normal": 1, "slow": 2}


class ModelRouter:
    def __init__(self, registry, settings, experience=None, fingerprints=None, observe=None, source_policy=None):
        self.registry, self.settings = registry, settings
        self.experience, self.fingerprints = experience, fingerprints or {}
        self.observe = observe or (lambda *args: None)
        self.source_policy = source_policy

    def ordered(self, profiles):
        return sorted(profiles, key=lambda p: (RESOURCE_ORDER[p.resource_class], -p.priority,
                      SPEED_ORDER[p.speed_class], p.id != self.settings.default_model, p.id))

    def select_model(self, task, required=None, state=None, *, task_capability=None, protocol=None):
        state = state or {}
        available = [p for p in self.registry.enabled() if p.id not in state.get("excluded", set())]
        manual = state.get("manual_model")
        if manual:
            profile = self.registry.profiles.get(manual)
            if not profile or profile not in available:
                raise ValueError("Das manuell gewählte Modell ist deaktiviert oder nicht verfügbar.")
            if (profile.profile_v2 or protocol or task_capability) and not eligible(profile,task,required,protocol,task_capability):
                raise ValueError("Manuelles Modell erfüllt Aufgaben-/Protokollanforderung nicht.")
            return Route(profile, "Manuelle Auswahl für diese Aufgabe")
        if not available:
            raise ValueError("Kein aktives Modell verfügbar. Bitte Modelle und Server prüfen.")
        task_type, _ = classify_task(task)
        required = required or required_capabilities(task_type, task)
        suitable = [p for p in available if eligible(p,task,required,protocol,task_capability)]
        if suitable:
            if state.get('response_contract')=='FILE_CONTENT':
                route=self.from_file_content_evidence(suitable)
                if route:return route
            observed = self.from_experience(task, suitable,state.get("category_override"))
            if observed:
                return observed
            return Route(self.ordered(suitable)[0], "Passende Capability: " + ", ".join(sorted(required)) + "; kleinste geeignete Ressourcenklasse")
        general = [p for p in available if p.v2().supports("general_chat","plain_text") and not protocol and not task_capability and not p.profile_v2]
        if not general:
            raise ValueError("Kein Modell mit bestätigter deklarierter Aufgaben- und Protokollfähigkeit verfügbar. Profil V2 prüfen.")
        fallback = next((p for p in general if p.id == self.settings.fallback_model), None)
        if fallback:
            return Route(fallback, "Spezialist nicht verfügbar; konfiguriertes General-Fallback")
        return Route(self.ordered(general)[0], "Spezialist fehlt; kleinstes verfügbares Fallback")

    def from_file_content_evidence(self,candidates):
        """Only exact, current local live FILE_CONTENT results; never a global score."""
        if not self.experience or not self.settings.use_experience:return None
        include_local=self.source_policy is None or self.source_policy.local_live_enabled
        if not include_local:return None
        suitable=[];minimum=self.settings.min_samples_for_experience
        quality=self.settings.quality.get('default_min_success_rate',0.8)
        for profile in candidates:
            if profile.tool_mode!='none':continue
            fingerprint=self.fingerprints.get(profile.id,'')
            with self.experience.connection() as db:
                rows=db.execute("""SELECT protocol_status FROM model_experience
                    WHERE model_fingerprint_id=? AND response_contract='FILE_CONTENT' AND source='live'
                    AND is_current=1 AND information_complete=1
                    AND protocol_status IN ('VERIFIED_SUCCESS','VERIFIED_FAILURE')
                    AND EXISTS(SELECT 1 FROM model_fingerprints WHERE id=? AND stale=0)""",(fingerprint,fingerprint)).fetchall()
            if len(rows)>=minimum and sum(row['protocol_status']=='VERIFIED_SUCCESS' for row in rows)/len(rows)>=quality:
                suitable.append(profile)
        if not suitable:return None
        chosen=self.ordered(suitable)[0]
        self.observe('MODEL_SELECTED_FROM_EXPERIENCE',{'model_id':chosen.id,'protocol':'FILE_CONTENT','reason':'verified local FILE_CONTENT evidence'})
        return Route(chosen,'Lokale verifizierte FILE_CONTENT-Protokollevidenz; keine allgemeine Coding-Bewertung')

    def from_experience(self, task, candidates, category_override=None):
        from .taxonomy import task_category
        from .contracts import contract_for_task,ResponseMode
        if not self.experience or not self.settings.use_experience:
            return None
        category = str(category_override or task_category(task))
        quality = self.settings.quality.get(category, self.settings.quality.get('default_min_success_rate', 0.8))
        known, good, poor = {}, [], []
        for p in candidates:
            contract=contract_for_task(task).mode
            if contract==ResponseMode.FILE_PROPOSAL and p.tool_mode!='none':contract=ResponseMode.TOOL_ACTION
            row = self.experience.summary(self.fingerprints.get(p.id, ''), category,contract) if self.source_policy is None or self.source_policy.local_live_enabled else self.experience.summary(self.fingerprints.get(p.id, ''), category,contract,include_local=False)
            if row and row['hard_sample_count'] >= self.settings.min_samples_for_experience:
                known[p.id] = row
                (good if row['success_rate'] >= quality else poor).append(p)
        if not good:
            return None
        def key(p):
            latency = known[p.id]['median_latency']
            return RESOURCE_ORDER[p.resource_class], latency if latency is not None else float('inf'), p.id
        selected = min(good, key=key)
        if not self.settings.allow_skip_from_experience:
            declared = self.ordered(candidates)[0]
            if declared in poor:
                return None
        else:
            for p in poor:
                self.observe('MODEL_SKIPPED_FROM_EXPERIENCE', {'model_id': p.id, 'category': category,
                    'samples': known[p.id]['hard_sample_count'], 'success_rate': known[p.id]['success_rate'],
                    'required_quality': quality, 'reason': 'Gemessene Qualität unter Mindestqualität; geeignete Alternative bekannt'})
        reason = f"Erfahrung {category}: {known[selected.id]['hard_sample_count']} geprüfte Versuche, {known[selected.id]['success_rate']:.0%} Erfolg ≥ {quality:.0%}; kleinste ausreichende Ressourcenklasse, dann gemessene Latenz"
        self.observe('MODEL_SELECTED_FROM_EXPERIENCE', {'model_id': selected.id, 'category': category,
            'required_quality': quality, 'reason': reason,
            'candidates': {p.id: {'samples': known[p.id]['hard_sample_count'], 'success_rate': known[p.id]['success_rate'],
                                'median_latency_ms': known[p.id]['median_latency']} for p in candidates if p.id in known}})
        return Route(selected, reason)

    def escalate(self, current, visited, task=None):
        candidates = [p for p in self.registry.enabled() if p.id not in visited and
                      RESOURCE_ORDER[p.resource_class] > RESOURCE_ORDER[current.resource_class] and (task is None or eligible(p,task))]
        reasoners = [p for p in candidates if p.role == "reasoning" or "deep_reasoning" in p.capabilities]
        return self.ordered(reasoners or candidates)[0] if candidates else None
