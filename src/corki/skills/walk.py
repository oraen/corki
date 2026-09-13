"""Bounded host skill traversal matching the native filesystem walk contract."""

import os
import stat
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from corki.skills.io import check_skill_io
from corki.skills.models import SkillDiscoveryMode, SkillLoadError

MAX_DIRECTORIES = 2000
MAX_ENTRIES = 20_000
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
ITEM_OVERHEAD_BYTES = 64
# Rust url's SPECIAL_PATH_SEGMENT leaves these printable ASCII bytes unescaped;
# pathlib.as_uri percent-encodes more punctuation. Keep budget accounting native.
_URI_LITERAL_ESCAPES = tuple(
    f"%{value:02X}" for value in range(33, 127) if chr(value) not in '"#%/<>?\\\\`{}'
)


@dataclass(frozen=True, slots=True)
class SkillWalk:
    files: tuple[Path, ...] = ()
    errors: tuple[SkillLoadError, ...] = ()
    truncated: bool = False


def walk_skill_files(
    root: Path,
    *,
    mode: SkillDiscoveryMode = SkillDiscoveryMode.RECURSIVE,
    follow_directory_links: bool = True,
) -> SkillWalk:
    """Return a bounded BFS inventory; traversal failures are not parse failures."""
    check_skill_io()
    files, errors = [], []
    truncated = False
    response_bytes = 0
    root = root.absolute()

    def reserve(path, message=""):
        nonlocal response_bytes, truncated
        uri = path.as_uri()
        uri_size = len(uri) - 2 * sum(uri.count(token) for token in _URI_LITERAL_ESCAPES)
        cost = uri_size + len(message.encode("utf-8", errors="replace"))
        cost += ITEM_OVERHEAD_BYTES
        if response_bytes + cost > MAX_RESPONSE_BYTES:
            truncated = True
            return False
        response_bytes += cost
        return True

    def failure(path, error):
        try:
            message = str(error)
        except Exception:
            message = type(error).__name__
        message = message.encode("utf-8", errors="replace").decode("utf-8")
        if not reserve(path, message):
            return False
        errors.append(SkillLoadError(path, message))
        return True

    try:
        metadata = root.lstat()
        linked = stat.S_ISLNK(metadata.st_mode)
        if linked:
            metadata = root.stat()
        if not stat.S_ISDIR(metadata.st_mode) or (linked and not follow_directory_links):
            return SkillWalk()
        identity = root.resolve(strict=True) if follow_directory_links else root
    except FileNotFoundError:
        return SkillWalk()
    except (OSError, RuntimeError) as error:
        failure(root, error)
        return SkillWalk((), tuple(errors), truncated)

    pending = deque([(root, 0)])
    visited = {identity}
    directories, entries_seen = 1, 0
    maximum_depth = 2 if mode is SkillDiscoveryMode.DIRECT_CHILDREN else 6
    while pending:
        check_skill_io()
        directory, depth = pending.popleft()
        try:
            # Like native read_directory, broken/inaccessible links never count
            # towards the entry limit. File-type failures at this stage are skipped.
            names = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    check_skill_io()
                    try:
                        linked = entry.is_symlink()
                        if linked:
                            entry.stat(follow_symlinks=True)
                    except OSError:
                        continue
                    names.append(entry.name)
        except OSError as error:
            if not failure(directory, error):
                break
            continue
        for name in sorted(names):
            check_skill_io()
            if entries_seen == MAX_ENTRIES:
                return SkillWalk(tuple(files), tuple(errors), True)
            entries_seen += 1
            path = directory / name
            try:
                metadata = path.lstat()
                linked = stat.S_ISLNK(metadata.st_mode)
                if linked:
                    metadata = path.stat()
                is_directory = stat.S_ISDIR(metadata.st_mode)
                if linked and (not follow_directory_links or not is_directory):
                    continue
                if not is_directory and not stat.S_ISREG(metadata.st_mode):
                    continue
            except OSError as error:
                if not failure(path, error):
                    return SkillWalk(tuple(files), tuple(errors), True)
                continue
            if not reserve(path):
                return SkillWalk(tuple(files), tuple(errors), True)
            if not is_directory:
                if name == "SKILL.md" and (
                    mode is SkillDiscoveryMode.RECURSIVE or path.parent.parent == root
                ):
                    files.append(path)
                continue
            if depth >= maximum_depth or name.startswith("."):
                continue
            try:
                identity = path.resolve(strict=True) if follow_directory_links else path
            except (OSError, RuntimeError) as error:
                if not failure(path, error):
                    return SkillWalk(tuple(files), tuple(errors), True)
                continue
            if identity in visited:
                continue
            visited.add(identity)
            if directories == MAX_DIRECTORIES:
                truncated = True
            else:
                directories += 1
                pending.append((path, depth + 1))
    check_skill_io()
    return SkillWalk(tuple(files), tuple(errors), truncated)
