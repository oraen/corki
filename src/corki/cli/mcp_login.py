"""Standalone third-party MCP login, without a model session or account service."""

import asyncio
import sys
import webbrowser
from dataclasses import replace
from pathlib import Path

from corki.config import CorkiPaths, CorkiSettings
from corki.config.managed_mcp import load_mcp_requirements
from corki.mcp.catalog import MCPCatalog, MCPRegistration
from corki.mcp.oauth_discovery import discover_oauth
from corki.mcp.oauth_login import login_oauth
from corki.mcp.oauth_owned import join_oauth_task
from corki.mcp.runtime_environment import MCPRuntimeContext


class LoginUsageError(ValueError):
    """Locally authored actionable diagnostics, safe to display without secrets."""


class _BrowserAuthorization:
    async def request(self, name, params):
        url = params["url"]
        print(f"Authorize MCP server {name!r} by opening this URL:\n{url}\n", flush=True)
        try:
            opened = await join_oauth_task(
                asyncio.create_task(asyncio.to_thread(webbrowser.open, url))
            )
        except Exception:
            opened = False
        if not opened:
            print(
                "Browser launch failed; copy the URL above manually.", file=sys.stderr, flush=True
            )
        # The explicit login command requests authorization. Provider consent and
        # the bound callback, not a default highlighted choice, authorize exchange.
        return {"action": "accept"}


async def run_login(name: str, scopes: str | None = None) -> int:
    requirements = load_mcp_requirements()
    paths = CorkiPaths.discover()
    settings = CorkiSettings.for_directory(Path.cwd(), config_file=paths.config_file)
    catalog = MCPCatalog(tuple(MCPRegistration(s) for s in settings.mcp_servers)).constrain(
        requirements
    )
    server = next((entry.settings for entry in catalog.servers if entry.name == name), None)
    if server is None:
        raise LoginUsageError(f"No configured MCP server named {name!r}.")
    if not server.enabled:
        raise LoginUsageError("MCP server is disabled or prohibited by managed policy.")
    if server.transport != "http":
        raise LoginUsageError("OAuth login requires a streamable HTTP MCP server.")
    if server.environment_id != "local":
        raise LoginUsageError("OAuth login for remote credential identities is not implemented.")
    if server.http_headers_helper is not None:
        raise LoginUsageError(
            "Standalone OAuth login with an HTTP header helper is not implemented."
        )
    if scopes is not None:
        values = tuple(value.strip() for value in scopes.split(","))
        if any(not value or any(c.isspace() for c in value) for value in values):
            raise LoginUsageError("--scopes requires nonempty comma-separated scope names.")
        server = replace(server, scopes=values)
    context = MCPRuntimeContext()
    discovery = await discover_oauth(server, context)
    if discovery is None:
        raise LoginUsageError("This MCP server has no discoverable OAuth authorization flow.")
    if not await login_oauth(
        server,
        context,
        paths.home,
        _BrowserAuthorization(),
        discovery,
        store_mode=settings.mcp_oauth_credentials_store,
    ):
        return 1
    print(f"Successfully logged in to MCP server {name!r}.", flush=True)
    return 0
