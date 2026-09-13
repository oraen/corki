"""Turn-owned model visibility, distinct from host connection availability."""

CODEX_APPS_SERVER = "codex_apps"


def ensure_model_access(server: str | None, excluded_servers: frozenset[str]) -> None:
    if server in excluded_servers:
        raise ValueError(f"MCP server '{server}' is excluded by host policy")
