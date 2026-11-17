"""Shared host form validation; no terminal or desktop presentation dependencies."""

import math
from datetime import date, datetime
from urllib.parse import urlsplit


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

