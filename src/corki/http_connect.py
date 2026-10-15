"""Cancellation-safe ownership of HTTPcore's local TCP connection handoff.

The Happy Eyeballs address ordering/race adapts AnyIO (MIT; ANYIO_LICENSE.txt).
Unlike the upstream handoff, every exceptional exit closes the winning stream.
HTTP framing, proxy handling, TLS and pooled response ownership remain HTTPX/HTTPcore's.
"""

import ipaddress
import socket

import anyio
import httpcore
import httpx
from anyio.abc import SocketAttribute, SocketStream
from anyio.lowlevel import get_async_backend
from httpcore._backends.anyio import AnyIOStream


async def _connect_tcp(host: str, port: int, local_host: str | None) -> SocketStream:
    backend = get_async_backend()
    family, local_address = socket.AF_UNSPEC, None
    if local_host:
        local = (await anyio.getaddrinfo(local_host, None))[0]
        family, local_address = local[0], local[4]
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [literal.compressed]
    else:
        addresses = []
        ipv6 = ipv4 = False
        for af, _, _, _, address in await anyio.getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM
        ):
            if af == socket.AF_INET6 and not ipv6:
                ipv6 = True
                addresses.insert(0, address[0])
            elif af == socket.AF_INET and not ipv4 and ipv6:
                ipv4 = True
                addresses.insert(1, address[0])
            else:
                addresses.append(address[0])

    winner: SocketStream | None = None
    errors: list[OSError] = []

    async def attempt(address: str, done: anyio.Event) -> None:
        nonlocal winner
        try:
            stream = await backend.connect_tcp(address, port, local_address)
            if winner is None:
                winner = stream
                group.cancel_scope.cancel()
            else:
                await anyio.aclose_forcefully(stream)
        except OSError as error:
            errors.append(error)
        finally:
            done.set()

    try:
        async with anyio.create_task_group() as group:
            for address in addresses:
                done = anyio.Event()
                group.start_soon(attempt, address, done)
                with anyio.move_on_after(0.25):
                    await done.wait()
        if winner is None:
            cause = errors[0] if len(errors) == 1 else ExceptionGroup("connection attempts", errors)
            raise OSError("All connection attempts failed") from cause
        return winner
    except BaseException:
        # A connected socket can exist even though the connector task never
        # returned it to HTTPcore. Neither pool.close nor response.close owns it.
        if winner is not None:
            await anyio.aclose_forcefully(winner)
        raise
    finally:
        errors.clear()


class OwnedNetworkStream(AnyIOStream):
    """TLS upgrade owns the original stream until the upgraded stream is handed off."""

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        try:
            upgraded = await super().start_tls(ssl_context, server_hostname, timeout)
            # Preserve cancellation ownership for nested TLS through HTTPS proxies.
            return OwnedNetworkStream(upgraded._stream)
        except BaseException:
            await anyio.aclose_forcefully(self._stream)
            raise


class OwnedConnectBackend(httpcore.AnyIOBackend):
    """Keep the existing stream/TLS implementation, replacing only TCP acquisition."""

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        stream = None
        try:
            with anyio.fail_after(timeout):
                stream = await _connect_tcp(host, port, local_address)
                for option in socket_options or ():
                    stream.extra(SocketAttribute.raw_socket).setsockopt(*option)
            return OwnedNetworkStream(stream)
        except BaseException as error:
            if stream is not None:
                await anyio.aclose_forcefully(stream)
            if isinstance(error, TimeoutError):
                raise httpcore.ConnectTimeout(str(error)) from error
            if isinstance(error, (OSError, anyio.BrokenResourceError)):
                raise httpcore.ConnectError(str(error)) from error
            raise


def own_http_connections(transport: httpx.AsyncHTTPTransport) -> httpx.AsyncHTTPTransport:
    """Install before any pool connection exists, including HTTPX-created proxy pools.

    These two private seams are confined here and to OwnedHTTPClient's construction
    hooks. The declared HTTPX0.28/HTTPcore1.0 dependency ranges bound that contract.
    Explicit host-provided transports are never inspected or mutated.
    """
    transport._pool._network_backend = OwnedConnectBackend()
    return transport
