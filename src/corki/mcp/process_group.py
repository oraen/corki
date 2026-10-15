"""One newly spawned POSIX MCP group, with source-style independent escalation."""

import logging
import signal
import threading
from collections.abc import Callable

from corki.tools.builtin.process_groups import signal_owned_group

logger = logging.getLogger(__name__)
TERM_GRACE_SECONDS = 2.0


def _kill(group_id: int, send: Callable[[int, int], bool]) -> None:
    try:
        send(group_id, signal.SIGKILL)
    except OSError:
        logger.warning("Failed to kill MCP process group %s", group_id, exc_info=True)


class MCPProcessGroup:
    """Retain the spawn-time group, never resolve an arbitrary PID's current group."""

    def __init__(self, group_id: int) -> None:
        if type(group_id) is not int or not 0 < group_id <= 2**31 - 1:
            raise ValueError("invalid owned MCP process group")
        self.group_id = group_id
        self._terminated = False
        self._timer: threading.Timer | None = None

    def terminate(self) -> None:
        if self._terminated:
            return
        self._terminated = True
        try:
            exists = signal_owned_group(self.group_id, signal.SIGTERM)
        except OSError:
            logger.warning("Failed to terminate MCP process group %s", self.group_id, exc_info=True)
            return
        if exists:
            # A cancelled asyncio waiter or an exited leader must not cancel escalation.
            self._timer = threading.Timer(
                TERM_GRACE_SECONDS, _kill, args=(self.group_id, signal_owned_group)
            )
            self._timer.daemon = True
            self._timer.start()
