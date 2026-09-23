import base64
import json
import os
import subprocess

import pytest

from corki.memory import git_baseline as git
from corki.memory import workspace
from corki.memory.artifacts import write_baseline, write_consolidated_artifacts
from corki.memory.models import ConsolidatedMemory


def test_reset_tracks_hidden_binary_modes_links_and_ignores_nonregular(tmp_path):
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden/data").write_bytes(b"\0\xff\n")
    executable = tmp_path / "exec"
    executable.write_bytes(b"run")
    executable.chmod(0o755)
    (tmp_path / "link").symlink_to("absent")
    nested = tmp_path / "nested/.git"
    nested.mkdir(parents=True)
    (nested / "excluded").write_text("not tracked")
    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / "pipe")
    before = git.capture(tmp_path)
    git.reset(tmp_path)
    assert git.read(tmp_path) == before
    assert set(before) == {".hidden/data", "exec", "link"}
    assert before["exec"]["mode"] == "100755"
    assert before["link"]["mode"] == "120000"
    assert (tmp_path / "link").is_symlink()  # baseline primitive, not workspace validation
    assert (tmp_path / ".git/index").is_file()
    assert git._git(tmp_path, "status", "--porcelain", "--untracked-files=no") == b""


def test_diff_is_read_only_and_reset_removes_deleted_objects_and_prompt(tmp_path):
    note = tmp_path / "forgotten.md"
    note.write_text("FORGET_THIS_BLOB")
    git.prepare(tmp_path)
    old = git._git(tmp_path, "rev-parse", "HEAD:forgotten.md").strip().decode()
    commit = git._git(tmp_path, "rev-parse", "HEAD").strip()
    objects = set((tmp_path / ".git/objects").rglob("*"))
    note.unlink()
    (tmp_path / "new.md").write_text("new")
    diff = workspace.render_diff(git.read(tmp_path), git.capture(tmp_path))
    assert "- D forgotten.md" in diff and "-FORGET_THIS_BLOB" in diff
    assert set((tmp_path / ".git/objects").rglob("*")) == objects
    git.prepare(tmp_path)
    assert git._git(tmp_path, "rev-parse", "HEAD").strip() == commit
    (tmp_path / "phase2_workspace_diff.md").write_text(diff)
    git.reset(tmp_path)
    assert not (tmp_path / "phase2_workspace_diff.md").exists()
    assert "forgotten.md" not in git.read(tmp_path)
    assert git._git(tmp_path, "rev-list", "--count", "HEAD").strip() == b"1"
    with pytest.raises(RuntimeError):
        git._git(tmp_path, "cat-file", "-p", old)
    assert not (tmp_path / ".git/objects" / old[:2] / old[2:]).exists()


@pytest.mark.parametrize("version", [1, 2, 3])
def test_legacy_migration_retains_pending_content_evidence(tmp_path, version):
    write_consolidated_artifacts(tmp_path, ConsolidatedMemory("old", "old"))
    note = tmp_path / "input.md"
    note.write_text("OLD INPUT\n")
    write_baseline(tmp_path, workspace.digest(workspace.capture(tmp_path), outputs=False))
    if version != 3:
        (tmp_path / ".consolidation-baseline.json").write_text(json.dumps({"version": version}))
    note.write_text("NEW INPUT\n")
    (tmp_path / ".hidden").write_text("previously ignored")
    git.prepare(tmp_path)
    assert not (tmp_path / ".consolidation-baseline.json").exists()
    before, after = git.read(tmp_path), git.capture(tmp_path)
    assert not git.matches(tmp_path, after)
    diff = workspace.render_diff(before, after)
    assert "NEW INPUT" in diff and ".hidden" in diff
    if version == 3:
        assert "-OLD INPUT" in diff and "- M input.md" in diff
    else:
        assert before == {}


@pytest.mark.parametrize("broken", ["HEAD", "objects"])
def test_owned_unusable_metadata_is_rebuilt(tmp_path, broken):
    (tmp_path / "note").write_text("keep")
    git.prepare(tmp_path)
    if broken == "HEAD":
        (tmp_path / ".git/HEAD").write_text("invalid")
    else:
        oid = git._git(tmp_path, "rev-parse", "HEAD^{tree}").strip().decode()
        (tmp_path / ".git/objects" / oid[:2] / oid[2:]).unlink()
    git.prepare(tmp_path)
    assert git.read(tmp_path) == git.capture(tmp_path)
    assert (tmp_path / ".git/index").is_file()


def test_unrelated_git_repository_is_never_deleted(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=10)
    (tmp_path / "user-file").write_text("keep")
    original = (tmp_path / ".git/HEAD").read_bytes()
    for function in (git.prepare, git.reset, git.ensure_layout):
        with pytest.raises(ValueError, match="unowned"):
            function(tmp_path)
    assert (tmp_path / ".git/HEAD").read_bytes() == original
    assert (tmp_path / "user-file").read_text() == "keep"


