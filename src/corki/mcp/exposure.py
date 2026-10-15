"""Agent Plugin declaration limits are separate from input-schema fallback."""

from corki.mcp.tools import MCPTool


def apply_agent_budget(tools):
    used = 0
    result = []
    for tool in tools:
        if isinstance(tool, MCPTool) and tool._agent_plugin:
            size = tool.model_spec_bytes()
            if size > 8000 or used + size > 64000:
                tool = tool.hidden_by_budget()
            else:
                used += size
        result.append(tool)
    return result
