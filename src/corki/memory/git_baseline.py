"""Git-backed baselines for the internal shared memory directory.

Only plumbing commands are used: no add filters, hooks, external diff or user
identity configuration. Callers own these synchronous operations through shutdown.
"""

from __future__ import annotations

import base64
import os
import shutil
import stat
import subprocess
from pathlib import Path

from corki.mcp.environment import NON_INHERITABLE
from corki.memory import workspace

_MARKER = "corki-memory-baseline-v1\n"
_LEGACY = ".consolidation-baseline.json"
_MESSAGE = b"Initialize Corki git baseline\n"


def _root(root: Path) -> Path:
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("memory Git baseline requires a non-symlink absolute root")
    resolved = root.resolve()
    if resolved.parent == resolved or resolved == Path.home().resolve():
        raise ValueError("memory Git baseline requires a bounded internal directory")
    return resolved


def _git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    # Pin both routing and config. Ambient GIT_DIR/INDEX_FILE/OBJECT_DIRECTORY
    # must never redirect a destructive internal reset into a user's repository.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_") and key.upper() not in NON_INHERITABLE
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="Corki",
        GIT_AUTHOR_EMAIL="noreply@corki.local",
        GIT_COMMITTER_NAME="Corki",
        GIT_COMMITTER_EMAIL="noreply@corki.local",
        GIT_TERMINAL_PROMPT="0",
    )
    command = [
        "git",
        "--no-replace-objects",
        f"--git-dir={root / '.git'}",
        f"--work-tree={root}",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"safe.directory={root}",
        *args,
    ]
    result = subprocess.run(
        command,
        cwd=root,
        env=env,
        input=data,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "memory Git baseline failed: " + result.stderr.decode("utf-8", "replace")
        )
    return result.stdout


def capture(root: Path) -> workspace.Snapshot:
    """Capture every Git-supported entry, ignoring only .git and non-regular types."""
    root = _root(root)
    pending, snapshot = [root], {}
    while pending:
        for path in pending.pop().iterdir():
            if path.name == ".git":
                continue
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                pending.append(path)
                continue
            if stat.S_ISLNK(mode):
                content, git_mode = os.fsencode(os.readlink(path)), "120000"
            elif stat.S_ISREG(mode):
                content = path.read_bytes()
                git_mode = "100755" if os.name != "nt" and mode & 0o111 else "100644"
            else:
                continue
            snapshot[path.relative_to(root).as_posix()] = {
                "content": base64.b64encode(content).decode("ascii"),
                "mode": git_mode,
            }
    return snapshot


def read(root: Path) -> workspace.Snapshot:
    """Read HEAD without creating objects or consulting the working-tree index."""
    root = _root(root)
    entries = []
    for row in _git(root, "ls-tree", "-rz", "HEAD").split(b"\0"):
        if not row:
            continue
        header, name = row.split(b"\t", 1)
        mode, kind, oid = header.split(b" ")
        if kind == b"blob":
            entries.append((os.fsdecode(name), mode.decode("ascii"), oid))
    if not entries:
        return {}
    raw = _git(root, "cat-file", "--batch", data=b"".join(oid + b"\n" for _, _, oid in entries))
    offset, snapshot = 0, {}
    for name, mode, expected in entries:
        end = raw.index(b"\n", offset)
        oid, kind, size = raw[offset:end].split()
        if oid != expected or kind != b"blob":
            raise ValueError("invalid memory Git baseline blob")
        start, length = end + 1, int(size)
        content = raw[start : start + length]
        if len(content) != length or raw[start + length : start + length + 1] != b"\n":
            raise ValueError("truncated memory Git baseline blob")
        snapshot[name] = {"content": base64.b64encode(content).decode("ascii"), "mode": mode}
        offset = start + length + 1
    return snapshot


