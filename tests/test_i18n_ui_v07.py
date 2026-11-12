import pytest
from PyQt6.QtWidgets import QApplication,QLineEdit
from alienape.config import Settings
from alienape.dialogs import SettingsDialog,PermissionDialog
from alienape.ui import MainWindow

@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])

@pytest.mark.parametrize('lang',['de','en','fr'])
def test_settings_language_visible_and_persisted(tmp_path,app,lang):
    settings=Settings();settings.ui.language=lang;dialog=SettingsDialog(settings)
    assert dialog.windowTitle()=={
        'de':'Einstellungen · Modelle und Bedienung',
        'en':'Settings · models and interface',
        'fr':'Paramètres · modèles et interface',
    }[lang]
    assert dialog.semantic_widget.enabled.text()=={
        'de':'Semantic Resolver aktivieren',
        'en':'Enable Semantic Resolver',
        'fr':'Activer le résolveur sémantique',
    }[lang]
    dialog.language.setCurrentIndex(dialog.language.findData(lang))
    dialog.save()
    assert dialog.settings.ui.language==lang
    assert dialog.settings.ui.density=='normal'
    dialog.settings.save(tmp_path/'config.toml')
    assert Settings.load(tmp_path/'config.toml').ui.language==lang

@pytest.mark.parametrize('lang,expected_tabs',[
    ('de',['Chat','Memory','Aktivität','Modellerfahrung','Community Cards']),
    ('en',['Chat','Memory','Activity','Model experience','Community Cards']),
    ('fr',['Conversation','Mémoire','Activité','Expérience du modèle','Cartes communautaires']),
])
def test_main_window_shell_is_localized(tmp_path,app,lang,expected_tabs):
    settings=Settings();settings.ui.language=lang
    window=MainWindow(settings,tmp_path)
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())]==expected_tabs
    assert window.files.accessibleName()=={'de':'Projektdateien','en':'Project files','fr':'Fichiers du projet'}[lang]
    assert window.memory_table.horizontalHeaderItem(0).text()=={'de':'Typ','en':'Type','fr':'Type'}[lang]
    assert window.community_cards_panel.local_table.horizontalHeaderItem(0).text()=={'de':'Zeit','en':'Time','fr':'Date'}[lang]
    window.close()

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
