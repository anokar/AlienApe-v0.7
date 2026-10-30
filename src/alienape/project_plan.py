"""Controller-owned, bounded project plans. Validation has no filesystem effects."""
from dataclasses import dataclass
from pathlib import PurePosixPath
import json
import re

from .security import SensitiveFilePolicy

MAX_FILES = 12
MAX_DIRECTORIES = 10
MAX_MUTATIONS = 20
MAX_PATH_DEPTH = 6
MAX_PLAN_BYTES = 32768


class InvalidProjectPlan(ValueError):
    pass


@dataclass(frozen=True)
class ProjectFile:
    path: str
    purpose: str
    language: str
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class ProjectPlan:
    root: str
    goal: str
    files: tuple[ProjectFile, ...]
    directories: tuple[str, ...]
    execution_order: tuple[str, ...]


def validate_plan(raw, workspace, root, goal):
    """Validate an advisory model proposal against a separately selected root.

    The proposal has no control over root or permissions. All file and directory
    paths are checked through the existing Workspace boundary before execution.
    """
    if not isinstance(root, str) or not root or root in ('.', '/'):
        raise InvalidProjectPlan('A separately selected project root is required')
    try:
        root_path = workspace.path(root)
    except (ValueError, PermissionError, OSError) as exc:
        raise InvalidProjectPlan('Invalid project root') from exc
    if root_path == workspace.root:
        raise InvalidProjectPlan('Workspace root is not a project root')
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_PLAN_BYTES:
        raise InvalidProjectPlan('Invalid plan size')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidProjectPlan('Duplicate JSON key')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, TypeError) as exc:
        raise InvalidProjectPlan('Invalid JSON') from exc
    if not isinstance(value, dict) or set(value) != {'files'} or not isinstance(value['files'], list):
        raise InvalidProjectPlan('Invalid plan schema')
    if not 1 <= len(value['files']) <= MAX_FILES:
        raise InvalidProjectPlan('File limit exceeded or empty plan')
    files = []
    seen = set()
    for entry in value['files']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'purpose', 'language', 'depends_on'}:
            raise InvalidProjectPlan('Invalid file schema')
        path, purpose, language, deps = (entry[k] for k in ('path', 'purpose', 'language', 'depends_on'))
        if not isinstance(path, str) or not path or '\\' in path:
            raise InvalidProjectPlan('Invalid relative path')
        parts = path.split('/')
        if len(parts) > MAX_PATH_DEPTH or any(p in ('', '.', '..') or re.search(r'[<>:"|?*\x00-\x1f]', p) for p in parts):
            raise InvalidProjectPlan('Invalid path depth or component')
        if any(not isinstance(x, str) or not x or len(x) > 500 for x in (purpose, language)):
            raise InvalidProjectPlan('Invalid metadata')
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps) or len(deps) > MAX_FILES:
            raise InvalidProjectPlan('Invalid dependencies')
        key = path.casefold()
        if key in seen:
            raise InvalidProjectPlan('Duplicate target')
        seen.add(key)
        try:
            full = workspace.path(root + '/' + path)
            if not full.is_relative_to(root_path):
                raise InvalidProjectPlan('Target escapes project root')
            SensitiveFilePolicy.writable(root + '/' + path)
        except (ValueError, PermissionError, OSError) as exc:
            raise InvalidProjectPlan('Unsafe target') from exc
        files.append(ProjectFile(path, purpose, language, tuple(deps)))
    by_name = {f.path: f for f in files}
    directories = {str(p) for f in files for p in PurePosixPath(f.path).parents if str(p) != '.'}
    if len(directories) > MAX_DIRECTORIES or len(directories) + len(files) > MAX_MUTATIONS:
        raise InvalidProjectPlan('Mutation limit exceeded')
    if len({d.casefold() for d in directories}) != len(directories):
        raise InvalidProjectPlan('Ambiguous directory casing')
    if any(d.casefold() in seen for d in directories):
        raise InvalidProjectPlan('File and directory conflict')
    ordered = []
    remaining = list(files)
    while remaining:
        ready = [f for f in remaining if all(d in ordered for d in f.depends_on)]
        if not ready:
            raise InvalidProjectPlan('Missing or cyclic dependency')
        for f in ready:
            ordered.append(f.path)
            remaining.remove(f)
    dirs = tuple(sorted(directories, key=lambda d: (d.count('/'), d)))
    return ProjectPlan(root, goal, tuple(files), dirs, dirs + tuple(ordered))
