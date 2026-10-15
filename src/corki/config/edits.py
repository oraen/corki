"""Atomic settings edits, retaining comments and the existing symlink target."""

from contextlib import suppress
from pathlib import Path

from corki.config import document, toml_edits
from corki.config.features import parse_mcp_servers
from corki.config.toml_edits import _WRITES, MCPServerInstallResult, write_paths


def _read(path):
    read_path, write_path = write_paths(path)
    contents = ""
    if read_path is not None:
        with suppress(FileNotFoundError):
            contents = read_path.read_text(encoding="utf-8")
    return contents, write_path


def set_config_value(path: Path, segments: tuple[str, ...], value: str) -> None:
    if path.suffix == ".toml":
        return toml_edits.set_config_value(path, segments, value)
    with _WRITES:
        contents, target = _read(path)
        toml_edits.write_text(target, document.set_value(contents, segments, value))


def add_missing_mcp_servers(path: Path, candidates: dict[str, dict]) -> MCPServerInstallResult:
    if path.suffix == ".toml":
        return toml_edits.add_missing_mcp_servers(path, candidates)
    with _WRITES:
        contents, target = _read(path)
        values = document.loads(contents)
        mcp = values.get("mcp", {})
        if not isinstance(mcp, dict):
            raise ValueError("MCP configuration must be an object")
        servers = mcp.get("servers", {})
        existing = parse_mcp_servers(servers)
        added = tuple(name for name in candidates if name not in servers)
        if not added:
            return MCPServerInstallResult((), existing)
        snapshot = parse_mcp_servers({**servers, **{name: candidates[name] for name in added}})
        for name in added:
            contents = document.set_value(contents, ("mcp", "servers", name), candidates[name])
        toml_edits.write_text(target, contents)
        return MCPServerInstallResult(added, snapshot)


def save_model_default(path: Path, provider: str, model: str, effort: str | None) -> None:
    """Commit the pair and its reasoning preference together, preserving comments."""
    if path.suffix == ".toml":
        # Legacy files remain readable; new defaults are written in the JSON format.
        raise ValueError("Migrate the legacy config to settings.json before saving defaults")
    with _WRITES:
        contents, target = _read(path)
        values = document.loads(contents)
        profiles = values.get("providers", {})
        provider = next(
            (name for name, entry in profiles.items() if provider in entry.get("aliases", [])),
            provider,
        )
        inline = values.get("provider", {})
        inline_identity = inline.get("id", inline.get("name", "default"))
        if provider not in profiles and (provider != inline_identity or not inline and profiles):
            raise ValueError("Selected provider is no longer configured; default was not saved")
        profile = profiles.get(provider, {})
        contents = document.set_value(
            contents, ("default",), {"provider": provider, "model": model}
        )
        if "models" in profile:
            contents = document.set_value(
                contents, ("providers", provider, "models", model, "reasoning_effort"), effort
            )
        else:
            # Preserve legacy layout while persisting its selected model/effort.
            inline = values.get("provider", {})
            identity = inline.get("id", inline.get("name", "default"))
            prefix = ("provider",) if identity == provider else ("providers", provider)
            contents = document.set_value(contents, (*prefix, "reasoning_effort"), effort)
        toml_edits.write_text(target, contents)
