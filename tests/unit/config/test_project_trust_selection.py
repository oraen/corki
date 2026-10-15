"""Active-project keys and worktree ownership mapped from Codex source tests."""

import errno
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from corki.config import project_trust
from corki.config.project_trust import active_project_trust, trust_git_root


def worktree(root, *, separate=False):
    main, checkout = root / "main", root / "checkout"
    main.mkdir()
    checkout.mkdir()
    common = main / ("metadata" if separate else ".git")
    registration = common / "worktrees/id"
    registration.mkdir(parents=True)
    (common / "HEAD").write_text("ref: refs/heads/main\n")
    if separate:
        (main / ".git").write_bytes(b"gitdir: " + os.fsencode(common))
    (checkout / ".git").write_bytes(b"gitdir: " + os.fsencode(registration))
    (registration / "gitdir").write_bytes(os.fsencode(checkout / ".git"))
    (registration / "commondir").write_bytes(b"../..\n")
    return main, checkout, registration


@pytest.mark.parametrize("separate", [False, True])
@pytest.mark.parametrize("location", ["root", "nested", "file", "empty_nested_git", "alias"])
def test_proven_worktree_returns_main_trust_root(tmp_path, separate, location):
    main, checkout, registration = worktree(tmp_path, separate=separate)
    cwd = checkout
    if location in {"nested", "file", "empty_nested_git"}:
        cwd = checkout / "nested"
        cwd.mkdir()
    if location == "file":
        cwd = cwd / "file.txt"
        cwd.write_text("data")
    if location == "empty_nested_git":
        (cwd / ".git").mkdir()
    if location == "alias":
        cwd = tmp_path / "alias"
        cwd.symlink_to(checkout, target_is_directory=True)
    assert trust_git_root(cwd) == main
    assert (
        active_project_trust({"projects": {str(main): {"trust_level": "untrusted"}}}, cwd)
        == "untrusted"
    )


@pytest.mark.parametrize(
    "failure",
    [
        "missing_backlink",
        "missing_common",
        "wrong_backlink",
        "wrong_common",
        "symlink_dotgit",
        "symlink_backlink",
        "symlink_common",
        "symlink_target",
        "wrong_owner",
        "oversize",
        "nul",
        "vertical_tab",
        "non_ascii_space",
    ],
)
def test_unproven_worktrees_do_not_inherit_main_trust(tmp_path, failure):
    main, checkout, registration = worktree(tmp_path, separate=True)
    other = tmp_path / "other"
    other.mkdir()
    if failure == "missing_backlink":
        (registration / "gitdir").unlink()
    elif failure == "missing_common":
        (registration / "commondir").unlink()
    elif failure == "wrong_backlink":
        (registration / "gitdir").write_text(str(other / ".git"))
    elif failure == "wrong_common":
        (registration / "commondir").write_text(str(other))
    elif failure == "wrong_owner":
        (main / ".git").write_text("gitdir: " + str(other))
    elif failure in {"symlink_dotgit", "symlink_backlink", "symlink_common"}:
        path = (
            checkout / ".git"
            if failure == "symlink_dotgit"
            else registration / ("gitdir" if failure == "symlink_backlink" else "commondir")
        )
        moved = other / "metadata"
        path.rename(moved)
        path.symlink_to(moved)
    elif failure == "symlink_target":
        alias = tmp_path / "target_alias"
        alias.symlink_to(registration, target_is_directory=True)
        (checkout / ".git").write_text("gitdir: " + str(alias))
    else:
        contents = (checkout / ".git").read_bytes()
        contents = {
            "oversize": contents + b" " * (64 * 1024),
            "nul": b"gitdir: \0",
            "vertical_tab": b"\x0b" + contents,
            "non_ascii_space": b"\xc2\xa0" + contents,
        }[failure]
        (checkout / ".git").write_bytes(contents)
    assert trust_git_root(checkout) is None
    assert (
        active_project_trust({"projects": {str(main): {"trust_level": "trusted"}}}, checkout)
        is None
    )


