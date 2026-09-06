from pathlib import Path

from corki.config import CorkiPaths


def test_ensure_exists_creates_expected_private_layout(tmp_path: Path) -> None:
    paths = CorkiPaths.from_home(tmp_path / ".corki")

    paths.ensure_exists()

    assert paths.config_file.is_file()
    assert paths.history_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.sessions_dir.is_dir()
    assert paths.skills_dir.is_dir()
    assert paths.plugins_dir.is_dir()
    assert paths.memories_dir.is_dir()
    assert paths.config_file.read_text(encoding="utf-8").startswith("# Corki configuration")
    assert paths.config_file.stat().st_mode & 0o777 == 0o600


def test_discover_honors_corki_home(monkeypatch, tmp_path: Path) -> None:
    custom_home = tmp_path / "isolated-home"
    monkeypatch.setenv("CORKI_HOME", str(custom_home))

    assert CorkiPaths.discover().home == custom_home
