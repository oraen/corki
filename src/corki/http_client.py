"""Owned local HTTP pools, without consumer-specific request or retry policy."""

import httpx

from corki.http_connect import own_http_connections


class OwnedHTTPClient(httpx.AsyncClient):
    """Reclaim connected streams when TCP handoff or TLS establishment is cancelled.

    Only pools constructed by HTTPX here receive the owned backend. Explicit
    transports and mounts belong to their caller and are not inspected or patched.
    Redirect, auth, timeout, response and retry semantics remain with the consumer.
    """

    def _init_transport(self, *args, **kwargs):
        transport = super()._init_transport(*args, **kwargs)
        return transport if kwargs.get("transport") is not None else own_http_connections(transport)

    def _init_proxy_transport(self, *args, **kwargs):
        return own_http_connections(super()._init_proxy_transport(*args, **kwargs))
