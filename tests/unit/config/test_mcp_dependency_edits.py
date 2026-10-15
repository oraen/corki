"""Dependency installs are atomic additions, never configuration replacements."""

import tomllib
from concurrent.futures import ThreadPoolExecutor

import pytest

from corki.config.toml_edits import add_missing_mcp_servers


def test_existing_name_and_unrelated_comments_are_preserved(tmp_path):
    path = tmp_path / "config.toml"
    text = '# retained\n[other]\nvalue=1\n[mcp.servers.same]\ncommand="original"\n'
    path.write_text(text)
    added = add_missing_mcp_servers(
        path,
        {
            "same": {"command": "replacement"},
            "new.with.dots": {"command": "fake"},
        },
    )
    assert added.added == ("new.with.dots",)
    assert {s.name: s.command for s in added.servers} == {
        "same": "original",
        "new.with.dots": "fake",
    }
    contents = path.read_text()
    assert "# retained" in contents
    value = tomllib.loads(contents)
    assert value["other"] == {"value": 1}
    assert value["mcp"]["servers"] == {
        "same": {"command": "original"},
        "new.with.dots": {"command": "fake"},
    }


@pytest.mark.parametrize(
    "contents", ["invalid=[", '[mcp]\nservers="bad"', "[mcp.servers.old]\nurl=3"]
)
def test_invalid_existing_config_is_not_replaced(tmp_path, contents):
    path = tmp_path / "config.toml"
    path.write_text(contents)
    with pytest.raises(ValueError):
        add_missing_mcp_servers(path, {"new": {"command": "fake"}})
    assert path.read_text() == contents


def test_replace_failure_does_not_leave_a_partial_batch(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text("# original\nother=1\n")

    def fail(*args):
        raise PermissionError("fixture")

    monkeypatch.setattr("corki.config.toml_edits.os.replace", fail)
    with pytest.raises(PermissionError):
        add_missing_mcp_servers(path, {"one": {"command": "a"}, "two": {"command": "b"}})
    assert path.read_text() == "# original\nother=1\n"
    assert list(tmp_path.iterdir()) == [path]


def test_two_writers_recheck_existing_names_under_the_same_lock(tmp_path):
    path = tmp_path / "config.toml"
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [
            pool.submit(
                add_missing_mcp_servers,
                path,
                {
                    "shared": {"command": str(n)},
                    str(n): {"command": str(n)},
                },
            )
            for n in range(2)
        ]
        added = [job.result().added for job in jobs]
    assert sum("shared" in names for names in added) == 1
    assert set(tomllib.loads(path.read_text())["mcp"]["servers"]) == {"shared", "0", "1"}


def test_no_additions_returns_existing_snapshot_without_writing(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[mcp.servers.old]\ncommand="original"\nenabled=false\n')

    def no_write(*args):
        raise AssertionError("no additions must not write")

    monkeypatch.setattr("corki.config.toml_edits._write_document", no_write)
    result = add_missing_mcp_servers(path, {"old": {"command": "replacement"}})
    assert result.added == ()
    assert len(result.servers) == 1
    assert result.servers[0].command == "original"
    assert not result.servers[0].enabled


def test_install_snapshot_is_not_reread_after_publication(tmp_path, monkeypatch):
    from corki.config import toml_edits

    path = tmp_path / "config.toml"
    path.write_text('[mcp.servers.old]\ncommand="original"\n')
    write = toml_edits._write_document

    def competing_edit(path, document):
        write(path, document)
        path.write_text('[mcp.servers.other]\ncommand="later-editor"\n')

    monkeypatch.setattr(toml_edits, "_write_document", competing_edit)
    result = add_missing_mcp_servers(path, {"new": {"command": "new-command"}})
    assert result.added == ("new",)
    assert {s.name: s.command for s in result.servers} == {
        "old": "original",
        "new": "new-command",
    }
    assert set(tomllib.loads(path.read_text())["mcp"]["servers"]) == {"other"}
