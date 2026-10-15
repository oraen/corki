"""Code-mode exposure and raw-source directives."""

import json
import re

from corki.prompting import PromptStore
from corki.protocol.tool_exposure import CODE_MODE_EXPOSURES
from corki.protocol.tools import ToolSpec

_EXEC_DESCRIPTION = PromptStore().render("tools/code_mode_exec")

CONTROL_NAMES = {"exec", "wait", "tool_search"}


def nested_description(spec):
    """Describe the call contract before dropping schemas at the worker boundary.

    The native runtime uses TypeScript declarations. This adapter keeps the same
    information as JSON schema text, including for deferred ALL_TOOLS entries;
    these schemas are documentation, not worker-side argument/result validators.
    """
    parameters = {"type": "string"} if spec.input_kind == "freeform" else spec.parameters
    description = spec.description + "\nInput schema: " + json.dumps(parameters, ensure_ascii=False)
    if spec.output_schema is not None:
        description += "\nOutput schema: " + json.dumps(spec.output_schema, ensure_ascii=False)
    return description


def nested_specs(registry):
    result = {}
    for spec in registry.specs():
        if (
            spec.name in CONTROL_NAMES
            or spec.exposure not in CODE_MODE_EXPOSURES
            or not registry.namespace_policy.includes_nested(spec.name)
        ):
            continue
        name = re.sub(r"[^A-Za-z0-9_]", "_", spec.name)
        if name and name[0].isdigit():
            name = "_" + name
        if name in result and result[name].name != spec.name:
            raise ValueError(f"Code Mode tool alias collision: {name}")
        result[name] = spec
    return result


def parse_source(source):
    if not source.strip():
        raise ValueError("exec expects non-empty raw JavaScript source")
    first, separator, rest = source.partition("\n")
    if not first.lstrip().startswith("// @exec:"):
        return source, 10000, 10000
    if not separator or not rest.strip():
        raise ValueError("exec pragma must be followed by JavaScript source")
    options = json.loads(first.lstrip()[len("// @exec:") :])
    if not isinstance(options, dict) or set(options) - {"yield_time_ms", "max_output_tokens"}:
        raise ValueError("exec pragma only supports yield_time_ms and max_output_tokens")
    for value in options.values():
        if value is not None and (type(value) is not int or not 0 <= value <= 2**53 - 1):
            raise ValueError("exec pragma fields must be non-negative safe integers")
    return (
        rest,
        options.get("yield_time_ms") if options.get("yield_time_ms") is not None else 10000,
        options.get("max_output_tokens") if options.get("max_output_tokens") is not None else 10000,
    )


def exec_spec(registry):
    definitions = nested_specs(registry)
    direct = {
        name: {
            "name": spec.name,
            "description": spec.description,
            "input_kind": spec.input_kind,
            "parameters": {"type": "string"} if spec.input_kind == "freeform" else spec.parameters,
            **({"output_schema": spec.output_schema} if spec.output_schema is not None else {}),
        }
        for name, spec in definitions.items()
        if not spec.exposure.is_deferred
    }
    return ToolSpec(
        "exec",
        _EXEC_DESCRIPTION
        + "\nDirect nested definitions: "
        + json.dumps(direct, ensure_ascii=False),
        {},
        input_kind="freeform",
        freeform_format={"type": "grammar", "syntax": "lark", "definition": "start: /[\\s\\S]+/"},
    )
