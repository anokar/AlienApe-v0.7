import asyncio
import threading
import pytest
from alienape.agent import Agent
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.file_actions import direct_read_path
from alienape.profiles import ModelProfile
from alienape.routed_agent import RoutedAgent
from alienape.routing import ModelRegistry
from alienape.tools import Workspace


class NoModel(MockBackend):
    async def chat(self, *args, **kwargs):
        raise AssertionError("Dateianzeige darf nicht vom Modell umformuliert werden")


class NoPool:
    def get(self, *args):
        raise AssertionError("Reine Dateianzeige benötigt keine Modellverbindung")


@pytest.mark.parametrize("command,path", [
    ('Lies "important_dates.txt"', 'important_dates.txt'),
    ('Bitte lese die Datei "andere daten.md".', 'andere daten.md'),
    ('Öffne README', 'README'),
    ('Zeige mir den Inhalt von `scripts/player.py`', 'scripts/player.py'),
    ('Was steht in „notiz.txt“?', 'notiz.txt'),
    ('Read "notes.txt"', 'notes.txt'),
    ('Show the contents of "notes.txt"', 'notes.txt'),
    ('Lies other.txt!', 'other.txt'),
])
def test_general_file_view_commands(command, path):
    assert direct_read_path(command) == path


@pytest.mark.parametrize("command", [
    'Lies "file.txt" und fasse den Inhalt zusammen.',
    'Lies "player.py" und finde den Fehler.',
    'Schreibe "read me" in "file.txt"',
    'Was hast du dir alles gemerkt?',
])
def test_analysis_and_other_instructions_are_not_swallowed(command):
    assert direct_read_path(command) is None


@pytest.mark.parametrize("routed", [False, True])
def test_file_view_preserves_exact_text_without_any_model(tmp_path, routed):
    content = 'Termine am 12.12 sind wichtig.\nZweite Zeile: äöü 🙂\n\tEinrückung\n'
    (tmp_path / 'important_dates.txt').write_bytes(content.encode('utf-8'))
    settings = Settings()
    if routed:
        settings.models = [ModelProfile('coder', 'Offline Coder', 'mock', 'mock-demo', enabled=False)]
        agent = RoutedAgent(Workspace(tmp_path), settings, registry=ModelRegistry(settings.models), pool=NoPool())
    else:
        agent = Agent(NoModel(), Workspace(tmp_path), settings)
    result = asyncio.run(agent.run('Lies "important_dates.txt"'))
    assert result == 'Inhalt von „important_dates.txt“:\n\n' + content
    assert agent.resources.counters.model_calls == 0
    assert agent.resources.counters.file_reads == agent.resources.counters.tool_calls == 1
    assert agent.resources.counters.bytes_read == len(content.encode('utf-8'))
    assert agent.resources.counters.file_writes == 0


def test_empty_missing_outside_and_cancel_keep_existing_boundaries(tmp_path):
    (tmp_path / 'empty.txt').write_text('')
    agent = Agent(NoModel(), Workspace(tmp_path), Settings())
    assert 'ist leer' in asyncio.run(agent.run('Lies "empty.txt"'))
    with pytest.raises(FileNotFoundError):
        asyncio.run(agent.run('Lies "missing.txt"'))
    with pytest.raises(PermissionError):
        asyncio.run(agent.run('Lies "../outside.txt"'))
    stop = threading.Event()
    stop.set()
    agent = Agent(NoModel(), Workspace(tmp_path), Settings(), stopped=stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run('Lies "empty.txt"'))
