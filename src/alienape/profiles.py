"""User-configured capabilities. Names never imply model capabilities."""
from dataclasses import dataclass, field
import re


@dataclass
class ModelProfile:
    id: str
    display_name: str
    provider: str
    model_name: str
    base_url: str = "http://localhost:11434/v1"
    role: str = "general"
    capabilities: list[str] = field(default_factory=lambda: ["general", "chat"])
    tool_mode: str = "none"
    priority: int = 0
    enabled: bool = True
    speed_class: str = "normal"
    resource_class: str = "medium"
    context_length: int = 0  # 0: not configured/unknown; tokens reported by user

    secret_reference: str = ""
    profile_v2: dict = field(default_factory=dict)

    def __post_init__(self):
        self.capabilities = sorted(set(self.capabilities))
        self.tool_mode = self.tool_mode.lower()
        self.resource_class = self.resource_class.lower()
        self.speed_class = self.speed_class.lower().replace("medium", "normal")

    def v2(self):
        from .profile_v2 import ProfileV2, migrate
        return ProfileV2.from_dict(self.profile_v2) if self.profile_v2 else migrate(self)

    @classmethod
    def from_dict(cls, raw):
        values = dict(raw)
        if "model" in values and "model_name" not in values:
            values["model_name"] = values.pop("model")
        values.setdefault("display_name", values.get("id", ""))
        if "base_url" not in values:
            values["base_url"] = "http://localhost:1234/v1" if values.get("provider") == "lmstudio" else "http://localhost:11434/v1"
        legacy=values.pop("api_key", "")
        if legacy:
            from .security import save_secret
            if values.get("secret_reference"):raise ValueError("Legacy-Key und Secret-Referenz gleichzeitig; Config bleibt unverändert.")
            values["secret_reference"]=save_secret(values["provider"],values["base_url"],legacy)
        return cls(**values)

    def validate(self):
        from .security import validate_reference
        validate_reference(self.secret_reference)
        self.v2().validate()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.id):
            raise ValueError("Modell-ID: 1–80 Buchstaben, Ziffern, Bindestriche oder Unterstriche.")
        if not self.display_name.strip() or not self.model_name.strip():
            raise ValueError("Anzeigename und Modellname dürfen nicht leer sein.")
        if self.provider not in {"ollama", "lmstudio", "mock"} or self.role not in {"general", "coding", "reasoning"}:
            raise ValueError("Ungültiger Anbieter oder Modellrolle.")
        if self.tool_mode not in {"native", "none", "json_fallback"}:
            raise ValueError("Tool-Modus muss native, none oder json_fallback sein.")
        if self.tool_mode == "json_fallback" and self.provider == "lmstudio":
            raise ValueError("JSON-Kompatibilitätsmodus wird derzeit für Ollama und Demo unterstützt.")
        if self.resource_class not in {"low", "medium", "high"} or self.speed_class not in {"fast", "normal", "slow"}:
            raise ValueError("Ungültige Ressourcen- oder Geschwindigkeitsklasse.")
        if not isinstance(self.enabled, bool) or not isinstance(self.priority, int) or not -1000 <= self.priority <= 1000:
            raise ValueError("Aktiv-Status oder Priorität ungültig.")
        if not isinstance(self.context_length, int) or self.context_length < 0 or 0 < self.context_length < 2048:
            raise ValueError("Kontextlänge: unbekannt (0) oder mindestens 2048 Tokens.")
        if not self.capabilities or any(not isinstance(c, str) or not re.fullmatch(r"[a-z_]+", c) for c in self.capabilities):
            raise ValueError("Mindestens eine Capability angeben (z. B. general, coding, planning).")
        from .backends import create_backend
        from types import SimpleNamespace
        backend = create_backend(SimpleNamespace(provider=self.provider, base_url=self.base_url, timeout=5))
        if hasattr(backend, "base_url"):
            self.base_url = backend.base_url


@dataclass
class RoutingSettings:
    mode: str = "auto"
    default_model: str = ""
    fallback_model: str = ""
    allow_escalation: bool = True
    max_escalations_per_task: int = 1
    manual_model: str = ""
    use_experience: bool = True
    min_samples_for_experience: int = 5
    allow_skip_from_experience: bool = True
    quality: dict = field(default_factory=lambda: {"default_min_success_rate": 0.80})
