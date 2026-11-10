import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKER = r'''
import json, sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from alienape.config import Settings, data_directory
from alienape.community_evidence import TrustState, community_evidence_for_routing
from alienape.community_ui import ExportObservationDialog
from alienape.experience import ModelFingerprint, SuccessSignal
from alienape.profiles import ModelProfile
from alienape.ui import MainWindow

action, installation, transfer = sys.argv[1], sys.argv[2], Path(sys.argv[3])
app = QApplication.instance() or QApplication([])
root = data_directory()
window = MainWindow(Settings(), root)

def observe(label):
    profile = ModelProfile(id=f"synthetic_{label.lower()}", display_name=f"Synthetic {label}",
        provider="mock", model_name=f"synthetic-{label.lower()}:offline", base_url="offline",
        role="coding", capabilities=["coding"], tool_mode="none", resource_class="low", context_length=8192)
    fp = ModelFingerprint.for_profile(profile, {"digest": ("a" if label == "A" else "b") * 64,
        "backend_version": "offline-fixture"})
    window.experience.register(profile, fp)
    return window.experience.record(fp.id, "PYTHON_DEBUG", SuccessSignal.UNKNOWN,
        subcategory=f"synthetic-{label}", source="live", session_id=f"synthetic-session-{label}",
        response_contract="FILE_CONTENT", protocol_status="VERIFIED_SUCCESS",
        verification={"verification_status": "PASS", "fixture": True})

def local_rows():
    with window.experience.connection() as db:
        return [tuple(row) for row in db.execute("""SELECT id,model_fingerprint_id,task_category,
            task_subcategory,success_signal,success_value,source,response_contract,protocol_status,
            verification_json,session_id FROM model_experience ORDER BY id""").fetchall()]

if action == "export":
    observation_id = observe("A")
    transfer.parent.mkdir(parents=True, exist_ok=True)
    dialog = ExportObservationDialog(window.experience, observation_id)
    app.processEvents()
    assert dialog.card is not None and dialog.preview.toPlainText()
    assert not dialog.export_button.isEnabled()
    dialog.confirm.setChecked(True); app.processEvents()
    assert dialog.export_button.isEnabled()
    QFileDialog.getSaveFileName = lambda *a, **k: (str(transfer), "JSON (*.json)")
    dialog.export_button.click(); app.processEvents()
    raw = transfer.read_bytes(); card = json.loads(raw.decode("utf-8"))
    assert card["model_identity"]["fingerprint"]
    assert card["outcomes"]["protocol"] == "VERIFIED_SUCCESS"
    assert card["outcomes"]["task_quality"] == "UNKNOWN"
    assert card["outcomes"]["verification"] == "PASS"
    print(json.dumps({"data_dir":str(root),"preview":True,"confirmed":True,
        "card":card,"file_sha256":__import__("hashlib").sha256(raw).hexdigest()}))
else:
    observe("B"); before = local_rows(); raw = transfer.read_bytes(); card = json.loads(raw)
    QFileDialog.getOpenFileName = lambda *a, **k: (str(transfer), "JSON (*.json)")
    QMessageBox.information = lambda *a, **k: QMessageBox.StandardButton.Ok
    panel = window.community_cards_panel
    panel.import_button.click(); app.processEvents()
    assert panel.card_table.rowCount() == 1
    stored = window.community_evidence.get_card(card["card_id"])
    assert stored["trust_state"] == TrustState.UNVERIFIED and stored["card"] == card
    assert local_rows() == before
    panel.import_button.click(); app.processEvents()
    assert panel.card_table.rowCount() == 1 and local_rows() == before
    duplicate = window.community_evidence.import_json(raw)
    assert duplicate == "DUPLICATE"
    assert community_evidence_for_routing(window.community_evidence.list_cards()) == ()
    assert window.experience.database.resolve() != window.community_evidence.database.resolve()
    print(json.dumps({"data_dir":str(root),"local_before":before,"local_after":local_rows(),
        "trust":stored["trust_state"],"card_count":panel.card_table.rowCount(),
        "duplicate":duplicate,"local_community_separate":True,"routing_evidence":[]}))
window.close(); app.processEvents()
'''


def _run(action, label, card_path, data_dir):
    env = os.environ.copy()
    env.update({"ALIENAPE_DATA_DIR":str(data_dir), "PYTHONPATH":str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE":"1", "QT_QPA_PLATFORM":"offscreen"})
    env["TEMP"] = str(data_dir.parent / "tmp"); env["TMP"] = env["TEMP"]; env["TMPDIR"] = env["TEMP"]
    Path(env["TEMP"]).mkdir(parents=True, exist_ok=True)
    result = subprocess.run([sys.executable,"-c",WORKER,action,label,str(card_path)], cwd=ROOT,
        env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip())


def test_community_card_ui_roundtrip_between_isolated_installations(tmp_path):
    data_a = tmp_path / "installation-a" / "data"
    data_b = tmp_path / "installation-b" / "data"
    transfer = tmp_path / "manual-transfer" / "community-card.json"
    exported = _run("export","A",transfer,data_a)
    assert Path(exported["data_dir"]) == data_a and exported["preview"] and exported["confirmed"]
    imported = _run("import","B",transfer,data_b)
    assert Path(imported["data_dir"]) == data_b
    assert len(imported["local_before"]) == 1 and imported["local_after"] == imported["local_before"]
    assert imported["trust"] == "UNVERIFIED" and imported["card_count"] == 1
    assert imported["duplicate"] == "DUPLICATE" and imported["local_community_separate"]
    assert imported["routing_evidence"] == []
    assert imported["local_after"][0][3] == "synthetic-B"
    assert exported["card"]["provenance"]["source"] == "live"
