"""Owned loopback callback listener; untrusted callbacks never redeem codes."""

import asyncio
import base64
import hashlib
import secrets
from contextlib import asynccontextmanager, suppress
from urllib.parse import parse_qs, urlsplit, urlunsplit

from corki.config.mcp_url import http_url
from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.oauth_owned import join_oauth_task


@asynccontextmanager
async def callback_listener(settings, metadata, state):
    """Bind one login's server and issuer, and join every accepted connection on exit."""
    configured = settings.oauth
    port = configured.callback_port if configured else None
    if port == 0:
        raise MCPProtocolError("OAuth callback port must be between 1 and 65535")
    issuer = metadata.get("issuer")
    required = metadata.get("authorization_response_iss_parameter_supported") is True
    if required and not issuer:
        raise MCPProtocolError("OAuth issuer-bound callback requires an issuer")
    parsed = urlsplit(http_url(settings.url))
    identity = urlunsplit((*parsed[:4], ""))
    suffix = base64.urlsafe_b64encode(hashlib.sha256(identity.encode()).digest()[:9]).decode()
    path = "/callback" if required else f"/callback/{suffix}"
    callback = configured.callback_url if configured else None
    if callback:
        requested = urlsplit(callback)
        if (
            requested.scheme != "http"
            or requested.hostname != "127.0.0.1"
            or requested.username is not None
            or requested.password is not None
            or requested.query
            or requested.fragment
        ):
            raise MCPProtocolError("This OAuth login requires a loopback callback URL")
        if not required and not requested.path.endswith("/" + suffix):
            raise MCPProtocolError("OAuth callback URL is not bound to this MCP server")
        path = requested.path
        if requested.port is not None:
            if port is not None and port != requested.port:
                raise MCPProtocolError("OAuth callback port conflicts with callback URL")
            port = requested.port
    result = asyncio.get_running_loop().create_future()
    tasks = set()
    writers = set()

    async def handle(reader, writer):
        try:
            status, message = 400, "Invalid OAuth callback"
            async with asyncio.timeout(10):
                head = await reader.readuntil(b"\r\n\r\n")
                method, target, _ = head.split(b"\r\n", 1)[0].decode("ascii").split(" ")
                url = urlsplit(target)
                query = parse_qs(url.query, keep_blank_values=True)
                single = all(
                    len(v) == 1 for k, v in query.items() if k in {"code", "state", "iss", "error"}
                )
                matched = secrets.compare_digest(
                    query.get("state", [""])[0].encode(), state.encode()
                )
                received = query.get("iss", [None])[0]
                bound = (not required or received is not None) and (
                    received is None or issuer is not None and received == issuer
                )
                if (
                    method == "GET"
                    and url.path == path
                    and single
                    and matched
                    and bound
                    and not result.done()
                ):
                    if query.get("error"):
                        result.set_exception(MCPProtocolError("OAuth authorization was rejected"))
                    elif query.get("code", [""])[0]:
                        result.set_result(query["code"][0])
                        status, message = (
                            200,
                            "Callback received. Return to Corki to finish authorization.",
                        )
                body = message.encode()
                writer.write(
                    f"HTTP/1.1 {status} {'OK' if status == 200 else 'Bad Request'}\r\n"
                    f"Content-Length: {len(body)}\r\nContent-Type: text/plain\r\n"
                    "Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
        except (
            ValueError,
            UnicodeError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
            OSError,
        ):
            pass
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            writers.discard(writer)

    def accept(reader, writer):
        # Record ownership synchronously, before the handler's first scheduling.
        writers.add(writer)
        task = asyncio.create_task(handle(reader, writer))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    server = await asyncio.start_server(accept, "127.0.0.1", port or 0, limit=8192)

    async def close():
        server.close()
        try:
            # A handler cancelled before its first poll cannot run its finally.
            for writer in tuple(writers):
                writer.close()
            pending = tuple(tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await server.wait_closed()
        finally:
            if result.done() and not result.cancelled():
                result.exception()
            else:
                result.cancel()

    try:
        actual = server.sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{actual}{path}", result
    finally:
        await join_oauth_task(asyncio.create_task(close()))
