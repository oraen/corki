"""Built-in CLI commands that do not require the agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from corki.config import CorkiPaths, CorkiSettings


class CommandAction(Enum):
    """Side effects requested by a built-in command."""

    NONE = auto()
    CLEAR = auto()
    REALTIME_ON = auto()
    REALTIME_OFF = auto()
    MCP_REFRESH = auto()
    COMPACT = auto()


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of parsing and executing one slash command."""

    handled: bool
    output: str = ""
    action: CommandAction = CommandAction.NONE


class CommandDispatcher:
    """Handle local commands before messages are passed to the agent runtime."""

    def __init__(self, settings: CorkiSettings, paths: CorkiPaths) -> None:
        self._settings = settings
        self._paths = paths
        self._realtime_enabled = settings.realtime_enabled

    def set_realtime_enabled(self, enabled: bool) -> None:
        self._realtime_enabled = enabled

    def dispatch(self, message: str) -> CommandResult:
        """Return ``handled=False`` when ``message`` belongs to the agent."""

        command = message.strip().lower()
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
                    "  /model   show how to configure the current model\n"
                    "  /compact summarize history in a standalone turn\n"
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
                    f"Directory:   {self._settings.working_directory}\n"
                    f"Corki home:  {self._paths.home}"
                ),
            )

        if command == "/clear":
            return CommandResult(handled=True, action=CommandAction.CLEAR)

        if command == "/compact":
            return CommandResult(handled=True, action=CommandAction.COMPACT)

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
                output=(
                    f"Current model: {self._settings.model}\n"
                    "Set [agent].model in ~/.corki/config.toml or CORKI_MODEL before startup."
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
