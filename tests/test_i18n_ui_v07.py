import pytest
from PyQt6.QtWidgets import QApplication,QLineEdit
from alienape.config import Settings
from alienape.dialogs import SettingsDialog,PermissionDialog

@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])

@pytest.mark.parametrize('lang',['de','en','fr'])
def test_settings_language_visible_and_persisted(tmp_path,app,lang):
    settings=Settings();dialog=SettingsDialog(settings)
    dialog.language.setCurrentIndex(dialog.language.findData(lang))
    dialog.save()
    assert dialog.settings.ui.language==lang
    dialog.settings.save(tmp_path/'config.toml')
    assert Settings.load(tmp_path/'config.toml').ui.language==lang

@pytest.mark.parametrize('lang',['de','en','fr'])
def test_permission_target_remains_exact(app,lang):
    class Parent:
        settings=Settings()
    parent=Parent();parent.settings.ui.language=lang
    # QWidget parent carries the same settings as MainWindow.
    from PyQt6.QtWidgets import QWidget
    window=QWidget();window.settings=parent.settings
    target='dossier/ä_file.txt'
    dialog=PermissionDialog({'name':'write_file','path':target,'arguments':{'content':'demo'}},window)
    assert any(w.text()==target for w in dialog.findChildren(QLineEdit))
