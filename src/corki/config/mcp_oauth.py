"""OAuth login configuration values; validation here does not authorize a login."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MCPServerOAuthSettings:
    """Preserve configured client/callback values until the login-specific checks."""

    client_id: str | None = None
    callback_url: str | None = None
    callback_port: int | None = None

    def __post_init__(self) -> None:
        for key in ("client_id", "callback_url"):
            value = getattr(self, key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"MCP oauth.{key} must be a string")
        port = self.callback_port
        if port is not None and (type(port) is not int or not 0 <= port <= 65535):
            raise ValueError("MCP oauth.callback_port must be an unsigned 16-bit integer")

    @classmethod
    def from_mapping(cls, value: Mapping) -> "MCPServerOAuthSettings":
        if not isinstance(value, Mapping):
            raise ValueError("MCP oauth must be a table")
        return cls(
            client_id=value.get("client_id"),
            callback_url=value.get("callback_url"),
            callback_port=value.get("callback_port"),
        )
