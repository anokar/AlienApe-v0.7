from dataclasses import asdict, dataclass, field
from pathlib import Path
import os
import sys
import tomllib
import tomli_w
from .profiles import ModelProfile, RoutingSettings
from .semantic_intent import SemanticSettings
from .community_evidence import ExperienceSourcePolicy


@dataclass
class UISettings:
    font_scale: float = 1.2
    density: str = "normal"
    high_contrast: bool = False
    language: str = "system"


@dataclass
class AgentSettings:
    max_steps: int = 20
    context_budget: int = 16000  # Conservative estimate: UTF-8 bytes, not tokenizer tokens


@dataclass
class ModelSettings:
    provider: str = "mock"
    base_url: str = "http://localhost:1234/v1"
    model: str = ""
    timeout: float = 120.0
    secret_reference: str = ""


@dataclass
class MemorySettings:
    enabled: bool = True
    retrieval_limit: int = 5


@dataclass
class BenchmarkSettings:
    suite_version: int = 3
    auto_run: bool = False


@dataclass
class OutputResources:
    default_limit: int = 1024
    max_limit: int = 4096
    max_retries: int = 1
    growth_factor: int = 2


@dataclass
class ResourceSettings:
    output: OutputResources = field(default_factory=OutputResources)

@dataclass
class VerificationSettings:
    mode: str='AUTO_SAFE'
    python_executable: str=''
    godot_executable: str=''
    timeout_seconds: float=30
    output_limit: int=8192


