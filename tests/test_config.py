import pytest
from alienape.config import Settings


def test_config_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    settings = Settings.load(path)
    settings.ui.font_scale = 1.4
    settings.model.provider = "ollama"
    settings.save(path)
    assert Settings.load(path) == settings
    assert "api_key" not in path.read_text()


def test_invalid_config(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('[agent]\nmax_steps = 0\n')
    with pytest.raises(ValueError):
        Settings.load(path)
