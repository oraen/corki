import os
from pathlib import Path

from corki.config import CorkiPaths
from corki.config.document import loads


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
    assert paths.config_file.name == "settings.json"
    assert paths.config_file.read_text(encoding="utf-8").startswith("// Corki settings")
    if os.name == "nt":
        from corki.platforms.windows_acl import WindowsPrivateACL
        from corki.platforms.windows_file_handles import WindowsFiles

        files = WindowsFiles()
        handle = files.open(paths.config_file, access=0x20000)
        try:
            WindowsPrivateACL().verify(handle)
        finally:
            files.close(handle)
    else:
        assert paths.config_file.stat().st_mode & 0o777 == 0o600


def test_discover_honors_corki_home(monkeypatch, tmp_path: Path) -> None:
    custom_home = tmp_path / "isolated-home"
    monkeypatch.setenv("CORKI_HOME", str(custom_home))

    assert CorkiPaths.discover().home == custom_home


def test_generated_configuration_does_not_advertise_excluded_protocols(tmp_path):
    paths = CorkiPaths.from_home(tmp_path / "new-home")
    paths.ensure_exists()
    content = paths.config_file.read_text(encoding="utf-8")
    for retired in (
        "namespace_mode",
        "supports_encrypted_tool_output",
        "native when eligible",
        "responses/compact",
        "tool_search_output",
        "codex_backend",
        "use_responses_lite",
    ):
        assert retired not in content
    document = loads(content)
    assert document["providers"]["default"]["base_url"] == ""
    assert document["providers"]["default"]["api_mode"] == "chat_completions"
    assert "max_steps" not in paths.config_file.read_text(encoding="utf-8")
    assert "max_tool_calls" not in paths.config_file.read_text(encoding="utf-8")
