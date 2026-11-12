from alienape.discovery import TEXT_EXTENSIONS
from alienape.languages import LANGUAGE_SUFFIXES
from alienape.routing import TaskType, classify_task, referenced_paths
from alienape.security import SensitiveFilePolicy
from alienape.task_slots import language_hint
from alienape.taxonomy import TaskCategory, task_category
from alienape.tools import Workspace


def test_common_languages_are_routed_and_resolved_from_paths():
    examples = {
        'src/Main.java': 'java',
        'src/server.rs': 'rust',
        'web/app.tsx': 'typescript',
        'scripts/tool.rb': 'ruby',
        'src/main.go': 'go',
        'app/main.kt': 'kotlin',
        'scripts/deploy.ps1': 'powershell',
        'src/contract.sol': 'solidity',
    }
    for path, language in examples.items():
        assert language_hint('Create a file', (path,)) == language
        assert '.' + path.rsplit('.', 1)[-1] in TEXT_EXTENSIONS
        assert classify_task(f'Fix a bug in {path}')[0] == TaskType.CODE_TASK


def test_language_paths_can_be_discovered_and_scoped():
    assert referenced_paths('Please review "src/Main.java" and src/server.rs') == ['src/Main.java', 'src/server.rs']
    assert '.rb' in LANGUAGE_SUFFIXES and '.rb' in TEXT_EXTENSIONS
    assert not SensitiveFilePolicy.sensitive('secrets.rb')
    assert SensitiveFilePolicy.sensitive('secrets.txt')


def test_generic_language_requests_use_code_generation_category():
    assert task_category('Create a Rust implementation') == TaskCategory.CODE_GENERATION
import asyncio

from alienape.agent import Agent
from alienape.backends import MockBackend, tool_reply
from alienape.config import Settings


def test_non_python_source_file_can_be_created_with_scoped_permission(tmp_path):
    backend = MockBackend([
        tool_reply('write_file', {'path': 'hello.rb', 'content': 'puts "Hello World"\n'}),
        {'role': 'assistant', 'content': 'Created hello.rb.'},
    ], delay=0)

    async def allow(*_):
        return True

    agent = Agent(backend, Workspace(tmp_path), Settings(), permission=allow)
    asyncio.run(agent.run('Create hello.rb with a Hello World program'))
    assert (tmp_path / 'hello.rb').read_text(encoding='utf-8') == 'puts "Hello World"\n'
    assert agent.write_scope.allowed_paths == ['hello.rb']
