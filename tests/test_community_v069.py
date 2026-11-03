"""Community Cards are portable offline observations, separate from Experience and routing."""
import hashlib
import json

import pytest
from PyQt6.QtWidgets import QApplication, QPlainTextEdit

from alienape.community_evidence import (
    CARD_SCHEMA_VERSION, CardConflictError, CardValidationError,
    CommunityEvidenceStore, TrustState, canonical_payload, card_state,
    community_evidence_for_routing, export_local_observation,
    list_local_observations, serialize_card,
)
from alienape.config import Settings
from alienape.community_ui import CommunityCardsPanel
from alienape.experience import ExperienceStore, ModelFingerprint, SuccessSignal
from alienape.routing import ModelRegistry, ModelRouter
from alienape.ui import MainWindow
from test_routing import profile


def seed_observation(tmp_path, *, signal=SuccessSignal.VERIFIED_SUCCESS, failure_type=None,
                     protocol_status="VERIFIED_SUCCESS", identity_update=None,
                     verification=None, response_contract="FILE_CONTENT"):
    model = profile("community-coder", "coding", "medium")
    experience = ExperienceStore(tmp_path / "experience.sqlite3")
    fingerprint = ModelFingerprint.for_profile(model)
    experience.register(model, fingerprint)
    observation_id = experience.record(
        fingerprint.id, "PYTHON_DEBUG", signal, source="live",
        failure_type=failure_type, response_contract=response_contract,
        protocol_status=protocol_status, verification=verification, language_v2="de",
    )
    if identity_update:
        with experience.connection() as db:
            row = db.execute("SELECT identity_json FROM model_fingerprints WHERE id=?", (fingerprint.id,)).fetchone()
            identity = json.loads(row[0])
            identity.update(identity_update)
            db.execute("UPDATE model_fingerprints SET identity_json=? WHERE id=?", (json.dumps(identity), fingerprint.id))
    return experience, observation_id, fingerprint, model


def rehash(card):
    card["integrity"]["content_sha256"] = hashlib.sha256(canonical_payload(card)).hexdigest()
    return card


def exported(experience, observation_id, description="General code observation", limitations=""):
    return export_local_observation(experience, observation_id, description, limitations)


def test_roundtrip_is_separate_and_always_unverified(tmp_path):
    experience, observation_id, _, _ = seed_observation(tmp_path)
    community = CommunityEvidenceStore(tmp_path / "community.sqlite3")
    with experience.connection() as db:
        before = db.execute("SELECT id,source,success_signal,protocol_status FROM model_experience ORDER BY id").fetchall()
    card = exported(experience, observation_id, "General code review observation")
    raw = serialize_card(card)
    assert card_state(card) == TrustState.UNVERIFIED
    assert community.import_json(raw) == "IMPORTED"
    assert community.import_json(raw) == "DUPLICATE"
    stored = community.get_card(card["card_id"])
    assert stored["trust_state"] == "UNVERIFIED"
    assert stored["card"] == card
    with experience.connection() as db:
        after = db.execute("SELECT id,source,success_signal,protocol_status FROM model_experience ORDER BY id").fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    assert community_evidence_for_routing(card) == ()


def test_unknown_fingerprint_metadata_stays_null(tmp_path):
    experience, observation_id, _, _ = seed_observation(tmp_path)
    with experience.connection() as db:
        db.execute("UPDATE model_experience SET model_fingerprint_id='not-a-fingerprint' WHERE id=?", (observation_id,))
    card = exported(experience, observation_id)
    assert card["model_identity"]["fingerprint"] is None
    assert card["model_identity"]["digest"] is None
    assert card["model_identity"]["quantization"] is None
    assert card["conditions"]["transport"] in {"none", "native", "json_fallback", "UNKNOWN"}
    community = CommunityEvidenceStore(tmp_path / "community.sqlite3")
    assert community.import_card(card) == "IMPORTED"
    assert community.get_card(card["card_id"])["trust_state"] == "UNVERIFIED"


