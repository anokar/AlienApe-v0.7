"""Bounded filesystem tools; all paths pass the same workspace boundary."""
from dataclasses import dataclass
from pathlib import Path
import asyncio
import json
import os
import re
import tempfile
import threading
from itertools import islice
from copy import deepcopy
from .security import redactor, transport_redactor, SensitiveFilePolicy
from jsonschema import validate
from jsonschema import ValidationError


class ToolArgumentInvalid(ValueError):
    """Invalid argument shape/type, before filesystem execution."""
    subreason='TOOL_ARGUMENT_INVALID'


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Projektordner existiert nicht.")

    def path(self, value):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise PermissionError("Ungültiger Dateipfad.")
        if self.root.resolve()!=self.root or getattr(self.root.lstat(),"st_file_attributes",0)&0x400:
            raise PermissionError("Workspace-Wurzel wurde durch eine Verknüpfung ersetzt.")
        # Reject Windows device names, ADS, drive-relative and UNC spellings too.
        normal = value.replace("\\", "/")
        if ":" in normal or "%" in normal or normal.startswith("/"):
            raise PermissionError("Nur relative Pfade innerhalb des Projektordners erlaubt.")
        parts = normal.split("/")
        if ".." in parts:
            raise PermissionError("Übergeordnete Pfade (..) sind gesperrt.")
        for part in parts:
            if part not in {"", ".", ".."}:
                if part.endswith((" ", ".")) or re.match(r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)", part, re.I):
                    raise PermissionError("Windows-Gerätepfad ist nicht erlaubt.")
        candidate = self.root.joinpath(*parts)
        # Disallow reparse points even when a link happens to point inside root.
        current = self.root
        for part in parts:
            current = current / part
            if current.exists() or current.is_symlink():
                stat = current.lstat()
                if current.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                    raise PermissionError("Verknüpfungen/Junctions werden nicht geöffnet.")
                if current.is_file() and stat.st_nlink > 1:
                    raise PermissionError("Hardlinks werden nicht geöffnet.")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise PermissionError("Zugriff außerhalb des Projektordners gesperrt.")
        return resolved


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    risk_level: str

    def api_schema(self):
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.schema}}


def schema(properties, required):
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


PATH = {"type": "string", "minLength": 1, "maxLength": 1024}


