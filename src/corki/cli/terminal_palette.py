"""One bounded default-color query owned by the active prompt application."""

import asyncio

from corki.cli.terminal_responses import frame_terminal_responses


class TerminalPalette:
    def __init__(self):
        self.attempted = False
        self.foreground = self.background = None

    @property
    def light(self):
        if self.background is None:
            return False
        r, g, b = self.background
        return 0.299 * r + 0.587 * g + 0.114 * b > 128

    def attach(self, app):
        app.before_render += self._start

    def _start(self, app):
        if self.attempted:
            return
        self.attempted = True
        parser = frame_terminal_responses(app.input)
        stdin = getattr(app.input, "stdin", None)
        stdout = getattr(app.output, "stdout", None)
        if parser is None or stdin is None or stdout is None:
            return
        try:
            if not stdin.isatty() or not stdout.isatty():
                return
        except (OSError, ValueError):
            return
        # before_render runs inside the app's raw-mode/input ownership scope.
        # Its background tasks are cancelled and joined when that scope exits.
        app.create_background_task(self._sample(app, parser))

    async def _sample(self, app, parser):
        ready = asyncio.Event()
        colors = {}

        def received(slot, rgb):
            colors[slot] = rgb
            if 10 in colors and 11 in colors:
                ready.set()

        parser.color_listeners.add(received)
        try:
            async with asyncio.timeout(0.1):
                app.output.write_raw("\x1b]10;?\x1b\\\x1b]11;?\x1b\\")
                app.output.flush()
                await ready.wait()
            self.foreground, self.background = colors[10], colors[11]
            app.invalidate()
        except (TimeoutError, OSError, ValueError):
            # Unsupported terminals and failed optional probes keep dark fallback.
            pass
        finally:
            parser.color_listeners.discard(received)