@pytest.mark.parametrize(
    "author,author_email,committer,committer_email",
    [
        ("Codex", "noreply@openai.com", "Codex", "noreply@openai.com"),
        ("Unrelated", "noreply@openai.com", "Codex", "noreply@openai.com"),
        ("Codex", "other@example.invalid", "Codex", "noreply@openai.com"),
        ("Codex", "noreply@openai.com", "Unrelated", "noreply@openai.com"),
        ("Codex", "noreply@openai.com", "Codex", "other@example.invalid"),
    ],
)
def test_native_legacy_baseline_requires_actual_codex_identity(
    tmp_path, author, author_email, committer, committer_email
):
    message = "Initialize Codex git baseline\n\nCo-authored-by: Codex <noreply@openai.com>"
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME=author,
        GIT_AUTHOR_EMAIL=author_email,
        GIT_COMMITTER_NAME=committer,
        GIT_COMMITTER_EMAIL=committer_email,
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], env=env, check=True, timeout=10)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            f"core.hooksPath={os.devnull}",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            message,
        ],
        env=env,
        check=True,
        timeout=10,
    )
    original = git._git(tmp_path, "rev-parse", "HEAD").strip()
    if (author, author_email, committer, committer_email) != (
        "Codex",
        "noreply@openai.com",
        "Codex",
        "noreply@openai.com",
    ):
        with pytest.raises(ValueError, match="unowned"):
            git.reset(tmp_path)
        assert git._git(tmp_path, "rev-parse", "HEAD").strip() == original
        assert not (tmp_path / ".git/corki-memory-baseline").exists()
        return

    git.prepare(tmp_path)
    assert git._git(tmp_path, "rev-parse", "HEAD").strip() == original
    git.reset(tmp_path)
    assert git._git(tmp_path, "log", "-1", "--format=%an <%ae> | %cn <%ce>").strip() == (
        b"Corki <noreply@corki.local> | Corki <noreply@corki.local>"
    )
    assert git._git(tmp_path, "log", "-1", "--format=%B").strip() == (
        b"Initialize Corki git baseline"
    )


def test_ambient_git_routing_and_filters_cannot_redirect_baseline(tmp_path, monkeypatch):
    memory, outside = tmp_path / "memory", tmp_path / "outside"
    memory.mkdir()
    outside.mkdir()
    subprocess.run(["git", "init", "-q", str(outside)], check=True, timeout=10)
    original = (outside / ".git/HEAD").read_bytes()
    monkeypatch.setenv("GIT_DIR", str(outside / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outside))
    monkeypatch.setenv("GIT_INDEX_FILE", str(outside / "index"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(outside / "objects"))
    (memory / "note").write_text("literal CRLF\r\n")
    (memory / ".gitattributes").write_text("* text eol=lf filter=external")
    git.prepare(memory)
    assert base64.b64decode(git.read(memory)["note"]["content"]) == (memory / "note").read_bytes()
    git.reset(memory)
    assert (outside / ".git/HEAD").read_bytes() == original
    assert not (outside / "objects").exists() and not (outside / "index").exists()


def test_failed_reset_is_not_rolled_back_or_automatically_replayed(tmp_path, monkeypatch):
    (tmp_path / "note").write_text("first")
    git.prepare(tmp_path)
    (tmp_path / "note").write_text("second")
    calls = []

    def fail(*args):
        calls.append(args)
        raise OSError("injected reset failure")

    with monkeypatch.context() as patch:
        patch.setattr(git, "_write_tree", fail)
        with pytest.raises(OSError, match="injected reset"):
            git.reset(tmp_path)
    assert len(calls) == 1
    assert (tmp_path / "note").read_text() == "second"
    git.prepare(tmp_path)  # a new owned pass, not a blind retry of an unknown operation
    assert git.read(tmp_path) == git.capture(tmp_path)


def test_no_hooks_filters_or_auth_context_are_inherited(tmp_path, monkeypatch):
    git.prepare(tmp_path)
    marker = tmp_path / "hook-ran"
    hooks = tmp_path / ".git/hooks"
    hooks.mkdir()
    for name in ("pre-commit", "post-commit", "reference-transaction"):
        hook = hooks / name
        hook.write_text("#!/bin/sh\ntouch '" + str(marker) + "'\n")
        hook.chmod(0o755)
    (tmp_path / ".gitattributes").write_text("*.txt filter=fixture")
    git._git(tmp_path, "config", "filter.fixture.clean", "touch '" + str(marker) + "'")
    (tmp_path / "literal.txt").write_bytes(b"literal\r\n")
    calls, original = [], subprocess.run
    monkeypatch.setenv("node_repl_auth_token", "fixture-token")

    def run(*args, **kwargs):
        calls.append(kwargs["env"])
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    commit = git._git(tmp_path, "rev-parse", "HEAD").strip().decode()
    git._git(tmp_path, "update-ref", "refs/fixture", commit)
    assert not marker.exists(), "even existing reference-transaction hooks must be disabled"
    git.reset(tmp_path)
    assert not marker.exists()
    assert all("node_repl_auth_token" not in env for env in calls)
    assert base64.b64decode(git.read(tmp_path)["literal.txt"]["content"]) == b"literal\r\n"


@pytest.mark.parametrize("name", ["有空格 的文件", "line\nbreak", "tab\tname"])
def test_git_paths_round_trip_without_line_protocol_ambiguity(tmp_path, name):
    (tmp_path / name).write_bytes(b"first\0line\n")
    git.prepare(tmp_path)
    assert git.read(tmp_path) == git.capture(tmp_path)
    (tmp_path / name).write_bytes(b"second\0line\n")
    assert not git.matches(tmp_path, git.capture(tmp_path))
    git.reset(tmp_path)
    assert git.read(tmp_path) == git.capture(tmp_path)
