from pathlib import Path

from app.config import Settings


def test_data_dir_defaults_to_slash_data(monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert Settings.from_env().data_dir == Path("/data")


def test_data_dir_comes_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert Settings.from_env().data_dir == tmp_path
