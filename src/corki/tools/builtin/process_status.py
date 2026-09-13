"""Project asyncio process status onto Codex's default pipe/portable-PTY paths."""

import os


def observation_exit_code(
    returncode: int | None, *, tty: bool, unix: bool | None = None
) -> int | None:
    is_unix = os.name != "nt" if unix is None else unix
    if returncode is None or returncode >= 0 or not is_unix:
        return returncode
    # asyncio encodes signal death as -signal. The pinned default portable-pty
    # status conversion reports 1; Codex's pipe helper instead uses 128 + signal.
    # This is not the inherited-fd PTY path, which Corki does not expose.
    return 1 if tty else 128 - returncode