def test_negative_protocol_evidence_remains_distinct_from_task_quality(tmp_path):
    experience, observation_id, _, _ = seed_observation(
        tmp_path, signal=SuccessSignal.VERIFIED_FAILURE,
        failure_type="MODEL_CAPABILITY_FAILURE", protocol_status="VERIFIED_FAILURE",
        verification={"verification_status": "FAIL", "private_details": "sensitive"},
    )
    card = exported(experience, observation_id)
    assert card["outcomes"]["protocol"] == "VERIFIED_FAILURE"
    assert card["outcomes"]["task_quality"] == "VERIFIED_FAILURE"
    assert card["outcomes"]["verification"] == "FAIL"
    assert "private_details" not in json.dumps(card)


@pytest.mark.parametrize(("signal", "failure_type", "expected"), [
    (SuccessSignal.RESOURCE_FAILURE, "RESOURCE_OUTPUT_LIMIT", "RESOURCE"),
    (SuccessSignal.UNKNOWN, "TOOL_FAILURE", "CONTROLLER"),
    (SuccessSignal.VERIFIED_FAILURE, "TOOL_FAILURE", "CONTROLLER"),
])
def test_resource_and_controller_observations_are_quality_neutral(tmp_path, signal, failure_type, expected):
    experience, observation_id, _, _ = seed_observation(
        tmp_path, signal=signal, failure_type=failure_type, protocol_status=None,
    )
    card = exported(experience, observation_id)
    assert card["outcomes"]["protocol"] == "UNKNOWN"
    assert card["outcomes"]["task_quality"] == "UNKNOWN"
    assert card["outcomes"]["failure_class"] == expected
    assert card["outcomes"]["failure_type"] == failure_type


def test_private_endpoint_prompt_answer_path_and_secrets_are_not_exported(tmp_path):
    experience, observation_id, fingerprint, _ = seed_observation(
        tmp_path,
        identity_update={
            "endpoint": "http://private.example/v1?api_key=topsecret",
            "model": "coder-model",
            "digest": "a" * 64,
            "quantization": "Q4_K_M",
        },
    )
    with experience.connection() as db:
        db.execute(
            "UPDATE model_experience SET validation_reason=?,tool_observation_json=?,session_id=? WHERE id=?",
            ("private prompt /Users/alienx/secret.py", '{"answer":"raw model answer"}', "user-session-private", observation_id),
        )
    card = exported(experience, observation_id, "Reviewed general debugging result")
    output = serialize_card(card).decode("utf-8")
    for private in ("private.example", "topsecret", "/Users/alienx", "raw model answer", "user-session-private", "validation_reason", fingerprint.endpoint):
        assert private not in output
    assert card["model_identity"]["digest"] == "a" * 64


def test_review_text_rejects_obvious_private_values(tmp_path):
    experience, observation_id, _, _ = seed_observation(tmp_path)
    with pytest.raises(CardValidationError):
        exported(experience, observation_id, "Task URL http://private.example")
    with pytest.raises(CardValidationError):
        exported(experience, observation_id, "Uses token secret: ABC")


def test_import_rejects_malformed_duplicate_keys_unsupported_extra_and_oversized(tmp_path):
    store = CommunityEvidenceStore(tmp_path / "community.sqlite3")
    with pytest.raises(CardValidationError):
        store.import_json('{"field":1,"field":2}')
    experience, observation_id, _, _ = seed_observation(tmp_path)
    card = exported(experience, observation_id)
    unsupported = dict(card, schema_version="99.0")
    with pytest.raises(CardValidationError, match="Unsupported"):
        store.import_card(unsupported)
    dangerous_key = "<img src=x onerror=execute()>"
    extra = dict(card, **{dangerous_key: "untrusted"})
    with pytest.raises(CardValidationError, match="strict schema") as error:
        store.import_card(extra)
    assert dangerous_key not in str(error.value)
    missing_provenance = dict(card)
    del missing_provenance["provenance"]
    with pytest.raises(CardValidationError, match="strict schema"):
        store.import_card(missing_provenance)
    long_text = json.loads(json.dumps(card))
    long_text["task"]["description"] = "x" * 241
    rehash(long_text)
    with pytest.raises(CardValidationError, match="strict schema"):
        store.import_card(long_text)
    wrong_type = json.loads(json.dumps(card))
    wrong_type["outcomes"] = []
    with pytest.raises(CardValidationError, match="strict schema"):
        store.import_card(wrong_type)
    with pytest.raises(CardValidationError, match="64 KiB"):
        store.import_json(b" " * (64 * 1024 + 1))
    tampered = json.loads(json.dumps(card))
    tampered["task"]["description"] = "Integrity was altered"
    with pytest.raises(CardValidationError, match="content hash"):
        store.import_card(tampered)


