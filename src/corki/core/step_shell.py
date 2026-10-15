"""Restore trusted Step shell identity without treating prompt text as authority."""

from pathlib import Path

from corki.core.state import CorkiState
from corki.shell import Shell, ShellType
from corki.tools.errors import FatalToolError


def step_shell(state: CorkiState, session_shell: Shell) -> Shell:
    snapshot = state.get("shell_snapshot")
    if snapshot is None:
        # Old checkpoints did not retain executable identity. Never extract a
        # purported executable path from an old model-visible environment block.
        return session_shell
    if (
        not isinstance(snapshot, dict)
        or type(snapshot.get("version")) is not int
        or snapshot["version"] != 1
        or not isinstance(snapshot.get("kind"), str)
        or not isinstance(snapshot.get("path"), str)
        or not snapshot["path"]
        or "\0" in snapshot["path"]
    ):
        raise FatalToolError("invalid prepared shell snapshot")
    try:
        return Shell(ShellType(snapshot["kind"]), Path(snapshot["path"]))
    except ValueError as error:
        raise FatalToolError("invalid prepared shell snapshot") from error
