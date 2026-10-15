"""Temporary references bridge retirement of old and admission of new MCP views."""

import asyncio

from corki.mcp.connection import MCPConnection
from corki.mcp.startup import MCPPreparation


class PendingStartupClaims:
    """Hold pending and ready-but-unpublished owners until successor admission."""

    def __init__(self, generation: MCPPreparation | None, *, force_reconnect: bool):
        self.views: dict[str, MCPConnection] = {}
        if generation is None or force_reconnect:
            return
        for name, connection in {
            **generation.pending_connections,
            **generation.staged,
        }.items():
            task = generation.tasks[name]
            if (
                name in generation.staged
                and not connection.closed
                or not task.done()
                and not task.cancelling()
                and not generation.is_dormant(name)
                and connection.startup_pending
            ):
                self.views[name] = connection.fork(connection.settings, connection.metadata)

    def cancel_startup(self) -> None:
        """Retirement must not hide in-flight physical work from an explicit stop."""
        for view in self.views.values():
            view.cancel_startup()

    async def release(self) -> None:
        """Join all releases even if the replacing capture is repeatedly cancelled."""
        if not self.views:
            return

        async def close():
            # Connection callbacks retain cleanup errors on the owning manager.
            await asyncio.gather(
                *(view.aclose() for view in self.views.values()), return_exceptions=True
            )

        cleanup = asyncio.create_task(close(), name="mcp-startup-handoff-close")
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        cleanup.result()
        if interrupted:
            raise asyncio.CancelledError
