"""Host-pinned MCP capabilities, separate from editable server declarations."""

from dataclasses import dataclass

import httpx

from corki.config import MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements
from corki.mcp.json_rpc import MCPProtocolError


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class MCPHTTPEnvironment:
    """One concrete host-selected HTTP capability and its owner policy.

    The host owns the carrier and closes it after all borrowing Runtimes finish.
    Neither MCP session shutdown nor reconciliation closes a shared environment.
    Object identity deliberately matters even when names and transports match.
    This does not implement executor discovery or stdio.
    """

    environment_id: str
    transport: httpx.AsyncBaseTransport
    requirements: MCPRequirements = MCPRequirements()

    def __post_init__(self) -> None:
        if not isinstance(self.environment_id, str) or self.environment_id == "local":
            raise ValueError("HTTP environment needs a nonlocal string identity")
        if not isinstance(self.transport, httpx.AsyncBaseTransport):
            raise ValueError("HTTP environment needs a concrete asynchronous transport")
        if not isinstance(self.requirements, MCPRequirements):
            raise ValueError("environment requirements must be host-owned typed policy")

    def validate(self, settings: MCPServerSettings) -> None:
        """Validate exact placement before allocating an HTTP session or reading secrets."""
        if settings.environment_id != self.environment_id:
            raise MCPProtocolError("MCP HTTP environment does not match server placement")
        if settings.transport != "http":
            raise MCPProtocolError("selected MCP environment has no stdio capability")
        if settings.http_headers_helper is not None:
            raise MCPProtocolError("HTTP headers helpers can only run in the local environment")

    async def executor_bearer(self, settings: MCPServerSettings) -> str | None:
        """Select executor resolution only for its actual negotiated HTTP capability."""
        from corki.mcp.executor_http import ExecutorHttpTransport

        if (
            settings.bearer_token_env_var is not None
            and isinstance(self.transport, ExecutorHttpTransport)
            and await self.transport.resolves_header_env_vars()
        ):
            return settings.bearer_token_env_var
        return None


@dataclass(frozen=True, slots=True)
class MCPRuntimeContext:
    """Immutable selection snapshot; bindings retain their original host handles."""

    environments: tuple[MCPHTTPEnvironment, ...] = ()

    def __post_init__(self) -> None:
        environments = tuple(self.environments)
        if any(not isinstance(binding, MCPHTTPEnvironment) for binding in environments):
            raise ValueError("MCP environments must be host-owned typed bindings")
        if len({binding.environment_id for binding in environments}) != len(environments):
            raise ValueError("MCP environment identities must be unique")
        object.__setattr__(self, "environments", environments)

    def resolve(self, settings: MCPServerSettings) -> MCPHTTPEnvironment | None:
        """Resolve before physical reuse; an absent binding never falls back to local."""
        for binding in self.environments:
            if binding.environment_id == settings.environment_id:
                binding.validate(settings)
                return binding
        require_local_environment(settings)
        return None

    def requirements_for(self, environment_id: str) -> MCPRequirements:
        """Owner policy is additional to controller requirements, not a replacement."""
        for binding in self.environments:
            if binding.environment_id == environment_id:
                return binding.requirements
        # Unknown identities remain unresolved, preserving their explicit startup error.
        return MCPRequirements()

    @classmethod
    def coerce(cls, value: "MCPRuntimeContext | None") -> "MCPRuntimeContext":
        """Reject editable mappings before Runtime resource allocation."""
        if value is None:
            return cls()
        if not isinstance(value, cls):
            raise ValueError("MCP runtime context must be host-owned typed bindings")
        return value


def require_local_environment(settings: MCPServerSettings) -> None:
    """Reject unknown bindings before local client reuse, creation or credential reads."""
    if settings.environment_id != "local":
        raise MCPProtocolError(
            f"MCP server `{settings.name}` references unknown environment id "
            f"`{settings.environment_id}`"
        )
