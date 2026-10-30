from copy import deepcopy

from PyQt6.QtWidgets import QDialog, QMessageBox, QPushButton
from alienape.model_dialogs import ModelsDialog
from test_gui import window, app, until
from test_models_gui import multi_window


def test_memory_selection_survives_reordering(window):
    first = window.memory.put('first', 'FACT', 'First')
    window.memory.put('second', 'FACT', 'Second')
    window.refresh_memory()
    row = next(i for i, r in enumerate(window.memory_records) if r['id'] == first['id'])
    window.memory_table.selectRow(row)
    window.memory_buttons[3].click()
    assert window.selected_memory()['id'] == first['id']
    window.memory_buttons[2].click()
    records = {r['id']: r for r in window.memory.all()}
    assert not records[first['id']]['active']


def test_memory_actions_require_selection(window):
    assert window.memory_buttons[0].isEnabled()
    assert all(not b.isEnabled() for b in window.memory_buttons[1:])


def test_profile_rename_preserves_routing(multi_window, monkeypatch):
    settings = deepcopy(multi_window.settings)
    settings.routing.default_model = settings.routing.fallback_model = 'coder'
    dialog = ModelsDialog(settings)
    dialog.table.selectRow(1)

    def edit(editor):
        editor.profile.id = 'renamed'
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr('alienape.model_dialogs.ProfileDialog.exec', edit)
    dialog.edit()
    assert dialog.default.currentData() == 'renamed'
    assert dialog.fallback.currentData() == 'renamed'
    dialog.save()
    assert dialog.settings.routing.default_model == 'renamed'


def test_disabled_manual_profile_returns_to_auto(multi_window):
    dialog = ModelsDialog(multi_window.settings)
    dialog.settings.routing.mode = 'manual'
    dialog.settings.routing.manual_model = 'coder'
    dialog.settings.models[1].enabled = False
    dialog.refresh()
    dialog.save()
    assert dialog.settings.routing.mode == 'auto'
    assert not dialog.settings.routing.manual_model


def test_busy_prompt_cannot_be_overwritten_by_example(window):
    window.input.setPlainText('Mein Entwurf')
    window.set_busy(True)
    button = next(b for b in window.findChildren(QPushButton) if b.text() == 'Memory ausprobieren')
    button.click()
    assert window.input.toPlainText() == 'Mein Entwurf'
    window.set_busy(False)


def test_delete_cancel_refresh_files_and_activity_buttons(window, monkeypatch):
    record = window.memory.put('test', 'FACT', 'Test')
    window.refresh_memory()
    window.memory_table.selectRow(0)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.No)
    window.memory_buttons[4].click()
    assert len(window.memory.all()) == 1
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    window.memory_buttons[4].click()
    assert not window.memory.all()
    (window.workspace.root / 'new.txt').write_text('new')
    next(b for b in window.findChildren(QPushButton) if b.text() == 'Dateien aktualisieren').click()
    assert any(window.files.topLevelItem(i).text(0) == 'new.txt' for i in range(window.files.topLevelItemCount()))
    window.tabs.setCurrentIndex(2)
    window.technical.click()
    window.activity_list.setCurrentRow(0)
    assert window.event_details.isVisible()
    assert 'MEMORY_STORE' in window.event_details.toPlainText()
