"""Codex-style command execution and resumable stdin tools."""

from __future__ import annotations

from dataclasses import dataclass

from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.shell import model_shell
from corki.tools.base import ToolContext
from corki.tools.builtin.process import ProcessManager
from corki.tools.builtin.shell_output import shell_result
from corki.tools.builtin.shell_policy import (
    DEFAULT_BACKGROUND_TERMINAL_MAX_TIMEOUT,
    U64_MAX,
    exec_yield_ms,
    stdin_yield_ms,
)


@dataclass(slots=True)
class ExecCommandTool:
    process_manager: ProcessManager
    default_yield_seconds: float
    timeout_seconds: float | None = None
    allow_login_shell: bool = True

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
                    "yield_time_ms": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": U64_MAX,
                        "description": (
                            "Wait before yielding; clamped to 250–30000 ms "
                            "(Windows initial floor 10000 ms)."
                        ),
                    },
                    "max_output_tokens": {"type": "integer", "minimum": 0, "maximum": U64_MAX},
                    "tty": {"type": "boolean"},
                    "login": {"type": "boolean"},
                    "shell": {
                        "type": "string",
                        "description": "Select a shell type by name or path.",
                    },
                    "sandbox_permissions": {
                        "type": "string",
                        "enum": ["use_default", "require_escalated"],
                        "description": (
                            "Per-command sandbox override. Defaults to use_default; "
                            "use require_escalated to request unsandboxed execution."
                        ),
                    },
                    "justification": {
                        "type": "string",
                        "description": (
                            "User-facing approval question; requires explicit sandbox_permissions."
                        ),
                    },
                    "prefix_rule": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Suggested reusable approval prefix for cmd; "
                            "does not grant or persist authority."
                        ),
                    },
                },
                "required": ["cmd"],
                "additionalProperties": False,
            },
            # The model may request independent commands in one step. Match
            # Codex's handler declaration; exclusive tools still form barriers.
            concurrency=ToolConcurrency.PARALLEL,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        assert call.arguments is not None
        sandbox_permissions = call.arguments.get("sandbox_permissions")
        justification = call.arguments.get("justification")
        if justification is not None and sandbox_permissions is None:
            raise ValueError(
                "`justification` requires an explicit `sandbox_permissions`; use "
                '`sandbox_permissions: "require_escalated"` for unsandboxed execution, '
                "or omit `justification`."
            )
        login = call.arguments.get("login", self.allow_login_shell)
        if login and not self.allow_login_shell:
            raise ValueError("login shell is disabled by config; omit `login` or set it to false.")
        requested_shell = call.arguments.get("shell")
        command = str(call.arguments["cmd"])
        workdir = context.cwd
        if requested := call.arguments.get("workdir"):
            candidate = (context.cwd / str(requested)).resolve()
            if not candidate.is_dir():
                raise ValueError(f"workdir is not a directory: {candidate}")
            workdir = candidate
        yield_seconds = (
            exec_yield_ms(
                call.arguments.get("yield_time_ms", round(self.default_yield_seconds * 1000))
            )
            / 1000
        )
        observation = await self.process_manager.execute(
            command,
            cwd=workdir,
            yield_seconds=yield_seconds,
            timeout_seconds=self.timeout_seconds,
            tty=bool(call.arguments.get("tty", False)),
            login=bool(login),
            shell=model_shell(requested_shell) if requested_shell is not None else context.shell,
            item_id=str(call.id),
            permissions=context.execution_permissions,
            write_stdin_approval=context.write_stdin_approval,
            terminal_policy_cwd=context.cwd,
            sandbox_permissions=sandbox_permissions or "use_default",
            justification=justification,
            prefix_rule=call.arguments.get("prefix_rule"),
            honor_allow_prefix_rules=context.honor_exec_policy_allow_rules,
            on_warning=context.on_warning,
        )
        return shell_result(call, observation, context)


@dataclass(slots=True)
class WriteStdinTool:
    process_manager: ProcessManager
    default_yield_seconds: float = 0.25
    max_yield_time_ms: int = DEFAULT_BACKGROUND_TERMINAL_MAX_TIMEOUT

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
                    "yield_time_ms": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": U64_MAX,
                        "description": (
                            "Default 250 ms; non-empty writes clamp to 250–30000 ms, "
                            "empty polls to 5000 ms through the configured background "
                            "maximum (default 300000 ms)."
                        ),
                    },
                },
                "required": ["session_id"],
                "additionalProperties": False,
            },
            # Different terminals may overlap; ProcessManager serializes
            # interactions with the same terminal's draining output buffer.
            concurrency=ToolConcurrency.PARALLEL,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        assert call.arguments is not None
        chars = str(call.arguments.get("chars", ""))
        yield_seconds = (
            stdin_yield_ms(
                call.arguments.get("yield_time_ms", round(self.default_yield_seconds * 1000)),
                empty=not chars,
                maximum=self.max_yield_time_ms,
            )
            / 1000
        )
        observation = await self.process_manager.write_stdin(
            str(call.arguments["session_id"]),
            chars,
            yield_seconds=yield_seconds,
            permissions=context.execution_permissions,
            policy_cwd=context.cwd,
            call_id=str(call.id),
            review_enabled=context.write_stdin_approval,
        )
        return shell_result(call, observation, context)