def test_duplicate_card_id_conflict_is_rejected(tmp_path):
    experience, observation_id, _, _ = seed_observation(tmp_path)
    card = exported(experience, observation_id)
    store = CommunityEvidenceStore(tmp_path / "community.sqlite3")
    assert store.import_card(card) == "IMPORTED"
    conflicting = json.loads(json.dumps(card))
    conflicting["task"]["description"] = "Different observation under same id"
    rehash(conflicting)
    with pytest.raises(CardConflictError):
        store.import_card(conflicting)


def test_community_default_off_and_import_has_no_router_effect(tmp_path):
    settings = Settings()
    assert settings.experience_sources.community_enabled is False
    experience, observation_id, fingerprint, model = seed_observation(tmp_path)
    settings.models = [model]
    router = ModelRouter(ModelRegistry([model]), settings.routing, experience, {model.id: fingerprint.id}, source_policy=settings.experience_sources)
    before = router.select_model("Debug Python").profile.id
    community = CommunityEvidenceStore(tmp_path / "community.sqlite3")
    community.import_card(exported(experience, observation_id))
    settings.experience_sources.community_enabled = True
    after = router.select_model("Debug Python").profile.id
    assert before == after
    assert community_evidence_for_routing(community.list_cards()) == ()


def test_local_observation_listing_is_individual_and_display_safe(tmp_path):
    experience, first, _, _ = seed_observation(tmp_path)
    with experience.connection() as db:
        db.execute("UPDATE model_experience SET session_id='sensitive-session-id' WHERE id=?", (first,))
    rows = list_local_observations(experience)
    assert len(rows) == 1
    assert rows[0]["local_id"] == first
    assert "session_id" not in rows[0]
    assert "sensitive-session-id" not in json.dumps(rows)


def test_imported_html_urls_and_unicode_controls_stay_inert_and_visible(tmp_path):
    app = QApplication.instance() or QApplication([])
    experience, observation_id, _, _ = seed_observation(tmp_path)
    card = exported(experience, observation_id)
    untrusted_text = '<img src=x onerror=execute()> https://example.invalid bidi\u202etxt\u202c zero\u200b'
    card["task"]["description"] = untrusted_text
    rehash(card)
    community = CommunityEvidenceStore(tmp_path / "community.sqlite3")
    assert community.import_card(card) == "IMPORTED"
    panel = CommunityCardsPanel(experience, community)
    panel.card_table.selectRow(0)
    shown = panel.card_detail.toPlainText()
    assert isinstance(panel.card_detail, QPlainTextEdit)
    assert "<img src=x onerror=execute()>" in shown
    assert "https://example.invalid" in shown
    assert "⟦U+202E⟧" in shown and "⟦U+202C⟧" in shown and "⟦U+200B⟧" in shown
    assert "\u202e" not in shown and "\u202c" not in shown and "\u200b" not in shown
    assert community.get_card(card["card_id"])["card"]["task"]["description"] == untrusted_text
    panel.close()
    app.processEvents()


def test_main_window_has_a_separate_community_cards_panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Settings(), tmp_path / "data")
    from alienape.community_ui import CommunityCardsPanel
    panel = window.findChild(CommunityCardsPanel)
    assert panel is not None
    assert panel.community_store.database.name == "community_evidence.sqlite3"
    assert panel.experience_store.database.name == "model_experience.sqlite3"
    assert window.tabs.tabText(window.tabs.indexOf(panel)) == "Community Cards"
    window.close()
