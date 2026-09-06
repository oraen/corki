"""Scoped list/read/search operations over the local memory hierarchy."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from corki.context.tokens import estimate_text_tokens
from corki.memory.artifacts import ensure_memory_layout
from corki.memory.repository import MemoryRepository
from corki.protocol.ids import ThreadId

_THREAD_ID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


class MemoryPathError(ValueError):
    """Raised when a requested path could escape or confuse the memory root."""


class LocalMemoryBackend:
    """Codex-style progressive disclosure without a vector database."""

    def __init__(self, root: Path, repository: MemoryRepository | None = None) -> None:
        self.root = root
        self._repository = repository
        ensure_memory_layout(root)

    def list(self, path: str | None = None, *, cursor: int = 0, limit: int = 2_000) -> dict:
        target = self._resolve(path)
        if cursor < 0 or not 1 <= limit <= 2_000:
            raise ValueError("cursor must be non-negative and limit must be between 1 and 2000")
        if not target.exists():
            raise FileNotFoundError(path or "")
        if target.is_symlink():
            raise MemoryPathError("memory paths must not be symbolic links")
        paths = [target] if target.is_file() else sorted(target.iterdir(), key=lambda p: p.name)
        entries = [
            {
                "path": item.relative_to(self.root).as_posix(),
                "type": "directory" if item.is_dir() else "file",
            }
            for item in paths
            if not item.name.startswith(".")
            and not item.is_symlink()
            and (item.is_file() or item.is_dir())
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
        if line_offset < 1 or (max_lines is not None and max_lines < 1) or max_tokens < 1:
            raise ValueError("line_offset, max_lines, and max_tokens must be positive")
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if target.is_symlink() or not target.is_file():
            raise MemoryPathError("memory read target must be a regular file")
        content = target.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        if line_offset > max(1, len(lines)):
            raise ValueError("line_offset exceeds file length")
        selected = lines[line_offset - 1 :]
        truncated = False
        if max_lines is not None and len(selected) > max_lines:
            selected = selected[:max_lines]
            truncated = True
        text = "".join(selected)
        text, token_truncated = _truncate_tokens(text, max_tokens)
        await self._record_usage(text)
        return {
            "path": path,
            "start_line_number": line_offset,
            "content": text,
            "truncated": truncated or token_truncated,
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
        if not start.exists():
            raise FileNotFoundError(path or "")
        files = [start] if start.is_file() else sorted(start.rglob("*"))
        matches: list[dict] = []
        for file in files:
            if (
                not file.is_file()
                or file.is_symlink()
                or any(part.startswith(".") for part in file.relative_to(self.root).parts)
            ):
                continue
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except UnicodeError:
                continue
            prepared_lines = [
                _prepare(value, case_sensitive=case_sensitive, normalized=normalized)
                for value in lines
            ]
            prepared_queries = [
                _prepare(value, case_sensitive=case_sensitive, normalized=normalized)
                for value in cleaned
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
        await self._record_usage("\n".join(item["content"] for item in page))
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
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]{0,79}\.md",
            filename,
        ):
            raise ValueError("memory note filename must be YYYY-MM-DDTHH-MM-SS-<slug>.md")
        if not note.strip():
            raise ValueError("memory note must not be empty")
        directory = self.root / "extensions" / "ad_hoc" / "notes"
        target = directory / filename
        with target.open("x", encoding="utf-8") as handle:
            handle.write(note.rstrip() + "\n")
        target.chmod(0o600)
        return {"path": target.relative_to(self.root).as_posix()}

    def _resolve(self, relative: str | None) -> Path:
        if relative in {None, ""}:
            return self.root
        logical = PurePosixPath(relative)
        if logical.is_absolute() or any(part in {"", ".", ".."} for part in logical.parts):
            raise MemoryPathError("memory path must stay within the memory root")
        if any(part.startswith(".") for part in logical.parts):
            raise FileNotFoundError(relative)
        current = self.root
        for index, part in enumerate(logical.parts):
            current = current / part
            if current.exists() and current.is_symlink():
                raise MemoryPathError("memory paths must not traverse symbolic links")
            if current.exists() and index + 1 < len(logical.parts) and not current.is_dir():
                raise MemoryPathError("memory path traverses a non-directory component")
        return current

    async def _record_usage(self, content: str) -> None:
        if self._repository is None:
            return
        ids = tuple(ThreadId(value) for value in dict.fromkeys(_THREAD_ID.findall(content)))
        if ids:
            await self._repository.mark_memories_used(ids)


def _prepare(value: str, *, case_sensitive: bool, normalized: bool) -> str:
    if normalized:
        value = re.sub(r"[\\/_-]+", " ", value)
        value = " ".join(value.split())
    return value if case_sensitive else value.casefold()


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


def _truncate_tokens(text: str, limit: int) -> tuple[str, bool]:
    if estimate_text_tokens(text) <= limit:
        return text, False
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_text_tokens(text[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "\n…truncated…\n", True