@dataclass
class Settings:
    ui: UISettings = field(default_factory=UISettings)
    agent: AgentSettings = field(default_factory=AgentSettings)
    model: ModelSettings = field(default_factory=ModelSettings)
    permissions: dict = field(default_factory=lambda: {"write_file": "ask", "make_directory": "ask"})
    memory: MemorySettings = field(default_factory=MemorySettings)
    benchmark: BenchmarkSettings = field(default_factory=BenchmarkSettings)
    routing: RoutingSettings = field(default_factory=RoutingSettings)
    models: list[ModelProfile] = field(default_factory=list)
    resources: ResourceSettings = field(default_factory=ResourceSettings)
    verification: VerificationSettings = field(default_factory=VerificationSettings)
    experience_sources: ExperienceSourcePolicy = field(default_factory=ExperienceSourcePolicy)
    diagnostic_trace: bool = False
    semantic_resolver: SemanticSettings = field(default_factory=SemanticSettings)

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
        result = cls()
        result.diagnostic_trace=raw.get('diagnostic_trace',False)
        for key, factory in (("ui", UISettings), ("agent", AgentSettings),
                             ("model", ModelSettings), ("memory", MemorySettings), ("benchmark", BenchmarkSettings)):
            if key in raw:
                values=dict(raw[key])
                legacy=values.pop('api_key',None) if key=='model' else None
                if legacy:
                    from .security import save_secret
                    if values.get("secret_reference"):raise ValueError("Legacy-Key und Secret-Referenz gleichzeitig; Config bleibt unverändert.")
                    values["secret_reference"]=save_secret(values.get("provider","mock"),values.get("base_url",""),legacy)
                setattr(result, key, factory(**values))
        result.permissions.update(raw.get("permissions", {}))
        result.routing = RoutingSettings(**raw.get("routing", {}))
        result.models = [ModelProfile.from_dict(item) for item in raw.get("models", [])]
        result.resources = ResourceSettings(OutputResources(**raw.get('resources',{}).get('output',{})))
        result.verification = VerificationSettings(**raw.get('verification',{}))
        result.semantic_resolver = SemanticSettings(**raw.get('semantic_resolver',{}))
        result.experience_sources = ExperienceSourcePolicy(**raw.get('experience_sources',{}))
        # The installed fixtures/validators define the suite version. Old rows stay historical.
        result.benchmark.suite_version = BenchmarkSettings().suite_version
        result.validate()
        # Only replace plaintext after every OS-store write/read confirmation succeeded. No plaintext backup.
        if any(isinstance(item,dict) and 'api_key' in item for item in [raw.get("model",{}),*raw.get("models",[])]):result.save(path)
        return result

    def validate(self):
        self.semantic_resolver.validate()
        self.experience_sources.validate()
        if type(self.diagnostic_trace) is not bool:raise ValueError('diagnostic_trace muss true/false sein.')
        from .security import validate_reference
        validate_reference(self.model.secret_reference)
        v=self.verification
        if v.mode not in {'OFF','ASK','AUTO_SAFE'} or not 0.1<=v.timeout_seconds<=120 or type(v.output_limit) is not int or not 256<=v.output_limit<=16384:
            raise ValueError('Verifikation: OFF/ASK/AUTO_SAFE, Timeout 0.1–120 s, Ausgabe 256–16384 Bytes.')
        for executable in [v.python_executable,v.godot_executable]:
            if executable and (not Path(executable).is_absolute() or not Path(executable).is_file()):raise ValueError('Verifier-Executable muss eine existierende absolute Datei sein.')
        output = self.resources.output
        if any(type(v) is not int for v in (output.default_limit,output.max_limit,output.max_retries,output.growth_factor)) or not 64 <= output.default_limit <= output.max_limit <= 65536 or output.max_retries not in {0,1} or output.growth_factor != 2:
            raise ValueError('Output-Ressourcen: 64 ≤ default_limit ≤ max_limit ≤ 65536, max_retries 0/1, growth_factor 2.')
        if not 1 <= self.agent.max_steps <= 100:
            raise ValueError("max_steps muss zwischen 1 und 100 liegen.")
        if not 2048 <= self.agent.context_budget <= 1000000:
            raise ValueError("context_budget muss zwischen 2048 und 1000000 liegen.")
        if self.model.provider not in {"mock", "ollama", "lmstudio"}:
            raise ValueError("Unbekannter Modellanbieter.")
        if self.ui.language not in {"system","de","en","fr"}:raise ValueError("Unsupported UI language.")
        if not 0.8 <= self.ui.font_scale <= 2.0:
            raise ValueError("Schriftskalierung muss zwischen 0.8 und 2.0 liegen.")
        if self.ui.density not in {"kompakt", "normal", "groß"}:
            raise ValueError("Unbekannte UI-Größe.")
        if not 1 <= self.memory.retrieval_limit <= 20:
            raise ValueError("Memory-Limit muss zwischen 1 und 20 liegen.")
        if not 1 <= self.model.timeout <= 600:
            raise ValueError("Zeitlimit muss zwischen 1 und 600 Sekunden liegen.")
        if any(v not in {"ask", "auto", "block"} for v in self.permissions.values()):
            raise ValueError("Erlaubte Dateiregeln: ask, auto, block.")
        if self.routing.mode not in {"auto", "manual"} or not isinstance(self.routing.allow_escalation, bool) or not isinstance(self.routing.max_escalations_per_task, int) or isinstance(self.routing.max_escalations_per_task, bool) or not 0 <= self.routing.max_escalations_per_task <= 3:
            raise ValueError("Routing-Modus ungültig oder Eskalationslimit außerhalb 0–3.")
        if not 1 <= self.routing.min_samples_for_experience <= 1000:
            raise ValueError("Erfahrungsrouting benötigt 1–1000 geprüfte Versuche.")
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1 for v in self.routing.quality.values()):
            raise ValueError("Mindestqualität muss zwischen 0 und 1 liegen.")
        if not isinstance(self.routing.use_experience, bool) or not isinstance(self.routing.allow_skip_from_experience, bool):
            raise ValueError("Erfahrungsrouting benötigt boolesche Schalter.")
        if self.benchmark.suite_version != 3 or self.benchmark.auto_run is not False:
            raise ValueError("Benchmark-Suite 3 wird ausschließlich bewusst gestartet (auto_run=false).")
        identifiers = [profile.id for profile in self.models]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Modell-IDs müssen eindeutig sein.")
        for profile in self.models:
            profile.validate()
        for key in (self.routing.default_model, self.routing.fallback_model, self.routing.manual_model):
            if key and self.models and key not in identifiers:
                raise ValueError(f"Routing verweist auf unbekanntes Modell: {key}")

    def save(self, path: Path):
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_bytes(tomli_w.dumps(asdict(self)).encode("utf-8"))
        os.replace(temp, path)


def data_directory() -> Path:
    # Existing portable installs keep their data in place; never copy or overwrite it.
    if os.environ.get("ALIENAPE_DATA_DIR"):return Path(os.environ["ALIENAPE_DATA_DIR"])
    if getattr(sys,"frozen",False):
        legacy=Path(sys.executable).parent / "data"
        if legacy.is_dir():return legacy
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "AlienApe"


def bundled_directory() -> Path:
    return Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
