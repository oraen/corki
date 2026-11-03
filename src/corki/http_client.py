"""Owned local HTTP pools, without consumer-specific request or retry policy."""

import httpx

from corki.http_connect import own_http_connections


class OwnedHTTPClient(httpx.AsyncClient):
    """Reclaim connected streams when TCP handoff or TLS establishment is cancelled.

    Only pools constructed by HTTPX here receive the owned backend. Explicit
    transports and mounts belong to their caller and are not inspected or patched.
    Redirect, auth, timeout, response and retry semantics remain with the consumer.
    """

    def _get_proxy_map(self, proxy, allow_env_proxies):
        proxies = super()._get_proxy_map(proxy, allow_env_proxies)
        if proxy is None and allow_env_proxies:
            # Windows system proxies often omit local bypass rules. Local model
            # gateways and MCP servers must remain reachable without that proxy.
            # Explicit caller proxies and mounts retain their normal precedence.
            for host in ("localhost", "127.0.0.1", "[::1]"):
                proxies[f"all://{host}"] = None
        return proxies

    def _init_transport(self, *args, **kwargs):
        transport = super()._init_transport(*args, **kwargs)
        return transport if kwargs.get("transport") is not None else own_http_connections(transport)

    def _init_proxy_transport(self, *args, **kwargs):
        return own_http_connections(super()._init_proxy_transport(*args, **kwargs))
