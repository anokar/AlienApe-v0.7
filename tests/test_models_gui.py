import pytest
from PyQt6.QtWidgets import QApplication, QDialog
from alienape.config import Settings
from alienape.ui import MainWindow
from alienape.model_dialogs import ModelsDialog, ProfileDialog
from test_routing import profile
from test_gui import until, send


@pytest.fixture
def multi_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.models = [profile("general", tool_mode="native"), profile("coder", "coding", "medium", tool_mode="none"),
                       profile("reasoner", "reasoning", "high", tool_mode="none")]
    window = MainWindow(settings, tmp_path / "data")
    window.show()
    until(lambda: window.worker is None and window.model.count() == 4)
    project = tmp_path / "project"
    project.mkdir()
    (project / "player.py").write_text("return a * b", encoding="utf-8")
    window.set_workspace(project)
    yield window
    window.stop()
    until(lambda: window.worker is None)
    window.close()
    app.processEvents()


def test_auto_manual_routing_visible_in_gui(multi_window):
    window = multi_window
    assert window.model.currentText() == "AUTO"
    assert "3 von 3" in window.connection.text()
    send(window, "Finde den Fehler in player.py")
    assert "coder" in window.active_model.text()
    assert "return a * b" in window.chat.toPlainText()
    assert any(e["event_type"] == "TOOL_DELEGATED" for e in window.events)
    assert window.per_model_text.isVisible()
    window.model.setCurrentIndex(window.model.findData("reasoner"))
    assert window.settings.routing.mode == "manual"
    send(window, "Fasse eine kurze Notiz zusammen")
    assert "reasoner" in window.active_model.text()
    assert "Manuelle Auswahl" in window.active_model.text()
    window.model.setCurrentIndex(0)
    assert window.settings.routing.mode == "auto"


def test_profile_editor_and_management_save(multi_window):
    window = multi_window
    dialog = ModelsDialog(window.settings, window)
    assert dialog.table.rowCount() == 3
    editor = ProfileDialog(profile("extra", tool_mode="none"), parent=dialog)
    editor.role.setCurrentText("coding")
    editor.save()
    assert editor.result() == QDialog.DialogCode.Accepted
    assert "coding" in editor.profile.capabilities
    dialog.settings.models.append(editor.profile)
    dialog.refresh()
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert len(dialog.settings.models) == 4
    assert len(window.settings.models) == 3  # Cancel/edit isolation
