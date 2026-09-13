"""Feature gates and source directives do not require an installed engine."""

import asyncio

import pytest

from corki.code_mode.specs import parse_source
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.tools import ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "mode,disable,fail",
    [
        ("direct", False, False),
        ("code_mode", False, False),
        ("code_mode", True, True),
        ("code_mode_only", False, True),
    ],
)
def test_missing_engine_fallback_and_fail_closed_are_real_startup_paths(
    tmp_path, monkeypatch, mode, disable, fail
):
    async def scenario():
        monkeypatch.setattr("corki.code_mode.service.CodeModeService.available", lambda: False)
        closed, requests = [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert ("exec" in {spec.name for spec in request.tools}) is fail
                yield ModelCompleted(())

            async def aclose(self):
                closed.append(True)

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=mode,
                code_mode_disable_fallback=disable,
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted)
        finally:
            await runtime.aclose()
        assert closed == [True]

    asyncio.run(scenario())


def test_strict_control_collision_is_turn_failure_without_partial_publication(
    tmp_path, monkeypatch
):
    async def scenario():
        monkeypatch.setattr("corki.code_mode.service.CodeModeService.available", lambda: True)

        class UserTool:
            spec = ToolSpec("wait", "user fixture", {})

        class Model:
            async def aclose(self):
                pass

        registry = ToolRegistry()
        user_tool = UserTool()
        registry.register(user_tool)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode="code_mode",
                error_on_tool_collisions=True,
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnFailed)
            assert "tool collision: functions.wait" in events[-1].error
            assert registry.get("exec") is None and registry.get("wait") is user_tool
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "source",
    [
        "",
        " ",
        "// @exec: {}",
        "// @exec: []\ntext(1)",
        '// @exec: {"other":1}\ntext(1)',
        '// @exec: {"yield_time_ms":-1}\ntext(1)',
        '// @exec: {"max_output_tokens":true}\ntext(1)',
        '// @exec: {"yield_time_ms":9007199254740992}\ntext(1)',
    ],
)
def test_invalid_directive_is_rejected_before_creating_cell(source):
    with pytest.raises(ValueError):
        parse_source(source)


def test_directive_preserves_source_and_explicit_zero():
    assert parse_source(
        ' \t// @exec: {"yield_time_ms":0,"max_output_tokens":null}\r\ntext("原样");\n'
    ) == ('text("原样");\n', 0, 10000)
