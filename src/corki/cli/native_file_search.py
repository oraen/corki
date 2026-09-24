"""Validated, owned calls to Corki's bundled local file candidate helper."""

import json
import os
from pathlib import Path, PurePath

from corki.execution.owned_process import run_owned


def executable():
    name = "corki-file-search.exe" if os.name == "nt" else "corki-file-search"
    path = Path(__file__).resolve().parents[1] / "_native" / "file_search" / name
    return path if path.is_file() else None


def decode(data):
    rows = json.loads(data)
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("invalid local search results")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "directory", "score"}:
            raise ValueError("invalid local search candidate")
        path, directory, score = row["path"], row["directory"], row["score"]
        if (
            not isinstance(path, str)
            or not path
            or len(path.encode("utf-8")) > 65536
            or PurePath(path).is_absolute()
            or ".." in PurePath(path).parts
            or path == "."
            or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in path)
            or type(directory) is not bool
            or type(score) is not int
            or not 0 <= score <= 2**32 - 1
            or path in seen
        ):
            raise ValueError("invalid local search candidate")
        seen.add(path)
        result.append((path, directory, score))
    return tuple(result)


async def search(binary, cwd, query):
    if not query:
        return ()
    if len(query) > 1000:
        raise ValueError("file search query exceeds its limit")
    raw = await run_owned(
        [str(binary)],
        json.dumps({"query": query, "limit": 100}, ensure_ascii=False).encode("utf-8"),
        cwd=cwd,
        output_limit=8_000_000,
        timeout=5,
    )
    return decode(raw)
