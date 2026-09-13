"""Own HTTP MCP generations, bounded lifecycle retries and session404 recovery."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Generic, TypeVar

import httpx

from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.request_policy import request_timeout

Session = TypeVar("Session")
RETRY_DELAYS = (0.250, 1.000)
TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
logger = logging.getLogger(__name__)


class BorrowedHttpTransport(httpx.AsyncBaseTransport):
    """A session borrows the injected carrier; only the outer owner closes it."""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._transport.handle_async_request(request)


def retryable(error: Exception) -> bool:
    """Never turn remote RPC/auth/schema failures into generic transport retries."""
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in TRANSIENT_STATUSES
    return isinstance(error, httpx.TransportError) and not isinstance(
        error, httpx.LocalProtocolError
    )


def session_expired(error: Exception) -> bool:
    """The sent request's session, not a subsequently published session, is authority."""
    return (
        isinstance(error, httpx.HTTPStatusError)
        and error.response.status_code == 404
        and error.request.extensions.get("corki_mcp_has_session") is True
    )


class HttpRecovery(Generic[Session]):
    """Publish only complete handshakes; old users retain their exact generation."""

    def __init__(
        self,
        initial: Session,
        factory: Callable[[], Session],
        initialize: Callable[[Session], Awaitable[None]],
        request: Callable[[Session, str, Mapping[str, Any]], Awaitable[Any]],
        close: Callable[[Session], Awaitable[None]],
        timeout: float,
        close_shared: Callable[[], Awaitable[None]] | None = None,
        prepare_auth: Callable[[Session], Awaitable[None]] | None = None,
    ) -> None:
        self.current = initial
        self.ready = False
        self.closed = False
        self._factory, self._initialize, self._request, self._close = (
            factory,
            initialize,
            request,
            close,
        )
        self._timeout = timeout
        self._close_shared = close_shared
        self._prepare_auth = prepare_auth
        self._lock = asyncio.Lock()
        self._sessions = {initial}
        self._users: dict[Session, int] = {}
        self._operations: dict[asyncio.Task, int] = {}
        self._retirements: dict[Session, asyncio.Task] = {}
        self._close_task: asyncio.Task | None = None

    @contextmanager
    def operation(self):
        if self.closed:
            raise MCPProtocolError("MCP client is shut down")
        task = asyncio.current_task()
        assert task is not None
        self._operations[task] = self._operations.get(task, 0) + 1
        try:
            yield
        finally:
            count = self._operations[task] - 1
            if count:
                self._operations[task] = count
            else:
                del self._operations[task]

    async def start(self) -> None:
        with self.operation():
            async with self._lock:
                if self.ready:
                    raise MCPProtocolError("MCP client already initialized")
                self.current = await self._connect(self.current)
                self.ready = True

    async def _connect(self, initial: Session | None = None) -> Session:
        async with asyncio.timeout(self._timeout) as budget:
            for attempt in range(len(RETRY_DELAYS) + 1):
                session = initial if initial is not None else self._factory()
                initial = None
                self._sessions.add(session)
                try:
                    if self._prepare_auth is not None:
                        # Credential transactions have their own bounded waits.
                        # Keep ownership, but exclude them from the handshake budget.
                        started = asyncio.get_running_loop().time()
                        deadline = budget.when()
                        budget.reschedule(None)
                        try:
                            await self._prepare_auth(session)
                        finally:
                            budget.reschedule(
                                deadline + asyncio.get_running_loop().time() - started
                            )
                    # Recovery uses the saved startup timeout, not a changed tool policy.
                    with request_timeout(
                        session, budget.when() - asyncio.get_running_loop().time()
                    ):
                        await self._initialize(session)
                    if self.closed:
                        raise MCPProtocolError("MCP client is shut down")
                    return session
                except BaseException as error:
                    try:
                        await self._close(session)
                    except asyncio.CancelledError:
                        raise
                    except Exception as cleanup_error:
                        logger.warning(
                            "MCP failed-handshake cleanup failed: %s", type(cleanup_error).__name__
                        )
                    else:
                        self._sessions.discard(session)
                    if (
                        not isinstance(error, Exception)
                        or not retryable(error)
                        or attempt == len(RETRY_DELAYS)
                    ):
                        raise
                    await asyncio.sleep(RETRY_DELAYS[attempt])
        raise AssertionError("unreachable handshake retry state")

    async def request(self, method: str, params: Mapping[str, Any], timeout: float) -> Any:
        with self.operation():
            params = deepcopy(dict(params))
            failed = self.current
            try:
                return await self._run(failed, method, params, timeout)
            except Exception as error:
                if not self.ready or not session_expired(error):
                    raise
            async with self._lock:
                if self.closed:
                    raise MCPProtocolError("MCP client is shut down")
                if self.current is failed:
                    replacement = await self._connect()
                    self.current = replacement
                    self._retire(failed)
            # Deliberately outside the first catch: never recursively recover a second404.
            return await self._run(self.current, method, params, timeout)

    async def _run(
        self, session: Session, method: str, params: Mapping[str, Any], timeout: float
    ) -> Any:
        with self.lease(session):
            if self._prepare_auth is not None:
                await self._prepare_auth(session)
            deadline = asyncio.get_running_loop().time() + timeout
            attempts = len(RETRY_DELAYS) + 1 if method == "tools/list" else 1
            for attempt in range(attempts):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    # The request owns an active-time budget. A human-paused attempt
                    # may finish after the wall-clock deadline; only retries consult it.
                    with request_timeout(session, remaining):
                        return await self._request(session, method, params)
                except Exception as error:
                    if attempt + 1 == attempts or not retryable(error):
                        raise
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError from error
                    async with asyncio.timeout(remaining):
                        await asyncio.sleep(RETRY_DELAYS[attempt])

    @contextmanager
    def lease(self, session: Session):
        """Requests and notifications both keep their admitted generation alive."""
        self._users[session] = self._users.get(session, 0) + 1
        try:
            yield session
        finally:
            self._users[session] -= 1
            if not self._users[session]:
                del self._users[session]
                if session is not self.current:
                    self._retire(session)

    def _retire(self, session: Session) -> None:
        if session in self._users or session in self._retirements:
            return
        task = asyncio.create_task(self._close(session), name="mcp-http-generation-close")
        self._retirements[session] = task

        def completed(task):
            if not task.cancelled() and task.exception() is None:
                self._sessions.discard(session)
                self._retirements.pop(session, None)
            else:
                logger.warning("MCP retired generation cleanup failed")

        task.add_done_callback(completed)

    async def aclose(self) -> None:
        if self._close_task is None:
            self.closed = True
            self._close_task = asyncio.create_task(
                self._close_all(), name="mcp-http-recovery-close"
            )
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            if not self._close_task.cancelled():
                self._close_task.exception()
            raise asyncio.CancelledError
        self._close_task.result()

    async def _close_all(self) -> None:
        users = tuple(self._operations)
        for task in users:
            task.cancel()
        await asyncio.gather(*users, return_exceptions=True)
        results = await asyncio.gather(
            *(self._close(s) for s in tuple(self._sessions)), return_exceptions=True
        )
        await asyncio.gather(*tuple(self._retirements.values()), return_exceptions=True)
        if self._close_shared is not None:
            try:
                await self._close_shared()
            except BaseException as error:
                results.append(error)
        for result in results:
            if isinstance(result, BaseException):
                raise result
