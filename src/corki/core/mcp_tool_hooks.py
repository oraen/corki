"""Trusted MCP hook configuration and data-only event template expansion."""

import json
import re
from copy import deepcopy


def normalize(handler):
    for name in ("server", "tool"):
        if not isinstance(handler.get(name), str) or not handler[name].strip():
            raise ValueError(f"MCP hook requires a non-empty {name}")
    timeout = handler.get("timeout")
    timeout = 600 if timeout is None else timeout
    if type(timeout) is not int or not 0 <= timeout < 2**64:
        raise ValueError("MCP hook timeout must be an unsigned integer")
    inputs = handler.get("input", {})
    if not isinstance(inputs, dict):
        raise ValueError("MCP hook input must be an object")
    from corki.plugins.agent_hooks import _toml_input

    _toml_input(inputs)
    result = {
        "type": "mcp_tool",
        "server": handler["server"],
        "tool": handler["tool"],
        "input": deepcopy(inputs),
        "timeout": max(1, timeout),
    }
    if handler.get("statusMessage") is not None:
        if not isinstance(handler["statusMessage"], str):
            raise ValueError("MCP hook statusMessage must be a string")
        result["statusMessage"] = handler["statusMessage"]
    return result


def expand(value, event):
    if isinstance(value, dict):
        return {key: expand(item, event) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item, event) for item in value]
    if not isinstance(value, str):
        return value

    def resolve(match):
        current = event
        for field in match.group(1).split("."):
            if not isinstance(current, dict) or field not in current:
                raise ValueError(f"hook input placeholder `{match.group(0)}` was not found")
            current = current[field]
        return deepcopy(current)

    pattern = re.compile(r"\$\{([^{}]+)\}")
    complete = pattern.fullmatch(value)
    if complete is not None:
        return resolve(complete)

    def text(match):
        item = resolve(match)
        return (
            item
            if isinstance(item, str)
            else json.dumps(item, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        )

    return pattern.sub(text, value)


async def run(command, payload, manager, thread_id):
    try:
        if manager is None:
            raise ValueError("MCP hook executor is unavailable")
        target = command.mcp
        output = await manager.call_hook(
            target["server"],
            target["tool"],
            expand(target["input"], payload),
            timeout=command.timeout,
            thread_id=thread_id,
        )
        return {"exit_code": 0, "stdout": output, "stderr": ""}
    except Exception as error:
        return {"error": f"MCP hook failed: {type(error).__name__}: {error}"}
