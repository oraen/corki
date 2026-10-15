"""Suspend a menu prompt before lending its terminal to an approval reader."""

import asyncio
from dataclasses import dataclass

from corki.cli.input_owner import join_inputs


@dataclass
class MenuOverlay:
    read: object
    request: object
    result: asyncio.Future

    async def run(self):
        if self.result.done():
            return
        reader = asyncio.create_task(self.read(self.request), name="corki-menu-approval")

        def withdrawn(result):
            if result.cancelled() and not reader.done():
                reader.cancel()

        self.result.add_done_callback(withdrawn)
        try:
            value = await reader
            if not self.result.done():
                self.result.set_result(value)
        except asyncio.CancelledError:
            self.result.cancel()
            if asyncio.current_task().cancelling():
                raise
        except Exception as error:
            if not self.result.done():
                self.result.set_exception(error)
        finally:
            self.result.remove_done_callback(withdrawn)
            await join_inputs(reader)


class MenuOverlayHost:
    def __init__(self, session):
        self.session = session
        self.active = False
        self.pending: asyncio.Future | None = None

    def offer(self, read, request):
        app = self.session.app
        if not self.active or not app.is_running or app.is_done:
            return None
        result = asyncio.get_running_loop().create_future()
        self.pending = result
        # prompt_async must finish its input cleanup before the approval starts.
        app.exit(result=MenuOverlay(read, request, result))
        return result

    def close(self):
        self.active = False
        if self.pending is not None and not self.pending.done():
            self.pending.cancel()
