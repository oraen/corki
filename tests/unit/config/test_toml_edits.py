"""Config amendments retain TOML content and publish at an exact filesystem target."""

import concurrent.futures
import os
import tomllib

import pytest

from corki.config.toml_edits import set_config_value


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("tool", ["write", "calendar/list_events", 'dots.and"quotes'])
def test_literal_keys_and_comments_survive_existing_and_inline_tables(tmp_path, inline, tool):
    path = tmp_path / "config.toml"
    prefix = '# unrelated comment\n[other]\nvalue="retain" # same line\n'
    path.write_text(
        prefix
        + ("[apps]\nmail = {enabled = false}\n" if inline else "[apps.mail]\nenabled=false\n")
    )
    set_config_value(path, ("apps", "mail", "tools", tool, "approval_mode"), "approve")
    text = path.read_text()
    parsed = tomllib.loads(text)
    assert prefix in text
    assert parsed == {
        "other": {"value": "retain"},
        "apps": {"mail": {"enabled": False, "tools": {tool: {"approval_mode": "approve"}}}},
    }
    assert not list(tmp_path.glob(".corki-config-*"))
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("contents", ["invalid = [", '[apps]\nmail="not a table"\n'])
def test_invalid_existing_document_is_not_overwritten(tmp_path, contents):
    path = tmp_path / "config.toml"
    path.write_text(contents)
    with pytest.raises(ValueError):
        set_config_value(path, ("apps", "mail", "tools", "write", "approval_mode"), "approve")
    assert path.read_text() == contents
    assert not list(tmp_path.glob(".corki-config-*"))


@pytest.mark.parametrize("exists", [False, True])
def test_relative_symlink_chain_updates_target_without_replacing_links(tmp_path, exists):
    target = tmp_path / "nested/actual.toml"
    if exists:
        target.parent.mkdir()
        target.write_text('other="keep"\n')
    link, middle = tmp_path / "config.toml", tmp_path / "middle.toml"
    try:
        link.symlink_to(middle.name)
        middle.symlink_to("nested/actual.toml")
    except OSError:
        pytest.skip("symlinks unavailable")
    set_config_value(link, ("apps", "mail", "tools", "write", "approval_mode"), "approve")
    assert link.is_symlink() and middle.is_symlink()
    parsed = tomllib.loads(target.read_text())
    assert parsed["apps"]["mail"]["tools"]["write"]["approval_mode"] == "approve"
    assert parsed.get("other") == ("keep" if exists else None)


def test_replace_failure_preserves_original_and_removes_only_owned_temporary(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('# existing\nvalue="old"\n')

    def fail(*args):
        raise PermissionError("fixture failure")

    monkeypatch.setattr("corki.config.toml_edits.os.replace", fail)
    with pytest.raises(PermissionError):
        set_config_value(path, ("value",), "new")
    assert path.read_text() == '# existing\nvalue="old"\n'
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_runtime_writes_do_not_drop_each_others_keys(tmp_path):
    path = tmp_path / "config.toml"
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(set_config_value, path, ("tools", str(n), "approval_mode"), "approve")
            for n in range(12)
        ]
        for future in futures:
            future.result()
    assert tomllib.loads(path.read_text()) == {
        "tools": {str(n): {"approval_mode": "approve"} for n in range(12)}
    }
