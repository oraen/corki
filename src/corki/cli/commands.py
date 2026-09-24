"""Built-in CLI commands that do not require the agent runtime."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto

from corki.config import CorkiPaths, CorkiSettings


class CommandAction(Enum):
    """Side effects requested by a built-in command."""

    NONE = auto()
    PLAN = auto()
    MODEL = auto()
    MODEL_PICK = auto()
    CYCLE_MODE = auto()
    CLEAR = auto()
    COPY = auto()
    REALTIME_ON = auto()
    REALTIME_OFF = auto()
    MCP_REFRESH = auto()
    MCP_LIST = auto()
    COMPACT = auto()
    MEMORY_RESET = auto()
    MEMORY_RESET_PREVIEW = auto()
    MEMORY_MODE_ENABLED = auto()
    MEMORY_MODE_DISABLED = auto()


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of parsing and executing one slash command."""

    handled: bool
    output: str = ""
    action: CommandAction = CommandAction.NONE
    input_text: str = ""


class CommandDispatcher:
    """Handle local commands before messages are passed to the agent runtime."""

    def __init__(self, settings: CorkiSettings, paths: CorkiPaths) -> None:
        self._settings = settings
        self._paths = paths
        self._realtime_enabled = settings.realtime_enabled

    def set_realtime_enabled(self, enabled: bool) -> None:
        self._realtime_enabled = enabled

    def set_model_settings(self, snapshot) -> None:
        self._settings = replace(
            self._settings,
            model=snapshot.model,
            collaboration_mode=snapshot.collaboration_mode,
            reasoning_effort=snapshot.reasoning_effort,
        )

    def dispatch(self, message: str) -> CommandResult:
        """Return ``handled=False`` when ``message`` belongs to the agent."""

        command = message.strip().lower()
        parts = message.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() == "/model":
            model = parts[1].strip()
            if any(c.isspace() or not c.isprintable() for c in model):
                return CommandResult(handled=True, output="Usage: /model <model-name>")
            return CommandResult(handled=True, action=CommandAction.MODEL, input_text=model)
        if parts and parts[0].lower() == "/plan":
            return CommandResult(
                handled=True,
                action=CommandAction.PLAN,
                input_text=parts[1] if len(parts) == 2 else "",
            )
        # ``?`` mirrors Codex's footer shortcut even though it is the only
        # built-in command that deliberately has no leading slash.
        if command != "?" and not command.startswith("/"):
            return CommandResult(handled=False)

        if command in {"?", "/help", "/?"}:
            return CommandResult(
                handled=True,
                output=(
                    "Available commands:\n"
                    "  /help    show this help\n"
                    "  /status  show the current session configuration\n"
                    "  /clear   clear the terminal and redraw the header\n"
                    "  /copy    copy the last response, code block, or quote\n"
                    "  /model [name]  show or change the session model\n"
                    "  /plan [prompt]  enter Plan mode, optionally planning a request\n"
                    "  /compact summarize history in a standalone turn\n"
                    "  /memory reset  review explicit memory reset confirmation\n"
                    "  /memory mode enabled|disabled  set this thread's memory source eligibility\n"
                    "  /mcp     list discovered MCP tools\n"
                    "  /mcp refresh  reconnect before preparation or the next MCP call\n"
                    "  /realtime [on|off]  configure live turn steering\n"
                    "  /stop    stop an active realtime turn"
                ),
            )

        if command == "/status":
            return CommandResult(
                handled=True,
                output=(
                    f"Model:       {self._settings.model}\n"
                    f"Reasoning:   {self._settings.reasoning_effort or 'model default'}\n"
                    f"Mode:        {self._settings.collaboration_mode.title()}\n"
                    f"Directory:   {self._settings.working_directory}\n"
                    f"Corki home:  {self._paths.home}"
                ),
            )

        if command == "/clear":
            return CommandResult(handled=True, action=CommandAction.CLEAR)

        if parts and parts[0].lower() == "/copy":
            if len(parts) != 1:
                return CommandResult(handled=True, output="Usage: /copy")
            return CommandResult(handled=True, action=CommandAction.COPY)

        if command == "/compact":
            return CommandResult(handled=True, action=CommandAction.COMPACT)

        if command == "/memory reset":
            return CommandResult(
                handled=True,
                action=CommandAction.MEMORY_RESET_PREVIEW,
                output=(
                    "Reset deletes generated memory, notes, skills and memory job state. "
                    "No backup is created. "
                    "Conversations and memory settings remain; "
                    "future generation may rebuild memory. "
                    "To confirm, enter /memory reset confirm."
                ),
            )

        if command == "/memory reset confirm":
            return CommandResult(handled=True, action=CommandAction.MEMORY_RESET)

        if command in {"/memory mode enabled", "/memory mode disabled"}:
            return CommandResult(
                handled=True,
                action=(
                    CommandAction.MEMORY_MODE_ENABLED
                    if command.endswith(" enabled")
                    else CommandAction.MEMORY_MODE_DISABLED
                ),
            )

        if command == "/mcp":
            return CommandResult(handled=True, action=CommandAction.MCP_LIST)

        if command == "/mcp refresh":
            return CommandResult(
                handled=True,
                output=(
                    "MCP reconnect queued for preparation or MCP call admission; "
                    "running calls are not retried."
                ),
                action=CommandAction.MCP_REFRESH,
            )

        if command == "/model":
            return CommandResult(
                handled=True,
                action=CommandAction.MODEL_PICK,
                output=(
                    f"Current model: {self._settings.model}\n"
                    "Use /model <model-name> to change subsequent turns in this session. "
                    "The configured provider and global configuration are unchanged."
                ),
            )

        if command == "/realtime on":
            return CommandResult(
                handled=True,
                output="Realtime steering enabled for subsequent turns.",
                action=CommandAction.REALTIME_ON,
            )

        if command == "/realtime off":
            return CommandResult(
                handled=True,
                output="Realtime steering disabled.",
                action=CommandAction.REALTIME_OFF,
            )

        if command == "/realtime":
            state = "enabled" if self._realtime_enabled else "disabled"
            return CommandResult(
                handled=True,
                output=f"Realtime steering starts {state}; use /realtime on or /realtime off.",
            )

        return CommandResult(handled=True, output=f"Unknown command: {message.strip()}")
