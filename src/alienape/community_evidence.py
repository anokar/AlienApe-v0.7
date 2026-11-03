"""Portable, untrusted observations kept outside local model Experience."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import uuid

from jsonschema import Draft202012Validator

from .security import redactor


MAX_CARD_BYTES = 64 * 1024
CARD_SCHEMA_VERSION = "1.0.0"


@dataclass
class ExperienceSourcePolicy:
    local_live_enabled: bool = True
    community_enabled: bool = False

    def validate(self):
        if type(self.local_live_enabled) is not bool or type(self.community_enabled) is not bool:
            raise ValueError("Experience source switches must be booleans.")


class TrustState:
    UNVERIFIED = "UNVERIFIED"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"


class CardValidationError(ValueError):
    pass


class CardConflictError(ValueError):
    pass


def _nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def _string(maximum, pattern=None, minimum=1):
    value = {"type": "string", "minLength": minimum, "maxLength": maximum}
    if pattern:
        value["pattern"] = pattern
    return value


_HEX64 = r"^[a-f0-9]{64}$"
_UUID4 = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_LANGUAGE = r"^(?:[a-z]{2,3}|UNKNOWN)$"
_CATEGORY = r"^(?:[A-Z][A-Z0-9_]{0,63}|UNKNOWN)$"
_SAFE_ID = r"^[A-Za-z0-9_.:+-]{1,120}$"
_TIMESTAMP = r"^\d{4}-\d{2}-\d{2}T[^\s]{1,24}(?:Z|[+-]\d{2}:\d{2})$"

CARD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AlienApe Community Observation Card",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "card_id", "created_at", "exported_at", "model_identity",
        "task", "conditions", "outcomes", "known_limitations", "provenance", "integrity",
    ],
    "properties": {
        "schema_version": {"const": CARD_SCHEMA_VERSION},
        "card_id": _string(36, _UUID4, 36),
        "created_at": _string(32, _TIMESTAMP),
        "exported_at": _string(32, _TIMESTAMP),
        "model_identity": {
            "type": "object", "additionalProperties": False,
            "required": ["provider", "model_name", "fingerprint", "digest", "quantization", "backend_version"],
            "properties": {
                "provider": _nullable(_string(64, _SAFE_ID)),
                "model_name": _nullable(_string(120, _SAFE_ID)),
                "fingerprint": _nullable(_string(64, _HEX64, 64)),
                "digest": _nullable(_string(64, _HEX64, 64)),
                "quantization": _nullable(_string(40, _SAFE_ID)),
                "backend_version": _nullable(_string(64, _SAFE_ID)),
            },
        },
        "task": {
            "type": "object", "additionalProperties": False,
            "required": ["category", "description"],
            "properties": {
                "category": _string(64, _CATEGORY),
                "description": _string(240),
            },
        },
        "conditions": {
            "type": "object", "additionalProperties": False,
            "required": ["response_contract", "transport", "language", "temperature", "max_output_tokens", "context_budget"],
            "properties": {
                "response_contract": {"enum": ["FILE_CONTENT", "FILE_PROPOSAL", "TOOL_ACTION", "STRUCTURED_JSON", "ANALYSIS_RESULT", "PLAIN_TEXT", "UNKNOWN"]},
                "transport": {"enum": ["native", "none", "json_fallback", "UNKNOWN"]},
                "language": _nullable(_string(7, _LANGUAGE)),
                "temperature": _nullable({"type": "number", "minimum": 0, "maximum": 2}),
                "max_output_tokens": _nullable({"type": "integer", "minimum": 1, "maximum": 1000000}),
                "context_budget": _nullable({"type": "integer", "minimum": 1, "maximum": 100000000}),
            },
        },
        "outcomes": {
            "type": "object", "additionalProperties": False,
            "required": ["protocol", "task_quality", "verification", "failure_class", "failure_type"],
            "properties": {
                "protocol": {"enum": ["VERIFIED_SUCCESS", "VERIFIED_FAILURE", "UNKNOWN"]},
                "task_quality": {"enum": ["VERIFIED_SUCCESS", "VERIFIED_FAILURE", "USER_CONFIRMED_SUCCESS", "USER_CONFIRMED_FAILURE", "UNKNOWN"]},
                "verification": {"enum": ["NOT_RUN", "PASS", "FAIL", "UNAVAILABLE", "ERROR", "TIMEOUT", "ABORTED", "UNKNOWN"]},
                "failure_class": {"enum": ["RESOURCE", "CONTROLLER", "PROTOCOL", "VALIDATION", "MODEL_QUALITY", "UNKNOWN", "NONE"]},
                "failure_type": {"enum": ["SUCCESS", "RESOURCE_OUTPUT_LIMIT", "RESOURCE_CONTEXT_LIMIT", "TIMEOUT", "FORMAT_FAILURE", "VALIDATION_FAILURE", "TOOL_FAILURE", "PERMISSION_FAILURE", "INFORMATION_MISSING", "MODEL_CAPABILITY_FAILURE", "UNKNOWN"]},
            },
        },
        "known_limitations": _string(500, minimum=0),
        "provenance": {
            "type": "object", "additionalProperties": False,
            "required": ["source", "observed_at", "suite_version", "application_version"],
            "properties": {
                "source": {"enum": ["live", "benchmark"]},
                "observed_at": _string(32, _TIMESTAMP),
                "suite_version": _nullable({"type": "integer", "minimum": 1, "maximum": 1000}),
                "application_version": _string(32, r"^[A-Za-z0-9.+-]+$"),
            },
        },
        "integrity": {
            "type": "object", "additionalProperties": False,
            "required": ["algorithm", "content_sha256"],
            "properties": {
                "algorithm": {"const": "SHA-256"},
                "content_sha256": _string(64, _HEX64, 64),
            },
        },
    },
}

_VALIDATOR = Draft202012Validator(CARD_SCHEMA)
_CONTRACTS = {"FILE_CONTENT", "FILE_PROPOSAL", "TOOL_ACTION", "STRUCTURED_JSON", "ANALYSIS_RESULT", "PLAIN_TEXT"}
_VERIFICATION = {"NOT_RUN", "PASS", "FAIL", "UNAVAILABLE", "ERROR", "TIMEOUT", "ABORTED"}
_FAILURE_TYPES = {
    "SUCCESS", "RESOURCE_OUTPUT_LIMIT", "RESOURCE_CONTEXT_LIMIT", "TIMEOUT", "FORMAT_FAILURE",
    "VALIDATION_FAILURE", "TOOL_FAILURE", "PERMISSION_FAILURE", "INFORMATION_MISSING",
    "MODEL_CAPABILITY_FAILURE", "UNKNOWN",
}
_PRIVATE_TEXT = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.-]*://|\bwww\.|\b[A-Z]:[\\/]|\\\\[^\\/]+[\\/]|"
    r"/(?:users|home)/|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|"
    r"\b(?:api[-_ ]?key|access[-_ ]?token|password|passwd|private[-_ ]?key|credential|secret)\b|"
    r"\bBearer\s+\S+|\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:localhost|127\.0\.0\.1):\d{2,5})"
)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_payload(card):
    """Canonical card content excluding the integrity envelope itself."""
    payload = {key: value for key, value in card.items() if key != "integrity"}
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CardValidationError("Card cannot be represented as canonical JSON") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CardValidationError("Duplicate JSON property")
        result[key] = value
    return result


def parse_card(raw):
    if isinstance(raw, str):
        try:
            raw_bytes = raw.encode("utf-8")
        except UnicodeError as exc:
            raise CardValidationError("Card is not valid UTF-8") from exc
    elif isinstance(raw, (bytes, bytearray)):
        raw_bytes = bytes(raw)
    else:
        raise CardValidationError("Card input must be UTF-8 JSON")
    if len(raw_bytes) > MAX_CARD_BYTES:
        raise CardValidationError("Card exceeds the 64 KiB import limit")
    try:
        return json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=lambda _: (_ for _ in ()).throw(CardValidationError("Invalid JSON number")))
    except CardValidationError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise CardValidationError("Malformed Community Card JSON") from exc


def validate_card(card):
    if not isinstance(card, dict):
        raise CardValidationError("Card root must be a JSON object")
    if card.get("schema_version") != CARD_SCHEMA_VERSION:
        raise CardValidationError("Unsupported Community Card schema version")
    errors = sorted(_VALIDATOR.iter_errors(card), key=lambda error: list(error.path))
    if errors:
        raise CardValidationError("Card does not match the strict schema: " + errors[0].message[:180])
    for key in ("created_at", "exported_at", "provenance"):
        stamp = card[key] if key != "provenance" else card[key]["observed_at"]
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CardValidationError("Card timestamps must be valid ISO-8601 values") from exc
        if parsed.tzinfo is None:
            raise CardValidationError("Card timestamps must include a timezone")
    expected = hashlib.sha256(canonical_payload(card)).hexdigest()
    supplied = card["integrity"]["content_sha256"]
    if not hmac.compare_digest(expected, supplied):
        raise CardValidationError("Card content hash does not match")
    return card


def card_state(card, fingerprint=None, now=None):
    """Valid cards are always unverified; hashes do not authenticate issuers."""
    if not isinstance(card, dict) or card.get("schema_version") != CARD_SCHEMA_VERSION:
        return TrustState.UNSUPPORTED_SCHEMA
    try:
        validate_card(card)
    except CardValidationError:
        return TrustState.UNVERIFIED
    return TrustState.UNVERIFIED


def _safe_label(value, maximum=120):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not 1 <= len(value) <= maximum or not re.fullmatch(r"[A-Za-z0-9_.:+-]+", value):
        return None
    if "://" in value or value.startswith((".", "-")):
        return None
    return value


def _nullable_int(value, maximum):
    return value if type(value) is int and 1 <= value <= maximum else None


def _safe_user_text(value, maximum, *, required=False):
    if not isinstance(value, str):
        raise CardValidationError("Preview text must be plain text")
    value = value.strip()
    if required and not value:
        raise CardValidationError("A reviewed generalized description is required")
    if len(value) > maximum:
        raise CardValidationError(f"Preview text exceeds {maximum} characters")
    if _PRIVATE_TEXT.search(value) or redactor.text(value) != value:
        raise CardValidationError("Text appears to contain private data; this observation was not exported")
    return value


def _failure_class(signal, failure_type):
    if failure_type in {"RESOURCE_OUTPUT_LIMIT", "RESOURCE_CONTEXT_LIMIT"} or signal == "RESOURCE_FAILURE":
        return "RESOURCE"
    if failure_type in {"TOOL_FAILURE", "PERMISSION_FAILURE", "INFORMATION_MISSING", "TIMEOUT"}:
        return "CONTROLLER"
    if failure_type == "FORMAT_FAILURE":
        return "PROTOCOL"
    if failure_type == "VALIDATION_FAILURE":
        return "VALIDATION"
    if failure_type == "MODEL_CAPABILITY_FAILURE":
        return "MODEL_QUALITY"
    if failure_type == "SUCCESS" and signal in {"VERIFIED_SUCCESS", "USER_CONFIRMED_SUCCESS"}:
        return "NONE"
    return "UNKNOWN" if failure_type not in {None, ""} or signal in {"VERIFIED_FAILURE", "USER_CONFIRMED_FAILURE"} else "NONE"


def _task_quality(signal, failure_type):
    if signal == "RESOURCE_FAILURE" or failure_type in {
        "TOOL_FAILURE", "PERMISSION_FAILURE", "INFORMATION_MISSING", "TIMEOUT",
        "RESOURCE_OUTPUT_LIMIT", "RESOURCE_CONTEXT_LIMIT", "FORMAT_FAILURE",
    }:
        return "UNKNOWN"
    if signal in {"VERIFIED_SUCCESS", "VERIFIED_FAILURE", "USER_CONFIRMED_SUCCESS", "USER_CONFIRMED_FAILURE"}:
        return signal
    return "UNKNOWN"


def _protocol_outcome(value):
    return value if value in {"VERIFIED_SUCCESS", "VERIFIED_FAILURE"} else "UNKNOWN"


def _verification_status(raw):
    if not isinstance(raw, str) or not raw:
        return "UNKNOWN"
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return "UNKNOWN"
    if not isinstance(value, dict):
        return "UNKNOWN"
    status = value.get("verification_status", value.get("post_status", value.get("status")))
    return status if status in _VERIFICATION else "UNKNOWN"


def _build_card(row, description, limitations):
    description = _safe_user_text(description, 240, required=True)
    limitations = _safe_user_text(limitations, 500)
    identity = {}
    try:
        identity = json.loads(row["identity_json"] or "{}")
    except (ValueError, TypeError):
        pass
    if not isinstance(identity, dict):
        identity = {}
    source = row["source"] if row["source"] in {"live", "benchmark"} else None
    if source is None:
        raise CardValidationError("Only local live or benchmark observations can be exported")
    category = str(row["task_category"] or "UNKNOWN").upper()
    if not re.fullmatch(_CATEGORY, category):
        category = "UNKNOWN"
    signal = str(row["success_signal"] or "UNKNOWN")
    failure_type = str(row["failure_type"] or "UNKNOWN")
    if failure_type not in _FAILURE_TYPES:
        failure_type = "UNKNOWN"
    contract = row["response_contract"] if row["response_contract"] in _CONTRACTS else "UNKNOWN"
    tool_mode = identity.get("tool_mode")
    transport = tool_mode if tool_mode in {"native", "none", "json_fallback"} else "UNKNOWN"
    language = row["language_v2"] if isinstance(row["language_v2"], str) and re.fullmatch(r"[a-z]{2,3}", row["language_v2"]) else None
    temperature = identity.get("temperature")
    if type(temperature) not in {int, float} or not 0 <= temperature <= 2:
        temperature = None
    identity_fingerprint = row["model_fingerprint_id"]
    if not isinstance(identity_fingerprint, str) or not re.fullmatch(_HEX64, identity_fingerprint):
        identity_fingerprint = None
    digest = identity.get("digest") if isinstance(identity.get("digest"), str) and re.fullmatch(_HEX64, identity["digest"]) else None
    quantization = _safe_label(identity.get("quantization"), 40)
    backend_version = _safe_label(identity.get("backend_version"), 64)
    verification = _verification_status(row["verification_json"])
    observed_at = row["timestamp"] if isinstance(row["timestamp"], str) else utc_now()
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed_at = utc_now()
    except ValueError:
        observed_at = utc_now()
    from . import __version__
    created = utc_now()
    card = {
        "schema_version": CARD_SCHEMA_VERSION,
        "card_id": str(uuid.uuid4()),
        "created_at": created,
        "exported_at": created,
        "model_identity": {
            "provider": _safe_label(identity.get("provider"), 64),
            "model_name": _safe_label(identity.get("model")),
            "fingerprint": identity_fingerprint,
            "digest": digest,
            "quantization": quantization,
            "backend_version": backend_version,
        },
        "task": {"category": category, "description": description},
        "conditions": {
            "response_contract": contract,
            "transport": transport,
            "language": language,
            "temperature": temperature,
            "max_output_tokens": _nullable_int(identity.get("max_output_tokens"), 1000000),
            "context_budget": _nullable_int(identity.get("context_budget"), 100000000),
        },
        "outcomes": {
            "protocol": _protocol_outcome(row["protocol_status"]),
            "task_quality": _task_quality(signal, failure_type),
            "verification": verification,
            "failure_class": _failure_class(signal, failure_type),
            "failure_type": failure_type,
        },
        "known_limitations": limitations,
        "provenance": {
            "source": source,
            "observed_at": observed_at,
            "suite_version": row["suite_version"] if type(row["suite_version"]) is int and 1 <= row["suite_version"] <= 1000 else None,
            "application_version": __version__,
        },
    }
    card["integrity"] = {"algorithm": "SHA-256", "content_sha256": hashlib.sha256(canonical_payload(card)).hexdigest()}
    return validate_card(card)


def list_local_observations(experience_store, limit=1000):
    """Return display-safe rows; database identifiers stay local to the UI."""
    with experience_store.connection() as db:
        rows = db.execute("""SELECT e.id,e.timestamp,e.task_category,e.response_contract,e.success_signal,e.failure_type,
                e.source,e.model_fingerprint_id,f.identity_json
            FROM model_experience e LEFT JOIN model_fingerprints f ON f.id=e.model_fingerprint_id
            WHERE e.source IN ('live','benchmark') AND e.parent_experience_id IS NULL
            ORDER BY e.timestamp DESC,e.id DESC LIMIT ?""", (max(1, min(int(limit), 5000)),)).fetchall()
    result = []
    for row in rows:
        signal = str(row["success_signal"] or "UNKNOWN")
        failure = str(row["failure_type"] or "UNKNOWN")
        identity = {}
        try:
            identity = json.loads(row["identity_json"] or "{}")
        except (ValueError, TypeError):
            pass
        model = _safe_label(identity.get("model")) or "Unbekanntes Modell"
        result.append({
            "local_id": row["id"], "observed_at": row["timestamp"],
            "model": model, "category": str(row["task_category"] or "UNKNOWN"),
            "contract": row["response_contract"] or "UNKNOWN", "source": row["source"],
            "task_quality": _task_quality(signal, failure),
            "failure_class": _failure_class(signal, failure),
        })
    return result


def export_local_observation(experience_store, local_id, description, limitations=""):
    with experience_store.connection() as db:
        row = db.execute("""SELECT e.timestamp,e.task_category,e.response_contract,e.success_signal,
                e.failure_type,e.source,e.language_v2,e.model_fingerprint_id,e.verification_json,
                e.suite_version,e.protocol_status,f.identity_json
            FROM model_experience e
            LEFT JOIN model_fingerprints f ON f.id=e.model_fingerprint_id
            WHERE e.id=? AND e.source IN ('live','benchmark') AND e.parent_experience_id IS NULL""", (local_id,)).fetchone()
    if row is None:
        raise CardValidationError("The selected local observation is no longer available")
    return _build_card(row, description, limitations)


def serialize_card(card):
    validate_card(card)
    result = json.dumps(card, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    if len(result) > MAX_CARD_BYTES:
        raise CardValidationError("Card exceeds the 64 KiB export limit")
    return result


class CommunityEvidenceStore:
    """Separate local database containing only explicitly imported unverified cards."""

    def __init__(self, database):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS community_cards (
                card_id TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL UNIQUE,
                card_json TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                trust_state TEXT NOT NULL CHECK(trust_state='UNVERIFIED')
            )""")

    def connection(self):
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def import_json(self, raw):
        return self.import_card(parse_card(raw))

    def import_card(self, card):
        validate_card(card)
        card_json = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        card_id = card["card_id"]
        payload_hash = card["integrity"]["content_sha256"]
        with self.connection() as db:
            existing_id = db.execute("SELECT payload_hash FROM community_cards WHERE card_id=?", (card_id,)).fetchone()
            if existing_id:
                if existing_id["payload_hash"] == payload_hash:
                    return "DUPLICATE"
                raise CardConflictError("A different card already uses this card_id")
            existing_hash = db.execute("SELECT card_id FROM community_cards WHERE payload_hash=?", (payload_hash,)).fetchone()
            if existing_hash:
                return "DUPLICATE"
            try:
                db.execute("INSERT INTO community_cards VALUES(?,?,?,?,?)", (card_id, payload_hash, card_json, utc_now(), TrustState.UNVERIFIED))
            except sqlite3.IntegrityError as exc:
                raise CardConflictError("Card conflicts with an existing imported record") from exc
        return "IMPORTED"

    def list_cards(self):
        with self.connection() as db:
            rows = db.execute("SELECT card_id,card_json,imported_at,trust_state FROM community_cards ORDER BY imported_at DESC,card_id").fetchall()
        cards = []
        for row in rows:
            card = json.loads(row["card_json"])
            cards.append({
                "card_id": row["card_id"], "imported_at": row["imported_at"],
                "trust_state": row["trust_state"], "model_name": card["model_identity"]["model_name"] or "UNKNOWN",
                "category": card["task"]["category"], "task_description": card["task"]["description"],
            })
        return cards

    def get_card(self, card_id):
        with self.connection() as db:
            row = db.execute("SELECT card_json,imported_at,trust_state FROM community_cards WHERE card_id=?", (card_id,)).fetchone()
        if row is None:
            return None
        return {"card": json.loads(row["card_json"]), "imported_at": row["imported_at"], "trust_state": row["trust_state"]}


def community_evidence_for_routing(*args, **kwargs):
    """Community Cards are display-only and never contribute to model routing."""
    return ()
