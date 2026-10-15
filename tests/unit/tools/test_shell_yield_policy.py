import asyncio

import pytest

from corki.config import CorkiSettings
from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import ToolCall
from corki.tools.base import ToolContext
from corki.tools.builtin.process import ProcessObservation
from corki.tools.builtin.shell import ExecCommandTool, WriteStdinTool
from corki.tools.builtin.shell_policy import exec_yield_ms, stdin_yield_ms
from corki.tools.executor import _validate


@pytest.mark.parametrize("seconds", [0.25, 10, 45])
@pytest.mark.parametrize("login", [False, True])
def test_exec_description_matches_configured_defaults(seconds, login):
    tool = ExecCommandTool(None, seconds, allow_login_shell=login)
    properties = tool.spec.parameters["properties"]
    assert (
        f"Defaults to {exec_yield_ms(round(seconds * 1000))} ms"
        in (properties["yield_time_ms"]["description"])
    )
    assert ("Defaults to true." in properties["login"]["description"]) == login
    assert ("disabled by config" in properties["login"]["description"]) != login
    assert "false or omitted uses plain pipes" in properties["tty"]["description"]
    assert "Defaults to 10000 tokens" in properties["max_output_tokens"]["description"]


@pytest.mark.parametrize("seconds", [0.25, 10, 45])
@pytest.mark.parametrize("maximum", [0, 11000, 300000])
def test_stdin_description_matches_effective_wait_bounds(seconds, maximum):
    properties = WriteStdinTool(None, seconds, maximum).spec.parameters["properties"]
    description = properties["yield_time_ms"]["description"]
    requested = round(seconds * 1000)
    write = stdin_yield_ms(requested, empty=False, maximum=maximum)
    poll = stdin_yield_ms(requested, empty=True, maximum=maximum)
    assert f"Non-empty writes default to {write} ms" in description
    assert f"Empty polls default to {poll} ms" in description
    assert f"maximum ({max(5000, maximum)} ms)" in description
    assert properties["session_id"]["type"] == "string"
    assert "polls without writing" in properties["chars"]["description"]


@pytest.mark.parametrize(
    "kind,requested,expected",
    [
        ("exec", 0, 0.25),
        ("exec", 1000000, 30),
        ("empty", 0, 5),
        ("empty", 1000000, 300),
        ("input", 0, 0.25),
        ("input", 1000000, 30),
    ],
)
def test_yield_admission_clamps_instead_of_rejecting(tmp_path, kind, requested, expected):
    captured = []

    class Manager:
        async def execute(self, *args, **kwargs):
            captured.append(kwargs["yield_seconds"])
            return ProcessObservation("", 0, None)

        write_stdin = execute

    tool = (
        ExecCommandTool(Manager(), 10, 120) if kind == "exec" else WriteStdinTool(Manager(), 0.25)
    )
    args = {"cmd": "unused"} if kind == "exec" else {"session_id": "fixture"}
    if kind == "input":
        args["chars"] = "input"
    args["yield_time_ms"] = requested
    _validate(args, tool.spec.parameters, path="arguments")
    asyncio.run(
        tool.execute(ToolCall(new_tool_call_id(), tool.spec.name, args), ToolContext(tmp_path))
    )
    assert captured == [expected]


@pytest.mark.parametrize(
    "requested,expected", [(0, 10000), (5000, 10000), (20000, 20000), (40000, 30000)]
)
def test_windows_initial_floor_only(requested, expected):
    assert exec_yield_ms(requested, windows=True) == expected
    assert stdin_yield_ms(requested, empty=False, maximum=300000) == min(30000, max(250, requested))


@pytest.mark.parametrize("requested", [-1, True, 1.0, "100", 2**64])
def test_unsigned_integer_admission_is_enforced(requested):
    with pytest.raises(ValueError):
        exec_yield_ms(requested)
    with pytest.raises(ValueError):
        stdin_yield_ms(requested, empty=True, maximum=300000)


@pytest.mark.parametrize("maximum", [0, 4000, 5000, 11000, 2**64 - 1])
def test_config_background_window_loads_and_floors(tmp_path, maximum):
    config = tmp_path / "config.toml"
    config.write_text(f"[tools]\nbackground_terminal_max_timeout = {maximum}\n")
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.background_terminal_max_timeout == maximum
    assert stdin_yield_ms(2**64 - 1, empty=True, maximum=maximum) == max(5000, maximum)


@pytest.mark.parametrize("maximum", [-1, True, 1.0, "100", 2**64])
def test_config_background_window_rejects_invalid_values(tmp_path, maximum):
    with pytest.raises(ValueError, match="background_terminal_max_timeout"):
        CorkiSettings(working_directory=tmp_path, background_terminal_max_timeout=maximum)
