"""Namespace grouping and request-scoped reverse mappings, never leaf-name routing."""

from corki.models.base import ModelError
from corki.protocol.items import ToolResultItem
from corki.protocol.tool_names import compatible_tool_name, split_tool_name


def request_tool_aliases(request):
    specs = {
        spec.name: spec
        for item in (*request.context_items, *request.items)
        if isinstance(item, ToolResultItem) and not item.is_error
        for spec in item.discovered_tools
    }
    specs.update((spec.name, spec) for spec in request.tools)
    aliases = {}
    descriptions = {}
    for spec in specs.values():
        alias = compatible_tool_name(spec.name)
        if (
            request.tool_namespace_mode != "native"
            and alias in aliases
            and aliases[alias] != spec.name
        ):
            raise ModelError("tool compatibility wire name collision")
        aliases[alias] = spec.name
        namespace, _ = split_tool_name(spec.name)
        description = spec.namespace_description
        if namespace is not None and description and description.strip():
            previous = descriptions.setdefault(namespace, description)
            if previous != description:
                raise ModelError(f"conflicting tool namespace descriptions: {namespace}")
    return aliases


def group_tool_definitions(
    specs, *, native_namespaces=False, native_freeform=False, native_search=False, discovered=False
):
    output, groups = [], {}
    for spec in specs:
        if native_search and spec.name == "tool_search":
            output.append(
                {
                    "type": "tool_search",
                    "execution": "client",
                    "description": spec.description,
                    "parameters": dict(spec.parameters),
                }
            )
            continue
        definition = spec.as_response_tool(
            native_freeform=native_freeform, native_namespaces=native_namespaces
        )
        if discovered:
            definition["defer_loading"] = True
        namespace, _ = split_tool_name(spec.name)
        namespace = namespace if native_namespaces else None
        if namespace is None and not discovered:
            output.append(definition)
            continue
        namespace = namespace or "functions"
        description = spec.namespace_description if native_namespaces else None
        if namespace not in groups:
            group = {
                "type": "namespace",
                "name": namespace,
                "description": description or "",
                "tools": [],
            }
            groups[namespace] = group
            output.append(group)
        group = groups[namespace]
        if description and description.strip():
            if group["description"].strip() and group["description"] != description:
                raise ModelError(f"conflicting tool namespace descriptions: {namespace}")
            group["description"] = description
        group["tools"].append(definition)
    for name, group in groups.items():
        if not group["description"].strip():
            group["description"] = (
                ("" if native_namespaces else "Callable functions.")
                if name == "functions"
                else f"Tools in the {name} namespace."
            )
        if native_namespaces:
            group["tools"].sort(key=lambda tool: tool["name"])
    return output
