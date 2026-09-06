"""Connection lifecycle and dynamic registration for configured MCP servers."""

from __future__ import annotations

from contextlib import suppress

from corki.config import MCPServerSettings
from corki.mcp.client import MCPClient, create_client
from corki.mcp.resources import resource_tools
from corki.mcp.tools import MCPTool
from corki.protocol.tools import ToolExposure
from corki.tools import ToolRegistry


class MCPManager:
    def __init__(
        self,
        settings: tuple[MCPServerSettings, ...],
        registry: ToolRegistry,
        *,
        defer_tools: bool = False,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._defer_tools = defer_tools
        self._clients: list[MCPClient] = []
        self._clients_by_name: dict[str, MCPClient] = {}
        self.tool_names: tuple[str, ...] = ()
        self.warnings: tuple[str, ...] = ()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        names: list[str] = []
        warnings: list[str] = []
        staged: list[tuple[str, MCPClient, tuple[MCPTool, ...]]] = []
        staged_names: set[str] = set()
        current: MCPClient | None = None
        registered: list[str] = []
        try:
            for settings in self._settings:
                current = create_client(settings)
                try:
                    await current.start()
                    definitions = await current.list_tools()
                    tools = tuple(
                        MCPTool(
                            settings.name,
                            definition,
                            current,
                            exposure=ToolExposure.DEFERRED
                            if self._defer_tools
                            else ToolExposure.DIRECT,
                        )
                        for definition in definitions
                    )
                    exposed = [tool.spec.name for tool in tools]
                    if len(exposed) != len(set(exposed)):
                        raise ValueError("MCP server exposed duplicate normalized tool names")
                    collisions = [
                        name
                        for name in exposed
                        if name in staged_names or self._registry.get(name) is not None
                    ]
                    if collisions:
                        raise ValueError(f"MCP tool already registered: {', '.join(collisions)}")
                    staged.append((settings.name, current, tools))
                    staged_names.update(exposed)
                    current = None
                except Exception as exc:  # isolate one optional server
                    warnings.append(f"{settings.name}: {type(exc).__name__}: {exc}")
                    try:
                        await current.aclose()
                    except Exception as close_exc:
                        warnings.append(
                            f"{settings.name} close: {type(close_exc).__name__}: {close_exc}"
                        )
                    current = None

            # Publish handlers only after all fallible network discovery is
            # complete. Cancellation before this point leaves no ghost tools.
            for _, _, tools in staged:
                for tool in tools:
                    self._registry.register(tool)
                    registered.append(tool.spec.name)
                    names.append(tool.spec.name)

            clients_by_name = {name: client for name, client, _ in staged}
            if clients_by_name:
                for tool in resource_tools(self):
                    if self._registry.get(tool.spec.name) is not None:
                        warnings.append(f"aggregate MCP tool already registered: {tool.spec.name}")
                        continue
                    self._registry.register(tool)
                    registered.append(tool.spec.name)
                    names.append(tool.spec.name)

            self._clients = [client for _, client, _ in staged]
            self._clients_by_name = clients_by_name
            self.tool_names = tuple(names)
            self.warnings = tuple(warnings)
            self._started = True
        except BaseException:
            # Startup is a transaction from the registry's perspective. This
            # includes CancelledError, which intentionally bypasses the normal
            # optional-server Exception boundary.
            for name in reversed(registered):
                self._registry.unregister(name)
            clients = [client for _, client, _ in staged]
            if current is not None:
                clients.append(current)
            for client in reversed(clients):
                with suppress(BaseException):
                    await client.aclose()
            self._clients.clear()
            self._clients_by_name.clear()
            self.tool_names = ()
            self.warnings = ()
            self._started = False
            raise

    @property
    def prompt_inventory(self) -> str:
        """Bounded direct names and deferred sources, without an eager tool catalog."""

        names = set(self.tool_names)
        specs = tuple(spec for spec in self._registry.specs() if spec.name in names)
        direct = [f"- {spec.name}" for spec in specs if spec.exposure.is_model_visible]
        sources = sorted(
            {spec.source for spec in specs if spec.exposure.is_deferred and spec.source}
        )
        if sources:
            direct.append("Deferred sources: " + ", ".join(sources))
            direct.append("Use tool_search to discover their tools and load callable definitions.")
        return "\n".join(direct)[:8_000] or "- none"

    async def aclose(self) -> None:
        errors: list[BaseException] = []
        for client in reversed(self._clients):
            try:
                await client.aclose()
            except BaseException as exc:
                errors.append(exc)
        self._clients.clear()
        self._clients_by_name.clear()
        if errors:
            raise errors[0]

    async def list_capability(self, kind: str, server: str | None) -> dict[str, object]:
        clients = self._selected_clients(server)
        output: dict[str, object] = {}
        for name, client in clients:
            if kind == "resources":
                output[name] = await client.list_resources()
            elif kind == "templates":
                output[name] = await client.list_resource_templates()
            elif kind == "prompts":
                output[name] = await client.list_prompts()
            else:
                raise ValueError(f"unknown MCP capability: {kind}")
        return output

    async def read_resource(self, server: str, uri: str):
        return await self._client(server).read_resource(uri)

    async def get_prompt(self, server: str, name: str, arguments: dict[str, str]):
        return await self._client(server).get_prompt(name, arguments)

    def _selected_clients(self, server: str | None) -> tuple[tuple[str, MCPClient], ...]:
        if server is not None:
            return ((server, self._client(server)),)
        return tuple(self._clients_by_name.items())

    def _client(self, server: str) -> MCPClient:
        client = self._clients_by_name.get(server)
        if client is None:
            raise KeyError(f"unknown MCP server: {server}")
        return client
