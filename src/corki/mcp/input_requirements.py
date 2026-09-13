"""Accumulate exact MCP startup requirements from durable current-Turn user inputs."""

from types import SimpleNamespace

from corki.plugins.mentions import explicit_plugin_ids
from corki.protocol.items import UserMessageItem
from corki.skills.io import run_skill_io
from corki.skills.mentions import linked_paths


async def collect_input_requirements_async(state, items, *, cwd, skills, plugins, disabled=False):
    # Capture host-owned plugin state on the event loop. The worker only reads
    # this snapshot and returns staged requirements; it never publishes MCP state.
    return await run_skill_io(
        collect_input_requirements,
        dict(state),
        tuple(items),
        cwd=cwd,
        skills=skills,
        plugins=SimpleNamespace(plugins=tuple(plugins.plugins)),
        disabled=disabled,
    )


def collect_input_requirements(state, items, *, cwd, skills, plugins, disabled=False):
    def saved(key):
        values = state.get(key, ())
        if not isinstance(values, (tuple, list)) or any(not isinstance(v, str) for v in values):
            raise ValueError(f"invalid saved {key}")
        return set(values)

    servers = saved("mcp_required_servers")
    plugin_ids = saved("mcp_required_plugins")
    seen = saved("mcp_requirement_input_ids")
    if disabled:
        servers.clear()
        plugin_ids.clear()
    for item in items:
        if (
            not isinstance(item, UserMessageItem)
            or item.turn_id != state["turn_id"]
            or item.retained_from_id is not None
            or item.id in seen
        ):
            continue
        seen.add(str(item.id))
        if disabled:
            continue
        paths = {m.path for m in item.mentions if m.kind == "mention"}
        servers.update(
            path[6:]
            for path in paths | linked_paths(item.content)
            if path.startswith("mcp://") and path[6:]
        )
        mentioned_plugins = set(explicit_plugin_ids(item.content, item.mentions))
        # Only explicit plugin references require selected-root startup by ID.
        # A skill contributes its package's server requirements, not a synthetic
        # plugin mention based on the human-facing namespace.
        plugin_ids.update(mentioned_plugins)
        capability_ids = set(mentioned_plugins)
        selected = (
            skills.explicit_mentions(
                item.content,
                cwd,
                mentions=item.mentions,
            )
            if skills
            else ()
        )
        for skill in selected:
            servers.update(d.value for d in skill.dependencies if d.type.lower() == "mcp")
            if skill.plugin_id is not None:
                capability_ids.add(skill.plugin_id)
        for plugin in plugins.plugins:
            manifest = plugin.manifest
            if manifest.enabled and manifest.error is None and manifest.identity in capability_ids:
                raw_names = dict(manifest.mcp_raw_names)
                servers.update(raw_names.get(s.name, s.name) for s in manifest.mcp_servers)
    return {
        "mcp_required_servers": tuple(sorted(servers)),
        "mcp_required_plugins": tuple(sorted(plugin_ids)),
        "mcp_requirement_input_ids": tuple(sorted(seen)),
    }
