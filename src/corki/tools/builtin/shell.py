"""Codex-style command execution and resumable stdin tools."""

from __future__ import annotations

from dataclasses import dataclass

from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools.base import ToolContext
from corki.tools.builtin.process import ProcessManager, ProcessObservation


@dataclass(slots=True)
class ExecCommandTool:
    process_manager: ProcessManager
    default_yield_seconds: float
    timeout_seconds: float

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="exec_command",
            description=(
                "Run a shell command in the workspace. If it is still running, returns a "
                "session_id that can be passed to write_stdin."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command to execute."},
                    "workdir": {"type": "string", "description": "Working directory."},
                    "yield_time_ms": {"type": "integer", "minimum": 50, "maximum": 30000},
                    "max_output_tokens": {"type": "integer", "minimum": 1, "maximum": 100000},
                    "tty": {"type": "boolean"},
                    "login": {"type": "boolean"},
                },
                "required": ["cmd"],
                "additionalProperties": False,
            },
            # Shell commands can mutate shared workspace state. Serializing
            # them is the safe default; genuinely read-only tools opt in to
            # parallel execution explicitly.
            concurrency=ToolConcurrency.EXCLUSIVE,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        assert call.arguments is not None
        command = str(call.arguments["cmd"])
        workdir = context.cwd
        if requested := call.arguments.get("workdir"):
            candidate = (context.cwd / str(requested)).resolve()
            if not candidate.is_dir():
                raise ValueError(f"workdir is not a directory: {candidate}")
            workdir = candidate
        yield_seconds = (
            float(call.arguments.get("yield_time_ms", self.default_yield_seconds * 1000)) / 1000
        )
        observation = await self.process_manager.execute(
            command,
            cwd=workdir,
            yield_seconds=yield_seconds,
            timeout_seconds=self.timeout_seconds,
            tty=bool(call.arguments.get("tty", False)),
            login=bool(call.arguments.get("login", True)),
            max_output_bytes=int(call.arguments.get("max_output_tokens", 10000)) * 4,
        )
        return _result(call, observation)


@dataclass(slots=True)
class WriteStdinTool:
    process_manager: ProcessManager
    default_yield_seconds: float

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="write_stdin",
            description=(
                "Write characters to a running exec_command session, or poll it with empty chars."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "chars": {"type": "string"},
                    "yield_time_ms": {"type": "integer", "minimum": 50, "maximum": 30000},
                },
                "required": ["session_id"],
                "additionalProperties": False,
            },
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        assert call.arguments is not None
        yield_seconds = (
            float(call.arguments.get("yield_time_ms", self.default_yield_seconds * 1000)) / 1000
        )
        observation = await self.process_manager.write_stdin(
            str(call.arguments["session_id"]),
            str(call.arguments.get("chars", "")),
            yield_seconds=yield_seconds,
        )
        return _result(call, observation)


def _result(call: ToolCall, observation: ProcessObservation) -> ToolResult:
    status = (
        f"Process running with session ID {observation.session_id}."
        if observation.session_id
        else f"Process exited with code {observation.exit_code}."
    )
    if observation.timed_out:
        status += " Timed out."
    content = (
        f"{status}\nWall time: {observation.wall_time_seconds:.3f}s\nOutput:\n{observation.output}"
    )
    return ToolResult(
        call_id=call.id,
        tool_name=call.name,
        content=content,
        display_content=observation.output.strip()
        or (
            f"process running: {observation.session_id}"
            if observation.session_id
            else f"exit code {observation.exit_code}"
        ),
        is_error=observation.timed_out,
    )
