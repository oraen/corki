"""Scoped list/read/search operations over the local memory hierarchy."""

from __future__ import annotations

import errno
import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from corki.memory.artifacts import ensure_memory_layout
from corki.memory.inputs import truncate_memory_text
from corki.memory.repository import MemoryRepository

AD_HOC_FILENAME_PATTERN = (
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}-[a-z0-9][a-z0-9-]{0,79}\.md"
)


class MemoryPathError(ValueError):
    """Raised when a requested path could escape or confuse the memory root."""


class MemoryNotFoundError(FileNotFoundError):
    """A requested memory is absent, rather than an in-flight I/O operation failing."""


class MemoryNoteExistsError(FileExistsError):
    """Create-new rejected a duplicate note; its existing contents are unchanged."""


class LocalMemoryBackend:
    """Codex-style progressive disclosure without a vector database."""

    def __init__(self, root: Path, repository: MemoryRepository | None = None) -> None:
        # Legacy constructor compatibility; completed assistant citations own
        # usage feedback. Reading candidate text is not an adoption signal.
        del repository
        self.root = root
        ensure_memory_layout(root)

    def list(self, path: str | None = None, *, cursor: int = 0, limit: int = 2_000) -> dict:
        target = self._resolve(path)
        if cursor < 0 or not 1 <= limit <= 2_000:
            raise ValueError("cursor must be non-negative and limit must be between 1 and 2000")
        metadata = _required_metadata(target, path)
        paths = (
            [(target, metadata)]
            if stat.S_ISREG(metadata.st_mode)
            else _sorted_entries(target)
            if stat.S_ISDIR(metadata.st_mode)
            else []
        )
        entries = [
            {
                "path": item.relative_to(self.root).as_posix(),
                "type": "directory" if stat.S_ISDIR(info.st_mode) else "file",
            }
            for item, info in paths
            if not item.name.startswith(".")
            and (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
        ]
        if cursor > len(entries):
            raise ValueError("cursor exceeds result count")
        end = min(len(entries), cursor + limit)
        return {
            "path": path,
            "entries": entries[cursor:end],
            "next_cursor": end if end < len(entries) else None,
            "truncated": end < len(entries),
        }

    async def read(
        self,
        path: str,
        *,
        line_offset: int = 1,
        max_lines: int | None = None,
        max_tokens: int = 20_000,
    ) -> dict:
        if line_offset < 1 or (max_lines is not None and max_lines < 1) or max_tokens < 0:
            raise ValueError("line_offset, max_lines, and max_tokens must be positive")
        target = self._resolve(path)
        metadata = _required_metadata(target, path)
        if not stat.S_ISREG(metadata.st_mode):
            raise MemoryPathError("memory read target must be a regular file")
        content = target.read_bytes().decode("utf-8")
        starts = [0, *(index + 1 for index, char in enumerate(content) if char == "\n")]
        if line_offset > len(starts):
            raise ValueError("line_offset exceeds file length")
        end_line = line_offset - 1 + max_lines if max_lines is not None else len(starts)
        end = starts[end_line] if end_line < len(starts) else len(content)
        selected = content[starts[line_offset - 1] : end]
        text = truncate_memory_text(selected, max_tokens or 20_000)
        return {
            "path": path,
            "start_line_number": line_offset,
            "content": text,
            "truncated": end < len(content) or text != selected,
        }

    async def search(
        self,
        queries: tuple[str, ...],
        *,
        path: str | None = None,
        match_mode: str = "any",
        within_lines: int = 1,
        context_lines: int = 0,
        case_sensitive: bool = True,
        normalized: bool = False,
        cursor: int = 0,
        limit: int = 200,
    ) -> dict:
        cleaned = tuple(query.strip() for query in queries)
        if not cleaned or any(not query for query in cleaned):
            raise ValueError("queries must not be empty")
        if match_mode not in {"any", "all_on_same_line", "all_within_lines"}:
            raise ValueError("unsupported match_mode")
        if within_lines < 1 or context_lines < 0 or cursor < 0 or not 1 <= limit <= 200:
            raise ValueError("invalid search bounds")
        start = self._resolve(path)
        metadata = _required_metadata(start, path)
        prepared_queries = [
            _prepare(value, case_sensitive=case_sensitive, normalized=normalized)
            for value in cleaned
        ]
        if any(not value for value in prepared_queries):
            raise ValueError("queries must not be empty after normalization")
        matches: list[dict] = []
        for file in _search_files(start, metadata):
            try:
                content = file.read_bytes().decode("utf-8")
                lines = _search_lines(content)
            except UnicodeError:
                continue
            prepared_lines = [
                _prepare(value, case_sensitive=case_sensitive, normalized=normalized)
                for value in lines
            ]
            for first, last in _matching_windows(
                prepared_lines, prepared_queries, match_mode, within_lines
            ):
                begin = max(0, first - context_lines)
                end = min(len(lines), last + context_lines + 1)
                matches.append(
                    {
                        "path": file.relative_to(self.root).as_posix(),
                        "match_line_number": first + 1,
                        "content_start_line_number": begin + 1,
                        "content": "\n".join(lines[begin:end]),
                        "matched_queries": [
                            query
                            for query, prepared in zip(cleaned, prepared_queries, strict=True)
                            if any(prepared in line for line in prepared_lines[first : last + 1])
                        ],
                    }
                )
        matches.sort(key=lambda item: (item["path"], item["match_line_number"]))
        if cursor > len(matches):
            raise ValueError("cursor exceeds result count")
        end = min(len(matches), cursor + limit)
        page = matches[cursor:end]
        return {
            "queries": list(cleaned),
            "match_mode": match_mode,
            "path": path,
            "matches": page,
            "next_cursor": end if end < len(matches) else None,
            "truncated": end < len(matches),
        }

    def add_note(self, filename: str, note: str) -> dict:
        if len(filename.encode("utf-8")) > 128:
            raise ValueError("memory note filename must be at most 128 bytes")
        # The native backend permits a hyphen-first slug; the older schemars
        # regex hint was narrower than its actual handler validation.
        if not re.fullmatch(
            AD_HOC_FILENAME_PATTERN.replace("[a-z0-9][a-z0-9-]{0,79}", "[a-z0-9-]{1,80}"), filename
        ):
            raise ValueError("memory note filename must be YYYY-MM-DDTHH-MM-SS-<slug>.md")
        if not note.strip():
            raise ValueError("memory note must not be empty")
        content = note.encode("utf-8")
        # Validate only this operation's directories. A pre-existing non-directory
        # is a semantic path error; a failed mkdir/open/write is storage I/O.
        # This rejects existing redirected ancestors, not adversarial rename races.
        directory = self.root
        for component in (None, "extensions", "ad_hoc", "notes"):
            if component is not None:
                directory /= component
            _ensure_note_directory(directory)
        target = directory / filename
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise MemoryNoteExistsError(f"ad-hoc note '{filename}' already exists") from exc
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        return {"path": target.relative_to(self.root).as_posix()}

    def _resolve(self, relative: str | None) -> Path:
        if relative in {None, ""}:
            return self.root
        logical = PurePosixPath(relative)
        if logical.is_absolute() or any(part in {"", ".", ".."} for part in logical.parts):
            raise MemoryPathError("memory path must stay within the memory root")
        if any(part.startswith(".") for part in logical.parts):
            raise MemoryNotFoundError(relative)
        current = self.root
        for index, part in enumerate(logical.parts):
            current = current / part
            metadata = _metadata_or_none(current)
            if metadata is None:
                return current.joinpath(*logical.parts[index + 1 :])
            _reject_symlink(metadata)
            if index + 1 < len(logical.parts) and not stat.S_ISDIR(metadata.st_mode):
                raise MemoryPathError("memory path traverses a non-directory component")
        return current


def _metadata_or_none(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except ValueError as exc:
        # Python rejects embedded NUL before the syscall. Rust reports the same
        # path conversion as io::ErrorKind::InvalidInput, not a domain path error.
        raise OSError(errno.EINVAL, str(exc)) from exc


def _reject_symlink(metadata: os.stat_result) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise MemoryPathError("memory paths must not traverse symbolic links")


def _required_metadata(path: Path, relative: str | None) -> os.stat_result:
    metadata = _metadata_or_none(path)
    if metadata is None:
        raise MemoryNotFoundError(relative or "")
    _reject_symlink(metadata)
    return metadata


def _sorted_entries(directory: Path) -> list[tuple[Path, os.stat_result]]:
    # Path.rglob can silently swallow directory I/O failures. Only disappearance
    # at these enumeration points is ignorable under the native backend contract.
    try:
        iterator = os.scandir(directory)
    except FileNotFoundError:
        return []
    with iterator:
        entries = []
        for entry in iterator:
            try:
                metadata = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            entries.append((Path(entry.path), metadata))
    return sorted(entries, key=lambda entry: entry[0])


def _search_files(start: Path, metadata: os.stat_result) -> Iterator[Path]:
    if stat.S_ISREG(metadata.st_mode):
        yield start
        return
    if not stat.S_ISDIR(metadata.st_mode):
        return
    pending = [start]
    while pending:
        for path, _ in _sorted_entries(pending.pop()):
            if path.name.startswith("."):
                continue
            current = _metadata_or_none(path)
            if current is None or stat.S_ISLNK(current.st_mode):
                continue
            if stat.S_ISDIR(current.st_mode):
                pending.append(path)
            elif stat.S_ISREG(current.st_mode):
                yield path


def _ensure_note_directory(path: Path) -> None:
    metadata = _metadata_or_none(path)
    if metadata is None:
        path.mkdir(mode=0o700)
        metadata = _required_metadata(path, str(path))
    _reject_symlink(metadata)
    if not stat.S_ISDIR(metadata.st_mode):
        raise MemoryPathError(f"managed memory path must be a regular directory: {path}")


def _prepare(value: str, *, case_sensitive: bool, normalized: bool) -> str:
    value = value if case_sensitive else value.lower()
    if normalized:
        value = "".join(char for char in value if char.isalnum())
    return value


def _search_lines(content: str) -> list[str]:
    """Rust str.lines: LF or CRLF terminators, no extra empty line after final LF."""
    if not content:
        return []
    parts = content.split("\n")
    terminated = [line.removesuffix("\r") for line in parts[:-1]]
    return terminated + ([parts[-1]] if parts[-1] else [])


def _matching_windows(
    lines: list[str], queries: list[str], mode: str, within_lines: int
) -> list[tuple[int, int]]:
    matches: list[tuple[int, int]] = []
    if mode in {"any", "all_on_same_line"}:
        for index, line in enumerate(lines):
            flags = [query in line for query in queries]
            if any(flags) if mode == "any" else all(flags):
                matches.append((index, index))
        return matches
    for first in range(len(lines)):
        for last in range(first, min(len(lines), first + within_lines)):
            if all(any(query in value for value in lines[first : last + 1]) for query in queries):
                matches.append((first, last))
                break
    # Remove windows that strictly contain a more precise match.
    return [
        value
        for index, value in enumerate(matches)
        if not any(
            index != other_index
            and value[0] <= other[0]
            and value[1] >= other[1]
            and value != other
            for other_index, other in enumerate(matches)
        )
    ]
