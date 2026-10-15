"""Typed package policy admission, distinct from executable directory selection."""

import logging
from copy import deepcopy

_COMPATIBILITY_KEYS = frozenset({"directories", "disabled"})
_APPROVAL_MODES = frozenset({"auto", "writes", "prompt", "approve"})
_LOG = logging.getLogger(__name__)


def raw_plugin_policies(document: dict) -> dict:
    """Raw-layer services discard an invalid whole map; cold admission stays strict."""
    try:
        return plugin_policies(document)
    except ValueError:
        _LOG.warning("Invalid plugins configuration; ignoring package policy mapping")
        return {}


def plugin_policies(document: dict) -> dict:
    """Validate native fields; unknown fields remain forward compatible like serde."""
    plugins = _table(document.get("plugins", {}), "plugins")
    result = {}
    for name, value in plugins.items():
        if name in _COMPATIBILITY_KEYS and isinstance(value, list):
            continue
        prefix = f"plugins.{name}"
        value = _table(value, prefix)
        _enabled(value, prefix)
        for server, policy in _table(value.get("mcp_servers", {}), prefix + ".mcp_servers").items():
            path = f"{prefix}.mcp_servers.{server}"
            policy = _table(policy, path)
            _enabled(policy, path)
            _approval(policy, "default_tools_approval_mode", path)
            for key in ("enabled_tools", "disabled_tools"):
                if key in policy and (
                    not isinstance(policy[key], list)
                    or any(not isinstance(tool, str) for tool in policy[key])
                ):
                    raise ValueError(f"{path}.{key} must be an array of strings")
            for tool, entry in _table(policy.get("tools", {}), path + ".tools").items():
                tool_path = f"{path}.tools.{tool}"
                entry = _table(entry, tool_path)
                _approval(entry, "approval_mode", tool_path)
                if "output_token_limit" in entry:
                    limit = entry["output_token_limit"]
                    if type(limit) is not int or not 0 < limit < 2**64:
                        raise ValueError(f"{tool_path}.output_token_limit must be a positive usize")
        result[name] = deepcopy(value)
    return result


def _table(value, path):
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} must be a table")
    return value


def _enabled(value, path):
    if "enabled" in value and type(value["enabled"]) is not bool:
        raise ValueError(f"{path}.enabled must be a boolean")


def _approval(value, field, path):
    if field in value and (
        not isinstance(value[field], str) or value[field] not in _APPROVAL_MODES
    ):
        raise ValueError(f"{path}.{field} must be auto/writes/prompt/approve")
