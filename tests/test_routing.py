import asyncio
import pytest
from alienape.config import Settings
from alienape.profiles import ModelProfile
from alienape.routing import ModelRegistry, ModelRouter, TaskType, classify_task, referenced_paths, BackendPool
from alienape.task_packet import TaskPacket
from alienape.context import size


def profile(identifier, role="general", resource="low", enabled=True, **kwargs):
    caps = {"general": ["general", "chat", "summarization"], "coding": ["coding", "debugging"],
            "reasoning": ["planning", "deep_reasoning"]}[role]
    return ModelProfile(identifier, identifier, "mock", identifier, role=role, capabilities=caps,
                        resource_class=resource, enabled=enabled, **kwargs)


def registry():
    return ModelRegistry([profile("general"), profile("coder", "coding", "medium"), profile("reasoner", "reasoning", "high")])


def test_legacy_config_and_profile_roundtrip(tmp_path):
    old = tmp_path / "old.toml"
    old.write_text('[model]\nprovider="ollama"\nbase_url="http://localhost:11434/v1"\nmodel="user-model"\n')
    settings = Settings.load(old)
    assert not settings.models
    assert ModelRegistry.from_settings(settings).enabled()[0].model_name == "user-model"
    settings.models = list(registry().profiles.values())
    settings.routing.default_model = "general"
    settings.routing.fallback_model = "general"
    settings.save(old)
    assert Settings.load(old) == settings


@pytest.mark.parametrize("task,expected", [("Finde den Fehler in player.py", "coder"),
    ("Fasse diese kurze Notiz zusammen", "general"), ("Plane die nächsten Schritte", "reasoner")])
def test_auto_routes_by_configured_capabilities(task, expected):
    settings = Settings()
    assert ModelRouter(registry(), settings.routing).select_model(task).profile.id == expected


def test_disabled_unavailable_and_missing_specialist_fall_back():
    settings = Settings()
    models = registry()
    models.profiles["coder"].enabled = False
    assert ModelRouter(models, settings.routing).select_model("Debug player.py").profile.id == "general"
    models.profiles["coder"].enabled = True
    models.availability["coder"] = False
    assert ModelRouter(models, settings.routing).select_model("Debug player.py").profile.id == "general"
    del models.profiles["coder"]
    assert ModelRouter(models, settings.routing).select_model("Debug player.py").profile.id == "general"


def test_single_manual_and_smallest_model():
    settings = Settings()
    single = ModelRegistry([profile("only", "coding", "medium")])
    # A sole coding profile no longer bypasses declared general-chat eligibility.
    with pytest.raises(ValueError):ModelRouter(single, settings.routing).select_model("Hallo")
    models = registry()
    assert ModelRouter(models, settings.routing).select_model("Debug player.py", state={"manual_model": "reasoner"}).profile.id == "reasoner"
    models.add(profile("expensive", resource="high", priority=100))
    assert ModelRouter(models, settings.routing).select_model("Eine allgemeine Frage").profile.id == "general"


def test_registry_validation_and_pool_reuse():
    with pytest.raises(ValueError, match="Doppelte"):
        ModelRegistry([profile("same"), profile("same")])
    pool = BackendPool()
    models = registry()
    assert pool.get(models.profiles["general"]) is pool.get(models.profiles["coder"])
    assert asyncio.run(models.check_availability()) == ["general", "coder", "reasoner"]


def test_task_packet_budget_and_only_intended_fields():
    packet = TaskPacket("Finde Fehler in player.py", "CODE_TASK", ["coding"],
        relevant_files_or_contents=[{"path": "player.py", "content": "x" * 20000}],
        relevant_memory=[{"content": "relevant"}], resource_budget={"context_bytes": 3000})
    bounded = packet.bounded(3000)
    assert size(bounded.messages()) <= 3000
    assert bounded.relevant_files_or_contents[0]["truncated"]
    assert not hasattr(packet, "history")
    assert "Finde Fehler" in bounded.messages()[-1]["content"]


def test_task_classification_and_file_paths():
    assert classify_task("was hast du dir gemerkt")[0] == TaskType.MEMORY_TASK
    assert referenced_paths('Finde den Fehler in "some folder/player.py" und utils.py') == ["some folder/player.py", "utils.py"]
