import asyncio
import json

import pytest

from corki.cli import native_file_search, reference_completion
from corki.cli.reference_completion import FileSearchUnavailable


@pytest.mark.parametrize(
    "row",
    [
        {"path": "../escape", "directory": False, "score": 1},
        {"path": "/absolute", "directory": False, "score": 1},
        {"path": "bad\x1bname", "directory": False, "score": 1},
        {"path": "x", "directory": 1, "score": 1},
        {"path": "x", "directory": False, "score": True},
        {"path": "x", "directory": False, "score": -1},
        {"path": "x", "directory": False, "score": 2**32},
        {"path": ".", "directory": True, "score": 1},
        {"path": "x", "directory": False, "score": 1, "extra": "field"},
        {},
    ],
)
def test_malformed_candidates_are_not_selectable(row):
    with pytest.raises(ValueError):
        native_file_search.decode(json.dumps([row]))


@pytest.mark.parametrize("rows", [None, {}, [1], [dict(path="x", directory=True, score=1)] * 2])
def test_result_shape_and_duplicate_paths(rows):
    with pytest.raises(ValueError):
        native_file_search.decode(json.dumps(rows))


def test_unicode_paths_and_native_score_preserved():
    assert native_file_search.decode('[{"path":"中文 空目录","directory":true,"score":40}]') == (
        ("中文 空目录", True, 40),
    )


def test_native_failure_does_not_start_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(native_file_search, "executable", lambda: tmp_path / "helper")

    async def fail(*args):
        raise ValueError("private server path")

    def forbidden(*args):
        raise AssertionError("must not switch semantics after a helper error")

    monkeypatch.setattr(native_file_search, "search", fail)
    monkeypatch.setattr(reference_completion.shutil, "which", forbidden)
    with pytest.raises(FileSearchUnavailable, match="failed or exceeded") as error:
        asyncio.run(reference_completion.files(tmp_path, "a"))
    assert "private" not in str(error.value)
