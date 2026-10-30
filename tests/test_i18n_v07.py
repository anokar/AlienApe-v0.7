import asyncio
import pytest
from alienape.i18n import localize,effective_locale
from alienape.agent import Agent
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.tools import Workspace
from alienape.intent_resolution import DeterministicIntentResolver,IntentID

@pytest.mark.parametrize('lang,needle',[('de','nicht unterstützt'),('en','not currently supported'),('fr','pas prise en charge')])
def test_delete_is_unsupported_in_all_locales(tmp_path,lang,needle):
    settings=Settings();settings.ui.language=lang
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),settings)
    answer=asyncio.run(a.run('lösche datei.txt'))
    assert needle in answer and a.intent_resolution.intent_id==IntentID.DELETE_REQUEST_UNSUPPORTED
    assert a.resources.counters.model_calls==0

def test_fallback():
    assert effective_locale('fr-CA')=='fr' and effective_locale('xx')=='en'
    assert localize('NOT_A_REAL_KEY','fr')=='Message unavailable.'

@pytest.mark.parametrize('lang',['de','en','fr'])
def test_ui_language_does_not_change_intent(tmp_path,lang):
    settings=Settings();settings.ui.language=lang
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),settings)
    asyncio.run(a.run('lösche index.html'))
    assert a.intent_resolution.intent_id==IntentID.DELETE_REQUEST_UNSUPPORTED
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize('phrase', ['Was kannst du mit Dateien machen?','What can you do with files?','Que peux-tu faire avec les fichiers ?','Kannst du Dateien löschen?','Can you delete files?','Peux-tu supprimer des fichiers ?'])
def test_capability_input_matrix(phrase):
    assert DeterministicIntentResolver().resolve(phrase).intent_id==IntentID.CAPABILITY_QUERY

@pytest.mark.parametrize('phrase', ['Erstelle einen Ordner namens test.','Create a folder named test.','Crée un dossier nommé test.'])
def test_named_directory_input_matrix(phrase):
    resolution=DeterministicIntentResolver().resolve(phrase)
    assert resolution.intent_id==IntentID.CREATE_DIRECTORY and resolution.referenced_paths==('test',)

def test_language_settings_persist(tmp_path):
    settings=Settings();settings.ui.language='fr';settings.save(tmp_path/'config.toml')
    assert Settings.load(tmp_path/'config.toml').ui.language=='fr'

@pytest.mark.parametrize('lang',['de','en','fr'])
def test_sensitive_memory_remains_blocked(tmp_path,lang):
    settings=Settings();settings.ui.language=lang
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),settings)
    raw='FAKE_SECRET_NOT_REAL_73819275'
    answer=asyncio.run(a.run('Merke dir: api key '+raw))
    assert raw not in answer and a.resources.counters.model_calls==0
