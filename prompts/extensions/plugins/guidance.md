<plugins_instructions>
## Plugins

A plugin is a local bundle that may contribute tools, skills, and MCP servers.

- Plugin skills appear as `plugin-name:skill-name`; use that qualified name.
- MCP tools retain `mcp__server__tool` provenance; Python tools use
  `plugin__plugin__tool` names.
- Plugins are not invoked directly. Use the concrete capability that matches the task.
- If the user explicitly names a plugin, prefer that plugin's applicable capabilities.
- A startup warning means only that capability is unavailable; continue with independent tools.
</plugins_instructions>
