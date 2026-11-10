from pathlib import Path
import json
import os
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]

_WORKER = r'''
import json
import sys
from pathlib import Path
from alienape.config import data_directory
from alienape.experience import ExperienceStore, ModelFingerprint, SuccessSignal
from alienape.profiles import ModelProfile

action, key = sys.argv[1:3]
root = data_directory()
store = ExperienceStore(root / "model_experience.sqlite3")

if action == "write":
    profile = ModelProfile(
        id=f"synthetic_{key.lower()}",
        display_name=f"Synthetic {key}",
        provider="mock",
        model_name=f"synthetic-{key.lower()}:offline",
        role="general",
    )
    fingerprint = ModelFingerprint(
        provider="mock",
        model=profile.model_name,
        endpoint="offline",
        digest=("a" if key == "A1" else "b") * 64,
        tool_mode="none",
    )
    store.register(profile, fingerprint)
    store.record(
        fingerprint.id,
        "SYNTHETIC_ISOLATION",
        SuccessSignal.UNKNOWN,
        subcategory=key,
        source="live",
        session_id=f"synthetic-session-{key}",
        response_contract="FILE_CONTENT",
        protocol_status="VERIFIED_SUCCESS",
        verification={"status": "synthetic fixture"},
    )
    print(json.dumps({"data_dir": str(root), "fingerprint_id": fingerprint.id}))
elif action == "read":
    with store.connection() as db:
        rows = db.execute("""
            SELECT e.task_subcategory, e.success_signal, e.success_value, e.source,
                   e.session_id, e.protocol_status, e.response_contract,
                   e.verification_json, f.identity_json, f.display_name
            FROM model_experience e
            JOIN model_fingerprints f ON f.id=e.model_fingerprint_id
            WHERE e.task_category=?
        """, ("SYNTHETIC_ISOLATION",)).fetchall()
    print(json.dumps({
        "data_dir": str(root),
        "rows": [dict(row) for row in rows],
        "community_db_exists": (root / "community_evidence.sqlite3").exists(),
    }))
else:
    raise SystemExit("unknown action")
'''


def _run(action, key, data_dir):
    env = os.environ.copy()
    env["ALIENAPE_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", _WORKER, action, key],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, (
        f"isolated child process failed ({action}, {key}): "
        f"{completed.stderr or completed.stdout}"
    )
    return json.loads(completed.stdout.strip())


def test_experience_is_isolated_between_data_roots_across_restarts(tmp_path):
    data_a = tmp_path / "installation-a" / "data"
    data_b = tmp_path / "installation-b" / "data"
    assert data_a != data_b

    written_a = _run("write", "A1", data_a)
    written_b = _run("write", "B1", data_b)
    assert Path(written_a["data_dir"]) == data_a
    assert Path(written_b["data_dir"]) == data_b

    # New child processes simulate application restarts and query each store.
    read_a = _run("read", "A1", data_a)
    read_b = _run("read", "B1", data_b)
    assert Path(read_a["data_dir"]) == data_a
    assert Path(read_b["data_dir"]) == data_b

    assert len(read_a["rows"]) == 1
    assert len(read_b["rows"]) == 1
    row_a, row_b = read_a["rows"][0], read_b["rows"][0]
    assert row_a["task_subcategory"] == "A1"
    assert row_b["task_subcategory"] == "B1"
    assert row_a["success_signal"] == row_b["success_signal"] == "UNKNOWN"
    assert row_a["success_value"] is row_b["success_value"] is None
    assert row_a["source"] == row_b["source"] == "live"
    assert row_a["session_id"] == "synthetic-session-A1"
    assert row_b["session_id"] == "synthetic-session-B1"
    assert row_a["protocol_status"] == row_b["protocol_status"] == "VERIFIED_SUCCESS"
    assert row_a["response_contract"] == row_b["response_contract"] == "FILE_CONTENT"
    assert json.loads(row_a["verification_json"]) == {"status": "synthetic fixture"}
    assert json.loads(row_b["verification_json"]) == {"status": "synthetic fixture"}
    identity_a = json.loads(row_a["identity_json"])
    identity_b = json.loads(row_b["identity_json"])
    assert identity_a["digest"] == "a" * 64
    assert identity_b["digest"] == "b" * 64
    assert identity_a["model"] != identity_b["model"]
    assert row_a["display_name"] == "Synthetic A1"
    assert row_b["display_name"] == "Synthetic B1"

    assert not read_a["community_db_exists"]
    assert not read_b["community_db_exists"]
    assert (data_a / "model_experience.sqlite3").is_file()
    assert (data_b / "model_experience.sqlite3").is_file()
