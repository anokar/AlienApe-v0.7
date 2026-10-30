import time
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from alienape.config import Settings
from alienape.ui import MainWindow
from alienape.backends import MockBackend, tool_reply


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def until(predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    raise AssertionError("GUI-Aktion hat das Zeitlimit überschritten")


@pytest.fixture
def window(app, tmp_path):
    window = MainWindow(Settings(), tmp_path / "data")
    window.show()
    until(lambda: window.worker is None and window.model.count() == 1)
    project = tmp_path / "project"
    project.mkdir()
    (project / "hello.txt").write_text("GUI-End-to-End", encoding="utf-8")
    window.set_workspace(project)
    yield window
    window.stop()
    until(lambda: window.worker is None)
    window.close()
    QApplication.processEvents()


def send(window, message):
    window.input.setPlainText(message)
    window.send_button.click()
    until(lambda: window.worker is None)


def test_gui_starts_reads_and_retrieves_memory(window):
    assert "MockBackend" in window.connection.text()
    assert window.stop_button.accessibleName()
    send(window, 'Lies "hello.txt"')
    assert "GUI-End-to-End" in window.chat.toPlainText()
    assert window.recent.item(0).text() == "hello.txt"
    send(window, "Merke dir: Wir verwenden Godot 4.")
    assert window.memory_table.rowCount() == 1
    send(window, "was hast du dir alles gemerkt?")
    assert "godot-version: Wir verwenden Godot 4." in window.chat.toPlainText()
    window.clear_button.click()
    assert not window.session.history
    send(window, "Welche Godot-Version verwenden wir?")
    assert "Godot 4" in window.chat.toPlainText()
    assert window.activity_list.count() > 5
    assert window.send_button.isEnabled()


def test_gui_write_permission_and_stop(window):
    window.input.setPlainText('Schreibe "freigegeben" in "out.txt"')
    window.send()
    until(lambda: window.permission_dialog is not None)
    assert not (window.workspace.root / "out.txt").exists()
    window.permission_dialog.finish("once")
    until(lambda: window.worker is None)
    assert (window.workspace.root / "out.txt").read_text() == "freigegeben"
    window.input.setPlainText('Schreibe "niemals" in "stopped.txt"')
    window.send()
    until(lambda: window.permission_dialog is not None)
    window.stop_button.click()
    until(lambda: window.worker is None)
    assert not (window.workspace.root / "stopped.txt").exists()
    assert window.send_button.isEnabled()


def test_gui_http_worker_stop(window, monkeypatch):
    backend = MockBackend([tool_reply("write_file", {"path": "no.txt", "content": "no"})], delay=10)
    monkeypatch.setattr("alienape.ui.create_backend", lambda settings: backend)
    window.input.setPlainText("slow")
    window.send()
    QTest.qWait(60)
    assert window.worker is not None
    window.stop()
    until(lambda: window.worker is None, timeout=1)
    assert "Gestoppt" in window.chat.toPlainText()
    assert not (window.workspace.root / "no.txt").exists()


def test_gui_unavailable_server_stays_usable(window):
    window.settings.model.provider = "ollama"
    window.settings.model.base_url = "http://127.0.0.1:1/v1"
    window.settings.model.timeout = 1
    window.refresh_models()
    until(lambda: window.worker is None)
    assert "Nicht verbunden" in window.connection.text()
    assert window.settings_button.isEnabled()
