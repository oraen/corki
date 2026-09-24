import asyncio
from types import SimpleNamespace

import pytest

from corki.cli import model_picker
from corki.config import CorkiSettings
from corki.protocol.context import ModelContextInfo


@pytest.mark.parametrize("cancel", [False, True])
def test_advanced_backtracks_without_publishing(tmp_path, monkeypatch, cancel):
    settings = CorkiSettings(
        tmp_path,
        model="local",
        reasoning_effort="high",
        model_contexts=(
            ModelContextInfo(
                "local",
                supported_reasoning_levels=("low", "medium", "high", "max", "ultra"),
                default_reasoning_level="medium",
            ),
        ),
    )
    replies = iter(
        [
            "local",
            "More reasoning…",
            model_picker.BACK,
            model_picker.BACK,
            "local",
            None if cancel else "medium",
        ]
    )
    calls = []

    async def choose(session, current, choices, **kwargs):
        calls.append((current, choices, kwargs))
        assert settings.reasoning_effort == "high"
        return next(replies)

    monkeypatch.setattr(model_picker, "choose_model", choose)
    result = asyncio.run(model_picker.choose_model_and_effort(None, settings, settings))
    assert calls[1][0] == "high"
    assert calls[1][1] == ("low", "medium", "high", "More reasoning…")
    assert calls[1][2]["default_choice"] == "medium"
    assert calls[2][1] == ("max", "ultra")
    assert calls[3][2]["initial"] == "More reasoning…"
    assert calls[4][2]["initial"] == "local"
    assert result is None if cancel else result == model_picker.ModelSelection("local", "medium")


def test_new_model_highlights_its_default_not_old_effort(tmp_path, monkeypatch):
    settings = CorkiSettings(
        tmp_path,
        model_contexts=(
            ModelContextInfo(
                "new",
                supported_reasoning_levels=("low", "medium", "high"),
                default_reasoning_level="medium",
            ),
        ),
    )
    calls = []

    async def choose(session, current, choices, **kwargs):
        calls.append((current, kwargs))
        return "new" if len(calls) == 1 else "medium"

    monkeypatch.setattr(model_picker, "choose_model", choose)
    result = asyncio.run(
        model_picker.choose_model_and_effort(
            None, settings, SimpleNamespace(model="old", reasoning_effort="high")
        )
    )
    assert calls[1][0] is None
    assert calls[1][1]["initial"] == "medium"
    assert result == model_picker.ModelSelection("new", "medium")
