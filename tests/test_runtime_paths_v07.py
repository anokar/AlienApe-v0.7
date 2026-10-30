from pathlib import Path
import sys
from alienape.config import data_directory

def test_explicit_override_beats_legacy(monkeypatch,tmp_path):
    old=tmp_path/'install'/'data';old.mkdir(parents=True)
    selected=tmp_path/'separate-test'
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(old.parent/'AlienApe.exe'))
    monkeypatch.setenv('ALIENAPE_DATA_DIR',str(selected))
    assert data_directory()==selected and not selected.exists()
    assert old.is_dir()

def test_legacy_portable_data_is_reused_without_migration(monkeypatch,tmp_path):
    legacy=tmp_path/'install'/'data';legacy.mkdir(parents=True);marker=legacy/'marker';marker.write_bytes(b'unchanged')
    monkeypatch.setattr(sys,'frozen',True,raising=False);monkeypatch.setattr(sys,'executable',str(legacy.parent/'AlienApe.exe'))
    monkeypatch.delenv('ALIENAPE_DATA_DIR',raising=False);monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    assert data_directory()==legacy and marker.read_bytes()==b'unchanged'
    assert not (tmp_path/'local'/'AlienApe').exists()

def test_no_legacy_uses_user_data_not_source(monkeypatch,tmp_path):
    monkeypatch.setattr(sys,'frozen',False,raising=False);monkeypatch.delenv('ALIENAPE_DATA_DIR',raising=False);monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    assert data_directory()==tmp_path/'local'/'AlienApe'
    assert not data_directory().exists()