def _owned_metadata(root: Path) -> None:
    metadata = root / ".git"
    if not metadata.exists() and not metadata.is_symlink():
        return
    if metadata.is_symlink() or not metadata.is_dir():
        raise ValueError("refusing unowned memory .git metadata")
    marker = metadata / "corki-memory-baseline"
    if marker.is_file() and not marker.is_symlink() and marker.read_text() == _MARKER:
        return
    # Accept a native internal baseline, not an arbitrary project checkout.
    try:
        message = _git(root, "log", "-1", "--format=%B").strip()
        count = _git(root, "rev-list", "--count", "HEAD").strip()
        if count == b"1" and message == (
            b"Initialize Codex git baseline\n\nCo-authored-by: Codex <noreply@openai.com>"
        ):
            return
    except RuntimeError:
        pass
    raise ValueError("refusing to replace an unowned Git repository in memory root")


def _write_tree(root: Path, snapshot: workspace.Snapshot) -> bytes:
    directories: dict[str, list[bytes]] = {"": []}
    for name, entry in sorted(snapshot.items()):
        parts = name.split("/")
        if any(part in {"", ".", "..", ".git"} for part in parts):
            raise ValueError("invalid memory Git baseline path")
        parent = "/".join(parts[:-1])
        for index in range(1, len(parts)):
            directories.setdefault("/".join(parts[:index]), [])
        oid = _git(
            root, "hash-object", "-w", "--stdin", data=base64.b64decode(entry["content"])
        ).strip()
        directories[parent].append(
            entry["mode"].encode() + b" blob " + oid + b"\t" + os.fsencode(parts[-1]) + b"\0"
        )
    for name in sorted(directories, key=lambda p: (p.count("/"), len(p)), reverse=True):
        oid = _git(root, "mktree", "-z", data=b"".join(directories[name])).strip()
        if not name:
            return oid
        parent, _, leaf = name.rpartition("/")
        directories[parent].append(b"040000 tree " + oid + b"\t" + os.fsencode(leaf) + b"\0")
    raise AssertionError("missing memory Git root tree")


def reset(root: Path, *, snapshot: workspace.Snapshot | None = None) -> None:
    """Replace only owned .git with one commit; retain no old objects or backups."""
    root = _root(root)
    _owned_metadata(root)
    from corki.memory.artifacts import remove_workspace_diff

    remove_workspace_diff(root)
    current = capture(root) if snapshot is None else snapshot
    metadata = root / ".git"
    if metadata.is_dir():
        shutil.rmtree(metadata)
    metadata.mkdir(mode=0o700)
    (metadata / "corki-memory-baseline").write_text(_MARKER)
    _git(root, "init", "-q", "--template=", "--initial-branch=main", "--object-format=sha1")
    tree = _write_tree(root, current)
    commit = _git(root, "commit-tree", tree.decode("ascii"), data=_MESSAGE).strip()
    _git(root, "update-ref", "HEAD", commit.decode("ascii"))
    _git(root, "read-tree", "--reset", "HEAD")


def prepare(root: Path) -> None:
    """Prepare the baseline before child configuration and input synchronization."""
    from corki.memory.artifacts import remove_workspace_diff

    root = _root(root)
    # Check ownership before layout cleanup can touch a caller's unrelated .git.
    ensure_layout(root)
    remove_workspace_diff(root)
    legacy = root / _LEGACY
    previous = workspace.read_previous(root) if legacy.exists() else None
    usable = False
    if (root / ".git").is_dir():
        try:
            read(root)
            usable = True
        except (RuntimeError, ValueError):
            pass  # Owned corrupt/unborn metadata is rebuilt like the native baseline.
    if not usable:
        if legacy.exists():
            # Known content survives migration; unknown legacy versions force a
            # real pass instead of silently blessing current outputs as consumed.
            reset(root, snapshot=previous or {})
        else:
            reset(root)
    if legacy.exists():
        legacy.unlink()  # Only after the replacement baseline is usable.


def ensure_layout(root: Path) -> None:
    """Clean shared workspace links before creating managed input directories."""
    from corki.memory.artifacts import ensure_memory_layout, remove_memory_symlinks

    root = _root(root)
    _owned_metadata(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    remove_memory_symlinks(root)
    ensure_memory_layout(root)


def matches(root: Path, current: workspace.Snapshot) -> bool:
    """An unchanged tree is a no-op only with valid live memory artifacts."""
    if read(root) != current:
        return False
    from corki.memory.artifacts import validate_shared_artifacts

    try:
        validate_shared_artifacts(root)
    except (OSError, UnicodeError, ValueError):
        return False
    return True
