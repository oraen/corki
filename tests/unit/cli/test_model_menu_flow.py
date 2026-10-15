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
            "local(default)",
            "More reasoning…",
            model_picker.BACK,
            model_picker.BACK,
            "local(default)",
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
    assert calls[4][2]["initial"] == "local(default)"
    assert (
        result is None
        if cancel
        else result == model_picker.ModelSelection("local", "medium", "default")
    )


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
    assert result == model_picker.ModelSelection("new", "medium", "default")


def test_model_menu_sorts_pairs_and_uses_selected_provider_metadata(tmp_path, monkeypatch):
    import json

    from corki.core.model_settings import capture_model_settings

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "default": {"provider": "z", "model": "shared"},
                "providers": {
                    "z": {
                        "models": {
                            "shared": {"supported_reasoning_levels": [{"effort": "low"}]},
                            "aaa": {},
                        }
                    },
                    "a": {
                        "models": {"shared": {"supported_reasoning_levels": [{"effort": "high"}]}}
                    },
                },
            }
        )
    )
    settings = CorkiSettings.for_directory(tmp_path, config_file=path)

    async def choose(session, current, choices, **kwargs):
        assert choices == ("aaa(z)", "shared(a)", "shared(z)")
        assert current == "shared(z)"
        return "shared(a)"

    monkeypatch.setattr(model_picker, "choose_model", choose)
    result = asyncio.run(
        model_picker.choose_model_and_effort(None, settings, capture_model_settings(settings))
    )
    assert result == model_picker.ModelSelection("shared", "high", "a")
