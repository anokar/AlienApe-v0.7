import os
import threading
import asyncio
import pytest
from alienape.tools import Workspace, ToolRegistry


@pytest.mark.parametrize("path", ["../outside.txt", "a/../../outside.txt", "C:\\Windows\\x", "\\\\server\\share", "x:ads", "NUL.txt", "COM1", "x. ", "/etc/passwd"])
def test_path_safety(tmp_path, path):
    with pytest.raises(PermissionError):
        Workspace(tmp_path).path(path)


def test_read_list_search(tmp_path):
    (tmp_path / "hello.txt").write_text("Hallo Godot 4", encoding="utf-8")
    tools = ToolRegistry(Workspace(tmp_path))
    assert "Godot" in tools.execute("read_file", {"path": "hello.txt"})["content"]
    assert len(tools.execute("list_files", {"path": "."})["entries"]) == 1
    assert tools.execute("search_files", {"path": ".", "query": "godot"})["matches"][0]["line"] == 1
    with pytest.raises(PermissionError):
        tools.execute("read_file", {"path": "../outside"})


def test_hardlink_blocked(tmp_path):
    source = tmp_path / "source"
    source.write_text("secret")
    os.link(source, tmp_path / "link")
    with pytest.raises(PermissionError):
        Workspace(tmp_path).path("link")


def test_link_blocked(tmp_path):
    (tmp_path / "real").mkdir()
    try:
        (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    except OSError:
        pytest.skip("Windows erlaubt diesem Benutzer keine Symlinks")
    with pytest.raises(PermissionError):
        Workspace(tmp_path).path("link/file")


def test_limits_and_cancel(tmp_path):
    (tmp_path / "huge").write_bytes(b"x" * 262145)
    stop = threading.Event()
    tools = ToolRegistry(Workspace(tmp_path), stop)
    with pytest.raises(ValueError):
        tools.execute("read_file", {"path": "huge"})
    stop.set()
    with pytest.raises(asyncio.CancelledError):
        tools.execute("list_files", {"path": "."})
