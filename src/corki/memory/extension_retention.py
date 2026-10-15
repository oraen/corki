"""Best-effort retention of timestamped extension resources before consolidation."""

import logging
import stat
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

_LOG = logging.getLogger(__name__)
_RETENTION = timedelta(days=7)


def prune_old_extension_resources(root: Path, *, now: datetime | None = None) -> bool:
    """Remove expired direct Markdown resources, never extension notes or link targets.

    Called inside the lease-owned workspace write, before its snapshot/dirty check.
    File-system failures are diagnostic only; later sampling failure does not undo
    expiry. The return value records successful deletions, not publication success.
    """
    cutoff = (now if now is not None else datetime.fromtimestamp(time.time(), UTC)) - _RETENTION
    extensions = root / "extensions"
    if not _is_directory(root) or not _is_directory(extensions):
        return False
    changed = False
    for extension in _entries(extensions):
        if not _is_directory(extension):
            continue
        try:
            if not (extension / "instructions.md").exists():
                continue
        except OSError:
            continue
        resources = extension / "resources"
        if not _is_directory(resources):
            continue
        for resource in _entries(resources):
            try:
                if not stat.S_ISREG(resource.lstat().st_mode):
                    continue
            except OSError:
                continue
            timestamp = _resource_timestamp(resource.name)
            if timestamp is None or timestamp > cutoff:
                continue
            try:
                resource.unlink()
            except FileNotFoundError:
                pass  # A concurrent removal is already the desired state.
            except OSError as exc:
                _LOG.warning("failed pruning old memory extension resource %s: %s", resource, exc)
            else:
                changed = True
    return changed


def _is_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except FileNotFoundError:
        return False
    except OSError as exc:
        _LOG.warning("failed inspecting memory extension directory %s: %s", path, exc)
        return False


def _entries(path: Path) -> Iterator[Path]:
    try:
        yield from path.iterdir()
    except FileNotFoundError:
        return
    except OSError as exc:
        _LOG.warning("failed reading memory extension directory %s: %s", path, exc)


def _resource_timestamp(name: str) -> datetime | None:
    if not name.endswith(".md"):
        return None
    prefix = name[:19]
    # Chrono accepts leap seconds; Python datetime does not. Normalize that one
    # second for comparison with the ordinary UTC clock used for the cutoff.
    leap_second = prefix.endswith("-60")
    if leap_second:
        prefix = prefix[:-2] + "59"
    try:
        timestamp = datetime.strptime(prefix, "%Y-%m-%dT%H-%M-%S").replace(tzinfo=UTC)
        return timestamp + timedelta(seconds=leap_second)
    except (ValueError, OverflowError):
        return None
