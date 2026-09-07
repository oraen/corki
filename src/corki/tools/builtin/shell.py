"""Codex-style command execution and resumable stdin tools."""

from __future__ import annotations

from dataclasses import dataclass

from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools.base import ToolContext
from corki.tools.builtin.process import ProcessManager
from corki.tools.builtin.shell_output import shell_result


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
                    "max_output_tokens": {"type": "integer", "minimum": 0},
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
        )
        return shell_result(call, observation, context)


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
                    "max_output_tokens": {"type": "integer", "minimum": 0},
                    "yield_time_ms": {"type": "integer", "minimum": 50, "maximum": 30000},
                },
                "required": ["session_id"],
                "additionalProperties": False,
            },
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        assert call.arguments is not None
        yield_seconds = (
            float(call.arguments.get("yield_time_ms", self.default_yield_seconds * 1000)) / 1000
        )
        observation = await self.process_manager.write_stdin(
            str(call.arguments["session_id"]),
            str(call.arguments.get("chars", "")),
            yield_seconds=yield_seconds,
        )
        return shell_result(call, observation, context)
