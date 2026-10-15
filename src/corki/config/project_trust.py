"""Host project selection, mapped to pinned ConfigToml and git-utils/trust.rs.

This selects an active project, not the distinct per-directory config-layer gate.
Filesystem metadata is host discovery; trust never comes from model/tool arguments.
"""

import os
import stat
import sys
from pathlib import Path

_MAX_METADATA = 64 * 1024
_ASCII_SPACE = b" \t\n\r\x0c"
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def project_configurations(document: dict) -> dict:
    """Validate the shared input without conflating its two trust consumers."""
    projects = document.get("projects")
    if projects is None:
        return {}
    if not isinstance(projects, dict):
        raise ValueError("projects must be a table of project configurations")
    for name, value in projects.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            raise ValueError("each project configuration must be a named table")
        trust = value.get("trust_level")
        if trust is not None and (
            not isinstance(trust, str) or trust not in {"trusted", "untrusted"}
        ):
            raise ValueError("project trust_level must be trusted or untrusted")
    return projects


def lookup_project(projects: dict, path: Path, *, normalize_wsl: bool = True) -> dict | None:
    """Canonical/logical lookup; an existing empty entry is not absence."""
    for key in _lookup_keys(path, normalize_wsl=normalize_wsl):
        if key in projects:
            return projects[key]
        matches = sorted(name for name in projects if _lookup_key(name) == key)
        if matches:
            return projects[matches[0]]
    return None


def active_project_trust(document: dict, cwd: Path) -> str | None:
    projects = project_configurations(document)
    cwd = Path(os.path.abspath(cwd))
    # Presence matters: an empty cwd entry masks a trusted repository entry.
    for path in (cwd, trust_git_root(cwd)):
        if path is None:
            continue
        project = lookup_project(projects, path)
        if project is not None:
            return project.get("trust_level")
    return None


def _lookup_key(value: str) -> str:
    return value.translate(_ASCII_LOWER) if os.name == "nt" else value


def _lossy_path(path: Path) -> str:
    if os.name == "nt":
        return str(path).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return os.fsencode(path).decode("utf-8", "replace")


def _lookup_keys(path: Path, *, normalize_wsl: bool = True) -> tuple[str, ...]:
    canonical = _canonical(path)
    if canonical is None:
        # Native normalization returns an error before WSL folding when the
        # filesystem cannot canonicalize; its caller then uses the logical key.
        return (_lookup_key(_lossy_path(path)),)
    # Native comparison normalization folds only ASCII on WSL drive mounts.
    if normalize_wsl and sys.platform == "linux" and len(canonical.parts) >= 3:
        parts = canonical.parts
        if (
            parts[1].lower() == "mnt"
            and len(parts[2]) == 1
            and parts[2].isascii()
            and parts[2].isalpha()
        ):
            try:
                wsl = (
                    "WSL_DISTRO_NAME" in os.environ
                    or "microsoft" in Path("/proc/version").read_text().lower()
                )
            except (OSError, UnicodeError):
                wsl = False
            if wsl:
                canonical = Path(str(canonical).translate(_ASCII_LOWER))
    keys = (_lookup_key(_lossy_path(canonical)), _lookup_key(_lossy_path(path)))
    return tuple(dict.fromkeys(keys))


def _canonical(path: Path) -> Path | None:
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None


def _metadata(path: Path):
    try:
        original = path.lstat()
        is_link = stat.S_ISLNK(original.st_mode)
        return (path.stat() if is_link else original), is_link
    except (OSError, ValueError):
        return None


def _read_metadata(path: Path) -> bytes | None:
    entry = _metadata(path)
    if entry is None:
        return None
    metadata, is_link = entry
    if is_link or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_METADATA:
        return None
    try:
        with path.open("rb") as stream:
            value = stream.read(_MAX_METADATA + 1)
        return value if len(value) <= _MAX_METADATA else None
    except (OSError, ValueError):
        return None


def _join_bytes(parent: Path, value: bytes) -> Path | None:
    # PathUri native joining normalizes dot components without resolving aliases.
    if b"\0" in value:
        return None
    try:
        return Path(os.path.abspath(parent / os.fsdecode(value)))
    except (OSError, ValueError):
        return None


def _gitdir(path: Path) -> Path | None:
    value = _read_metadata(path)
    if value is None:
        return None
    value = value.strip(_ASCII_SPACE)
    if not value.startswith(b"gitdir:"):
        return None
    target = value[len(b"gitdir:") :].strip(_ASCII_SPACE)
    return _join_bytes(path.parent, target) if target else None


def trust_git_root(cwd: Path) -> Path | None:
    """Resolve only proven Git/worktree ownership; never run a Git executable."""
    entry = _metadata(cwd)
    base = cwd if entry is not None and stat.S_ISDIR(entry[0].st_mode) else cwd.parent
    root = None
    for candidate in (base, *base.parents):
        entry = _metadata(candidate / ".git")
        if entry is None:
            continue
        if not stat.S_ISDIR(entry[0].st_mode) or _metadata(candidate / ".git/HEAD") is not None:
            root = candidate
            break
    if root is None:
        return None
    dot_git = root / ".git"
    entry = _metadata(dot_git)
    if entry is None:
        return None
    if stat.S_ISDIR(entry[0].st_mode):
        return root
    git_dir = _gitdir(dot_git)
    if git_dir is None:
        return None
    entry = _metadata(git_dir)
    if entry is None or not stat.S_ISDIR(entry[0].st_mode) or entry[1]:
        return None
    canonical = _canonical(git_dir)
    if canonical is None or canonical.parent.name != "worktrees":
        return None
    common_dir = canonical.parent.parent
    backlink, common = _read_metadata(canonical / "gitdir"), _read_metadata(canonical / "commondir")
    if backlink is None or common is None:
        return None
    backlink, common = backlink.strip(_ASCII_SPACE), common.strip(_ASCII_SPACE)
    if not backlink or not common:
        return None
    registered = _join_bytes(canonical, backlink)
    if registered is None or registered.name != ".git":
        return None
    checkout = _canonical(root)
    registered_checkout = _canonical(registered.parent)
    common_path = _join_bytes(canonical, common)
    linked_common = _canonical(common_path) if common_path is not None else None
    # Compare canonical directory spellings, not Path equality (case-folded on
    # Windows), nor the final .git entries which might have been swapped.
    if checkout is None or registered_checkout is None or linked_common is None:
        return None
    if str(checkout) != str(registered_checkout) or str(linked_common) != str(common_dir):
        return None
    # Unlike Rust Path::parent(), pathlib's root.parent is the root itself.
    # The common directory must actually have a containing main checkout.
    if len(git_dir.parents) < 3:
        return None
    main_root = git_dir.parents[2]
    main_dot_git = main_root / ".git"
    entry = _metadata(main_dot_git)
    if entry is None:
        return None
    main_dir = main_dot_git if stat.S_ISDIR(entry[0].st_mode) else _gitdir(main_dot_git)
    if main_dir is None:
        return None
    owned_common = _canonical(main_dir)
    return main_root if owned_common is not None and str(owned_common) == str(common_dir) else None
