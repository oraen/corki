"""Host manifest presentation metadata, never remote MCP tool authority."""

from collections.abc import Mapping


def interface_display_name(manifest: Mapping) -> str | None:
    interface = manifest.get("interface")
    if interface is None:
        return None
    if not isinstance(interface, Mapping):
        raise ValueError("plugin interface must be an object")
    name = interface.get("displayName")
    if name is not None:
        if not isinstance(name, str):
            raise ValueError("plugin interface.displayName must be a string")
        name.encode("utf-8")
    return name
