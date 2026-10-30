"""UI-only integration checks, existing MockBackend and isolated stores."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib,json,uuid
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog,QPushButton,QScrollArea
from alienape.dialogs import SettingsDialog,PermissionDialog
from alienape.model_dialogs import ModelsDialog
from alienape.profiles import ModelProfile
from alienape.security import redactor
from test_gui import window,app,until,send

def event(kind,details):return {'timestamp':datetime.now(timezone.utc).isoformat(),'event_type':kind,'details':details,'resources':{}}

def test_chat_read_and_list_actions_unchanged(window):
    send(window,'Lies "hello.txt"');assert 'GUI-End-to-End' in window.chat.toPlainText()
    send(window,'Liste die Dateien auf');assert 'hello.txt' in window.chat.toPlainText()
    assert window.input.isReadOnly() is False

def test_project_open_action_and_path_visible(window,monkeypatch,tmp_path):
    target=tmp_path/'other';target.mkdir();(target/'a.py').write_text('x=1\n')
    monkeypatch.setattr('alienape.ui.QFileDialog.getExistingDirectory',lambda *_:str(target))
    window.project_button.click()
    assert window.workspace.root==target and window.project_path.text()==str(target)

def test_model_management_action_and_auto(window,monkeypatch):
    seen=[]
    monkeypatch.setattr(ModelsDialog,'exec',lambda self:seen.append(self) or QDialog.DialogCode.Rejected)
    window.models_button.click();assert seen
    window.settings.models=[ModelProfile('mock','Demo','mock','mock-demo')]
    window.refresh_models();until(lambda:window.worker is None)
    assert window.model.itemText(0)=='AUTO' and window.model.isVisible()

def test_stop_and_keyboard_navigation(window):
    window.set_busy(True);assert window.stop_button.isEnabled() and window.stop_button.focusPolicy()!=Qt.FocusPolicy.NoFocus
    window.stop_button.setFocus();QTest.keyClick(window.stop_button,Qt.Key.Key_Tab)
    assert window.focusWidget() is not None
    window.set_busy(False)

def test_permission_dialog_preserves_decisions(window):
    request={'name':'write_file','path':str(window.workspace.root/'out.txt'),'arguments':{'content':'safe'}}
    dialog=PermissionDialog(request,window)
    try:
        dialog.show();assert dialog.decision=='deny'
        next(b for b in dialog.findChildren(QPushButton) if b.text()=='Einmal erlauben').click()
        assert dialog.decision=='once'
    finally:dialog.close()

@pytest.mark.parametrize('status',['PASS','FAIL','UNKNOWN','NOT_RUN'])
def test_verification_text_is_exact_existing_state(window,status):
    before=deepcopy(window.settings)
    window.on_event(event('VERIFICATION_COMPLETED',{'verification_status':status,'message':'Bounded syntax check only'}))
    assert window.verification_label.text()=='Verification: '+status+'\nBounded syntax check only'
    assert window.settings==before and status in window.verification_label.toolTip()

def test_error_card_distinct_and_escaped(window):
    window.on_failure('FORMAT_FAILURE: expected file proposal <unsafe>')
    assert 'FORMAT_FAILURE' in window.chat.toPlainText()
    assert '<unsafe>' in window.chat.toPlainText() and window.ui_colors['error'][1:] in window.chat.toHtml()
    assert window.status.text().startswith('Fehler:')

def test_long_answer_scrolls(window):
    window.append_chat('AlienApe','Long answer\n'*200)
    until(lambda:window.chat.verticalScrollBar().maximum()>0)
    assert window.chat.verticalScrollBar().maximum()>window.chat.height()

@pytest.mark.parametrize('scale',[1.25,1.5,2.0])
def test_resize_and_font_scaling_keep_actions(window,scale):
    window.settings.ui.font_scale=scale;window.apply_style()
    for width,height in [(920,740),(1500,1000)]:
        window.resize(width,height);QTest.qWait(20)
        for control in [window.send_button,window.stop_button,window.project_button,window.model]:
            assert control.isVisible() and window.rect().contains(control.mapTo(window,control.rect().center()))

def test_details_available_without_new_state(window):
    assert not window.details_area.isVisible()
    original=window.connection.text();window.details_toggle.click()
    assert window.details_area.isVisible() and window.connection.text()==original
    window.details_toggle.click();assert not window.details_area.isVisible()

def test_settings_scrollable_and_buttons_accessible(window):
    dialog=SettingsDialog(window.settings,window)
    try:
        dialog.show();assert dialog.findChildren(QScrollArea)
        assert all(b.accessibleName() or b.text() for b in dialog.findChildren(QPushButton))
    finally:dialog.close()

def test_existing_redactor_still_used_for_chat(window):
    value='FAKE_UI_'+uuid.uuid4().hex;redactor.register(value)
    window.append_chat('AlienApe','Diagnostic '+value)
    assert value not in window.chat.toPlainText() and '<REDACTED>' in window.chat.toPlainText()

def test_presentation_does_not_modify_stores_or_core_settings(window):
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in [window.memory.database,window.experience.database]}
    settings=deepcopy(window.settings);history=deepcopy(window.session.history)
    window.details_toggle.click();window.resize(1100,850)
    window.append_chat('AlienApe','Presentation fixture')
    window.on_event(event('VERIFICATION_COMPLETED',{'verification_status':'UNKNOWN','message':'UI fixture'}))
    assert window.settings==settings and window.session.history==history
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in hashes.items())
