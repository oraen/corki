"""File-backed plugin selections retain provenance without changing collection APIs."""

import tomllib

from corki.config.layers import LocalConfigState, _merge, _normalize_layer
from corki.config.plugin_policies import plugin_policies, raw_plugin_policies


class LayerPluginDirectories(tuple):
    """Immutable parsed directories; a plain host tuple is an explicit override."""

    __slots__ = ()


class LayerDisabledPlugins(frozenset):
    """Immutable parsed denylist; a plain host frozenset is an explicit override."""

    __slots__ = ()


def plugin_selection(configuration: LocalConfigState, *, strict: bool = False):
    document = {}
    directories = []
    for layer in configuration.layers:
        if layer.disabled_reason is not None:
            continue
        values = tomllib.loads(layer.contents)
        _normalize_layer(values, layer.file.parent)
        if "plugins" in values:
            _merge(document, {"plugins": values["plugins"]})
            # A project may restrict known contributions, not introduce arbitrary
            # in-process code. Trusting project instructions is not installation.
            plugins = values["plugins"]
            if layer.kind == "user" and isinstance(plugins, dict):
                directories = plugins.get("directories", directories)
                if isinstance(directories, dict):
                    directories = []  # A package named directories, not executable roots.
    plugins = document.get("plugins", {})
    disabled = plugins.get("disabled", []) if isinstance(plugins, dict) else []
    if isinstance(disabled, dict):
        disabled = []  # A package named disabled, not the legacy selector.
    for value in (directories, disabled):
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("plugin directories/disabled must be string arrays")
    policies = plugin_policies(document) if strict else raw_plugin_policies(document)
    return directories, frozenset(disabled) | frozenset(
        name for name, policy in policies.items() if not policy.get("enabled", True)
    )
