"""Connection-owned helper cache and source-bounded authentication refresh."""

import asyncio
import weakref
from pathlib import Path

import httpx

from corki.mcp.active_time import ActiveTime
from corki.mcp.auth_challenge import insufficient_scope
from corki.mcp.header_helper_process import run_helper
from corki.mcp.http_stream import owned_response


def _consume(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


class _State:
    def __init__(self, command: str, cwd: Path):
        self.command, self.cwd = command, cwd
        self.epoch = 0
        self.current: asyncio.Task | None = None
        self.refresh: asyncio.Task | None = None
        self.tasks: set[asyncio.Task] = set()
        self.closed = False

    def attempt(self):
        task = asyncio.create_task(run_helper(self.command, self.cwd), name="mcp-header-helper")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(_consume)
        return task

    def cancel(self):
        self.closed = True
        for task in tuple(self.tasks):
            task.cancel()


def _origin(url: str):
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.raw_host:
        return None
    port = parsed.port if parsed.port is not None else 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.raw_host, port


class HeaderHelper:
    def __init__(self, url: str, command: str, cwd: Path):
        self.origin = _origin(url)
        if self.origin is None:
            raise ValueError("Invalid HTTP headers helper server origin")
        self._state = _State(command, cwd)
        self._finalizer = weakref.finalize(self, self._state.cancel)
        self._close_task: asyncio.Task | None = None

    async def headers(self):
        state = self._state
        if state.closed:
            raise RuntimeError("HTTP headers helper is closed")
        if state.current is None:
            state.current = state.attempt()
        epoch, task = state.epoch, state.current
        return epoch, httpx.Headers(await asyncio.shield(task), encoding="utf-8")

    async def refresh(self, epoch: int):
        state = self._state
        if state.closed:
            raise RuntimeError("HTTP headers helper is closed")
        if state.epoch != epoch:
            return (await self.headers())[1]
        if state.refresh is None:
            state.refresh = state.attempt()
        task = state.refresh
        error = None
        try:
            result = await asyncio.shield(task)
        except Exception as exc:
            error = exc
        if state.epoch == epoch and state.refresh is task:
            state.epoch = min(2**64 - 1, epoch + 1)
            if error is None:
                state.current = task
            state.refresh = None
        if error is not None:
            raise error
        return httpx.Headers(result, encoding="utf-8")

    async def aclose(self):
        if self._close_task is None:
            tasks = tuple(self._state.tasks)
            self._finalizer()

            async def join():
                await asyncio.gather(*tasks, return_exceptions=True)

            self._close_task = asyncio.create_task(join(), name="mcp-header-helper-close")
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    @staticmethod
    def _apply(original: httpx.Headers, helper: httpx.Headers):
        result = httpx.Headers(original, encoding="utf-8")
        for name, value in helper.items():
            if name == "authorization" and name in original:
                continue
            result[name] = value
        return result

    async def request(
        self,
        method: str,
        url: str,
        headers: httpx.Headers,
        timeout: float,
        send,
        *,
        active_time: ActiveTime | None = None,
    ):
        if _origin(url) != self.origin:
            return await send(headers, timeout)
        clock = active_time or ActiveTime()
        deadline = clock.time() + timeout

        def remaining():
            return max(0.001, deadline - clock.time())

        async with clock.timeout(remaining()):
            epoch, original = await self.headers()
        effective = self._apply(headers, original)

        async def check_redirect(response, applied):
            if (
                self.origin[0] == "http"
                and "proxy-authorization" in applied
                and 300 <= response.status_code < 400
                and "location" in response.headers
            ):
                async with owned_response(response):
                    raise ValueError(
                        "MCP HTTP redirect cannot safely replay Proxy-Authorization credentials"
                    )

        response = await send(effective, remaining())
        await check_redirect(response, effective)
        if method.upper() != "POST" or response.status_code not in (401, 403):
            return response
        if response.status_code == 403 and any(
            insufficient_scope(v) for v in response.headers.get_list("www-authenticate")
        ):
            return response
        try:
            async with clock.timeout(remaining()):
                refreshed = await self.refresh(epoch)
        except Exception:
            return response
        explicit = "authorization" in headers

        def values(mapping):
            return {
                key: value
                for key, value in mapping.items()
                if not (explicit and key == "authorization")
            }

        if values(original) == values(refreshed):
            return response
        async with owned_response(response):
            pass
        effective = self._apply(headers, refreshed)
        result = await send(effective, remaining())
        await check_redirect(result, effective)
        return result
