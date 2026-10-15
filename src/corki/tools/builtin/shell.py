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
                "Runs a command, returning output or a session ID for ongoing interaction "
                "with write_stdin. Set tty to true to allocate a PTY."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command to execute."},
                    "workdir": {
                        "type": "string",
                        "description": "Working directory for the command. "
                        "Defaults to the turn cwd.",
                    },
                    "yield_time_ms": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": U64_MAX,
                        "description": (
                            "Wait before yielding output. Defaults to "
                            f"{exec_yield_ms(round(self.default_yield_seconds * 1000))} ms; "
                            "clamped to 250–30000 ms "
                            "(Windows initial floor 10000 ms)."
                        ),
                    },
                    "max_output_tokens": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": U64_MAX,
                        "description": "Output token budget. Defaults to 10000 tokens; "
                        "larger requests may be capped by policy.",
                    },
                    "tty": {
                        "type": "boolean",
                        "description": "True allocates a PTY for the command; false or omitted "
                        "uses plain pipes.",
                    },
                    "login": {
                        "type": "boolean",
                        "description": (
                            "True runs the shell with -l/-i semantics; false disables them. "
                            "Defaults to true."
                            if self.allow_login_shell
                            else "Login shells are disabled by config; omit this parameter "
                            "or set it to false."
                        ),
                    },
                    "shell": {
                        "type": "string",
                        "description": "Select a shell type by name or path. Defaults to the "
                        "current turn's shell.",
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
        requested = round(self.default_yield_seconds * 1000)
        write_default = stdin_yield_ms(requested, empty=False, maximum=self.max_yield_time_ms)
        poll_default = stdin_yield_ms(requested, empty=True, maximum=self.max_yield_time_ms)
        return ToolSpec(
            name="write_stdin",
            description=(
                "Writes characters to an existing exec_command session and returns recent output."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Identifier of the running exec_command session.",
                    },
                    "chars": {
                        "type": "string",
                        "description": "Bytes to write to stdin. Defaults to empty, which polls "
                        "without writing.",
                    },
                    "max_output_tokens": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Output token budget. Defaults to 10000 tokens; "
                        "larger requests may be capped by policy.",
                    },
                    "yield_time_ms": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": U64_MAX,
                        "description": (
                            "Wait before yielding output. Non-empty writes default to "
                            f"{write_default} ms "
                            "and clamp to 250–30000 ms. Empty polls default to "
                            f"{poll_default} ms "
                            "and clamp to 5000 ms through the configured background "
                            f"maximum ({max(5000, self.max_yield_time_ms)} ms)."
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