def test_regular_repo_requires_head_and_explicit_empty_cwd_entry_masks_root(tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    nested = root / "nested"
    nested.mkdir()
    assert trust_git_root(nested) is None
    (root / ".git/HEAD").write_text("")
    config = {"projects": {str(root): {"trust_level": "trusted"}}}
    assert trust_git_root(nested) == root
    assert active_project_trust(config, nested) == "trusted"
    config["projects"][str(nested)] = {}
    assert active_project_trust(config, nested) is None


def test_canonical_key_precedes_alias_but_alias_is_not_reverse_canonicalized(tmp_path):
    real, alias = tmp_path / "real", tmp_path / "alias"
    real.mkdir()
    alias.symlink_to(real, target_is_directory=True)
    config = {"projects": {str(alias): {"trust_level": "trusted"}}}
    assert active_project_trust(config, alias) == "trusted"
    assert active_project_trust(config, real) is None
    config["projects"][str(real)] = {"trust_level": "untrusted"}
    assert active_project_trust(config, alias) == "untrusted"


@pytest.mark.parametrize(
    "projects",
    [
        [],
        "bad",
        {"/x": []},
        {"/x": {"trust_level": "unknown"}},
        {"/x": {"trust_level": True}},
        {"/x": {"trust_level": []}},
    ],
)
def test_malformed_projects_reject_before_any_selection(tmp_path, projects):
    with pytest.raises(ValueError, match="project"):
        active_project_trust({"projects": projects}, tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="Unix byte paths")
def test_non_utf8_worktree_backlink_and_lossy_config_key(tmp_path):
    main, checkout, registration = worktree(tmp_path)
    renamed = Path(os.fsdecode(os.fsencode(tmp_path) + b"/checkout-\xff"))
    try:
        checkout.rename(renamed)
    except OSError as error:
        if error.errno == errno.EILSEQ:
            pytest.skip("filesystem rejects non-UTF-8 filenames")
        raise
    (registration / "gitdir").write_bytes(os.fsencode(renamed / ".git"))
    assert trust_git_root(renamed) == main
    key = os.fsencode(renamed).decode("utf-8", "replace")
    assert (
        active_project_trust({"projects": {key: {"trust_level": "untrusted"}}}, renamed)
        == "untrusted"
    )


def test_exact_metadata_limit_and_logical_main_alias(tmp_path):
    main, checkout, registration = worktree(tmp_path)
    alias = tmp_path / "main_alias"
    alias.symlink_to(main, target_is_directory=True)
    pointer = b"gitdir: " + os.fsencode(alias / ".git/worktrees/id")
    (checkout / ".git").write_bytes(pointer + b" " * (64 * 1024 - len(pointer)))
    assert trust_git_root(checkout) == alias


@pytest.mark.parametrize("canonical_exists", [False, True])
def test_wsl_folds_only_successfully_canonicalized_drive_paths(monkeypatch, canonical_exists):
    path = Path("/mnt/C/Project")
    monkeypatch.setattr(project_trust.sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "fixture")
    monkeypatch.setattr(project_trust, "_canonical", lambda _: path if canonical_exists else None)
    monkeypatch.setattr(project_trust, "trust_git_root", lambda _: None)
    document = {
        "projects": {
            "/mnt/c/project": {"trust_level": "untrusted"},
            "/mnt/C/Project": {"trust_level": "trusted"},
        }
    }
    assert active_project_trust(document, path) == ("untrusted" if canonical_exists else "trusted")


def test_relative_backlink_and_move_require_repaired_ownership(tmp_path):
    main, checkout, registration = worktree(tmp_path)
    (checkout / ".git").write_text("gitdir: ../main/.git/worktrees/id\n")
    (registration / "gitdir").write_text("../../../../checkout/.git\n")
    assert trust_git_root(checkout) == main
    moved = tmp_path / "moved"
    checkout.rename(moved)
    assert trust_git_root(moved) is None
    (registration / "gitdir").write_text("../../../../moved/.git\n")
    assert trust_git_root(moved) == main


def test_filesystem_root_cannot_be_its_own_main_checkout(tmp_path, monkeypatch):
    # Native AbsolutePathBuf::parent() returns None at the root. Model an
    # executor filesystem without creating anything at the host filesystem root.
    root = Path(tmp_path.anchor)
    registration = root / "worktrees/id"
    metadata = {
        tmp_path: stat.S_IFDIR,
        tmp_path / ".git": stat.S_IFREG,
        registration: stat.S_IFDIR,
        root / ".git": stat.S_IFDIR,
    }
    monkeypatch.setattr(
        project_trust,
        "_metadata",
        lambda path: (SimpleNamespace(st_mode=metadata[path]), False) if path in metadata else None,
    )
    monkeypatch.setattr(project_trust, "_gitdir", lambda _: registration)
    monkeypatch.setattr(
        project_trust,
        "_read_metadata",
        lambda path: os.fsencode(tmp_path / ".git") if path.name == "gitdir" else b"../..",
    )
    monkeypatch.setattr(
        project_trust, "_canonical", lambda path: root if path == root / ".git" else path
    )
    assert trust_git_root(tmp_path) is None