class ToolRegistry:
    def __init__(self, workspace, stopped=None, observe=None):
        self.workspace = workspace
        self.stopped = stopped or threading.Event()
        self.observe = observe or (lambda *args: None)
        self.sensitive_read_path = None
        self.tools = {t.name: t for t in [
            Tool("read_file", "UTF-8-Datei innerhalb des Workspace lesen (maximal 256 KB).", schema({"path": PATH,"max_bytes":{"type":"integer","minimum":1,"maximum":262144}}, ["path"]), "read"),
            Tool("list_files", "Ordner im Workspace auflisten, begrenzt auf 500 Einträge.", schema({"path": PATH,"limit":{"type":"integer","minimum":1,"maximum":500}}, ["path"]), "read"),
            Tool("search_files", "Text in UTF-8-Dateien eines directory suchen; maximal 1000 Dateien, 100 Treffer.", {**schema({"directory": PATH,"path": PATH, "query": {"type": "string", "minLength": 1, "maxLength": 500}}, ["query"]),"oneOf":[{"required":["directory"]},{"required":["path"]}]}, "read"),
            Tool("write_file", "UTF-8-Datei erstellen/ersetzen. Benötigt Freigabe.", schema({"path": PATH, "content": {"type": "string", "maxLength": 262144}}, ["path", "content"]), "write"),
            Tool("make_directory", "Ordner im Workspace erstellen. Benötigt Freigabe.", schema({"path": PATH}, ["path"]), "write"),
        ]}

    def schemas(self,names=None,write_scope=None):
        schemas=[deepcopy(t.api_schema()) for t in self.tools.values() if names is None or t.name in names]
        for item in schemas:
            if item['function']['name']=='search_files':
                params=item['function']['parameters']
                params['properties'].pop('path',None)
                params.pop('oneOf',None)
                params['required']=['directory','query']
            if write_scope and item['function']['name']=='write_file' and write_scope.allowed_paths:
                item['function']['parameters']['properties']['path']['enum']=list(write_scope.allowed_paths)
        return schemas

    def check_stop(self):
        if self.stopped.is_set():
            raise asyncio.CancelledError()

    def validate(self, name, arguments):
        if name not in self.tools:
            raise ValueError(f"Unbekanntes Werkzeug: {name}")
        try:validate(arguments, self.tools[name].schema)
        except ValidationError as exc:raise ToolArgumentInvalid(f'{name}: ungültige Argumente ({exc.validator})') from exc
        path=self.workspace.path(arguments.get('directory',arguments.get('path')))
        relative=path.relative_to(self.workspace.root).as_posix()
        if name in {"write_file","make_directory"}:SensitiveFilePolicy.writable(relative)
        if name in {'list_files','search_files'} and SensitiveFilePolicy.sensitive(relative):
            raise PermissionError('SECRET_POLICY_BLOCK: sensible Verzeichnisse werden nicht durchsucht.')
        if name=="read_file" and SensitiveFilePolicy.sensitive(relative) and relative!=self.sensitive_read_path:
            raise PermissionError("SECRET_POLICY_BLOCK: sensibler Dateiinhalt ist kein generisches Modelltool.")
        if name in {'list_files','search_files'} and path.exists() and not path.is_dir():
            raise ToolArgumentInvalid(f'{name}.directory must be a directory')
        if name in {'read_file','write_file'} and path.exists() and not path.is_file():
            raise ToolArgumentInvalid(f'{name}.path must be a file')
        return path

    def execute(self, name, arguments):
        self.check_stop()
        path = self.validate(name, arguments)
        if name == "read_file":
            maximum=arguments.get('max_bytes',262144)
            path=self.validate(name,arguments)
            with path.open("rb") as stream:
                data = stream.read(maximum+1)
            if len(data) > maximum:
                raise ValueError(f"Datei überschreitet Leselimit von {maximum} Bytes.")
            text = data.decode("utf-8-sig")
            text = redactor.sensitive_read(text) if SensitiveFilePolicy.sensitive(arguments["path"]) else transport_redactor.text(text)
            self.observe("FILE_READ", {"path": arguments["path"], "bytes": len(data)})
            return {"path": arguments["path"], "content": text}
        if name == "write_file":
            data = arguments["content"].encode("utf-8")
            if len(data) > 262144:
                raise ValueError("Schreiblimit: 256 KB.")
            # Temp + replace avoids partial writes and modifying a linked inode.
            fd, temp = tempfile.mkstemp(prefix=".alienape-", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.check_stop()
                self.validate(name, arguments)
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            self.observe("FILE_WRITE", {"path": arguments["path"], "bytes": len(data)})
            return {"path": arguments["path"], "written_bytes": len(data)}
        if name == "make_directory":
            self.check_stop()
            path=self.validate(name,arguments)
            path.mkdir(parents=True, exist_ok=True)
            return {"directory": arguments["path"]}
        if name == "list_files":
            entries = [];scanned=0;maximum=arguments.get('limit',500)
            with os.scandir(path) as scan:
                for entry in islice(scan,maximum):
                    self.check_stop()
                    scanned+=1
                    try:
                        safe = self.workspace.path(str(Path(entry.path).relative_to(self.workspace.root)))
                        entries.append({"path": str(safe.relative_to(self.workspace.root)), "directory": safe.is_dir()})
                    except (PermissionError, OSError):
                        continue
            return {"entries": sorted(entries, key=lambda e: e["path"]), "truncated": scanned>=maximum,"scanned_entries":scanned}
        if name == "search_files":
            hits, stack, visited = [], [path], 0
            while stack and visited < 1000:
                self.check_stop()
                directory = stack.pop()
                directory=self.workspace.path(directory.relative_to(self.workspace.root).as_posix())
                with os.scandir(directory) as scan:
                    for entry in scan:
                        self.check_stop()
                        visited += 1
                        if visited > 1000:
                            break
                        try:
                            safe = self.workspace.path(str(Path(entry.path).relative_to(self.workspace.root)))
                            if SensitiveFilePolicy.sensitive(safe.relative_to(self.workspace.root).as_posix()):continue
                            if safe.is_dir():
                                stack.append(safe)
                                continue
                            if safe.stat().st_size > 262144:
                                continue
                            safe=self.workspace.path(safe.relative_to(self.workspace.root).as_posix())
                            with safe.open("rb") as stream:
                                data = stream.read(262145)
                            if len(data) > 262144:
                                continue
                            self.observe("FILE_READ", {"path": str(safe.relative_to(self.workspace.root)), "bytes": len(data)})
                            for line, text in enumerate(data.decode("utf-8-sig").splitlines(), 1):
                                if arguments["query"].casefold() in text.casefold():
                                    hits.append({"path": str(safe.relative_to(self.workspace.root)), "line": line, "text": transport_redactor.text(text[:500])})
                                    if len(hits) >= 100:
                                        return {"matches": hits, "truncated": True}
                        except (UnicodeError, OSError):
                            continue
            return {"matches": hits, "truncated": bool(stack) or visited >= 1000}
        raise ValueError("Werkzeug nicht implementiert.")
