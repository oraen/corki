"""Host-only standard MCP form interaction, independent of conversation input."""

import json
import math
from copy import deepcopy
from datetime import date, datetime
from urllib.parse import urlsplit

from corki.protocol.wire_numbers import dumps_wire


def display_text(value: object) -> str:
    """Untrusted server labels are text, never terminal control sequences or markup."""
    return "".join(c if c.isprintable() or c == "\n" else " " for c in str(value))[:12000]


def validate_field(value, schema):
    kind = schema.get("type")
    if kind is None and ("anyOf" in schema or "oneOf" in schema):
        # RMCP TitledItems contains only string const/title alternatives, no type key.
        kind = "string"
    valid = {
        "string": isinstance(value, str),
        "number": type(value) in (int, float),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
        "array": isinstance(value, list),
    }
    if not valid.get(kind, False):
        raise ValueError(f"Expected {kind}")
    if kind in ("number", "integer"):
        if not math.isfinite(value):
            raise ValueError("Expected a finite number")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError("Value is below the minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError("Value is above the maximum")
    if kind == "string":
        if len(value) < schema.get("minLength", 0):
            raise ValueError("Text is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError("Text is too long")
        fmt = schema.get("format")
        if fmt == "date":
            date.fromisoformat(value)
        elif fmt == "date-time":
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Date-time requires a timezone")
        elif fmt == "email" and (
            value.count("@") != 1 or any(c.isspace() for c in value) or not all(value.split("@"))
        ):
            raise ValueError("Expected an email address")
        elif fmt == "uri" and not urlsplit(value).scheme:
            raise ValueError("Expected an absolute URI")
    choices = schema.get("enum")
    alternatives = schema.get("oneOf", schema.get("anyOf"))
    if alternatives is not None:
        choices = [choice["const"] for choice in alternatives]
    if choices is not None and value not in choices:
        raise ValueError("Choose one of the listed values")
    if kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get(
            "maxItems", len(value)
        ):
            raise ValueError("Incorrect number of selected values")
        for item in value:
            validate_field(item, schema["items"])


async def collect_elicitation(request, read, notice, *, decide=None, choose_scope=None):
    """Read typed fields, then require an explicit final decision; never open URLs."""
    params = request.params
    label = (
        "Shell execution approval"
        if request.kind == "shell_approval"
        else "Skill MCP dependency installation"
        if request.kind == "skill_dependency_install"
        else "Patch approval"
        if request.kind == "patch_approval"
        else f"MCP request from {request.server_name}"
    )
    notice(display_text(f"{label}: {params['message']}"))
    if request.kind == "patch_approval" and "patch_retry" in params["_meta"]:
        retry = params["_meta"]["patch_retry"]
        delta = retry["committed_delta"]
        # Keep uncertainty visible even when patch/content previews are large.
        notice(
            display_text(
                f"Prior attempt: delta exact: {delta['exact']}; "
                f"committed changes: {len(delta['changes'])}. No rollback performed."
            )
        )
        evidence = "Patch retry evidence:\n" + json.dumps(retry, ensure_ascii=False, indent=2)
        notice(display_text(evidence))
        if len(evidence) > 12000:
            notice(
                "Patch retry evidence exceeds the display limit; the preview above is truncated."
            )
    if request.kind in {"tool_approval", "shell_approval", "patch_approval"}:
        arguments = (
            dumps_wire(params["_meta"]["tool_params"])
            if request.kind == "tool_approval"
            else json.dumps(params["_meta"]["tool_params"], ensure_ascii=False, indent=2)
        )
        notice(display_text("Tool arguments:\n" + arguments))
        if len("Tool arguments:\n" + arguments) > 12000:
            notice("Tool arguments exceed the display limit; the preview above is truncated.")
    content = {}
    try:
        if params.get("mode", "form") == "url":
            notice(display_text(f"URL (open manually if you choose): {params['url']}"))
        else:
            schema = params["requestedSchema"]
            # Only host-owned execution requests may select authority directly.
            # Ordinary server forms, including fields named scope, remain data.
            if (
                choose_scope is not None
                and request.kind in {"shell_approval", "patch_approval"}
                and set(schema["properties"]) == {"scope"}
            ):
                scopes = schema["properties"]["scope"].get("enum")
                if (
                    isinstance(scopes, list)
                    and scopes
                    and scopes[0] == "once"
                    and all(isinstance(scope, str) for scope in scopes)
                    and len(scopes) == len(set(scopes))
                    and all(scope in {"once", "session", "rule"} for scope in scopes)
                    and ("rule" not in scopes or request.kind == "shell_approval")
                ):
                    return await choose_scope(tuple(scopes))
            notice("Enter /decline or /cancel at any prompt. Empty optional fields are omitted.")
            for name, field in schema["properties"].items():
                kind = field.get("type")
                if kind not in ("string", "number", "integer", "boolean", "array"):
                    notice("This form contains an unsupported field type; request declined.")
                    return "decline", None
                label = display_text(field.get("title", name))
                if field.get("description"):
                    notice(display_text(field["description"]))
                # Display choices/defaults without adding these to the normal composer/history.
                hints = {
                    key: field[key]
                    for key in ("enum", "enumNames", "oneOf", "items", "default")
                    if key in field
                }
                if hints:
                    notice(display_text(json.dumps(hints, ensure_ascii=False)))
                required = name in schema.get("required", [])
                while True:
                    raw = await read(
                        f"{label} ({kind}, {'required' if required else 'optional'}): "
                    )
                    if raw.strip() in ("/decline", "/cancel"):
                        return raw.strip()[1:], None
                    if not raw and "default" not in field:
                        if required:
                            notice("This field is required.")
                            continue
                        break
                    try:
                        value = (
                            deepcopy(field["default"])
                            if not raw
                            else raw
                            if kind == "string"
                            else json.loads(raw)
                        )
                        validate_field(value, field)
                    except (ValueError, TypeError, KeyError, OverflowError):
                        notice("Invalid value; check the field type and constraints.")
                        continue
                    content[name] = value
                    break
        if decide is not None:
            action = await decide()
            if action not in ("accept", "decline", "cancel"):
                return "cancel", None
            return action, content if action == "accept" else None
        while True:
            action = (await read("Accept / decline / cancel: ")).strip().lower().lstrip("/")
            if action in ("accept", "decline", "cancel"):
                return action, content if action == "accept" else None
            notice("Enter accept, decline, or cancel explicitly.")
    except (KeyboardInterrupt, EOFError):
        return "cancel", None
