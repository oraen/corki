"""Typed scalar reads over YAML syntax nodes, without YAML 1.1 value coercion.

Only known fields are interpreted. This follows the serde_yaml contracts used by
Codex's skill structs; it is not a general replacement YAML deserializer.
"""

import re

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


class _SyntaxLoader(yaml.BaseLoader):
    def compose_scalar_node(self, anchor):
        tag = self.peek_event().tag
        node = super().compose_scalar_node(anchor)
        node.explicit_tag = tag
        return node


def parse(text: str):
    return yaml.compose(text, Loader=_SyntaxLoader)


def is_null(node) -> bool:
    if (
        isinstance(node, ScalarNode)
        and node.style is None
        and node.explicit_tag == "tag:yaml.org,2002:null"
        and node.value not in ("", "~", "null", "Null", "NULL")
    ):
        raise ValueError("invalid explicitly tagged null")
    return node is None or (
        isinstance(node, ScalarNode)
        and node.style is None
        and node.explicit_tag in (None, "tag:yaml.org,2002:null")
        and node.value in ("", "~", "null", "Null", "NULL")
    )


def fields(node, known: tuple[str, ...], *, optional=False):
    if optional and is_null(node):
        return {}
    if not isinstance(node, MappingNode):
        raise ValueError("expected a YAML mapping")
    result = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode):
            raise ValueError("expected a scalar field name")
        name = key.value
        if name in known:
            if name in result:
                raise ValueError(f"duplicate field {name}")
            result[name] = value
    return result


def string(node) -> str | None:
    if is_null(node):
        return None
    if not isinstance(node, ScalarNode):
        raise ValueError("expected a scalar string")
    return node.value


def _literal(node, tag):
    return isinstance(node, ScalarNode) and (
        node.explicit_tag == "tag:yaml.org,2002:" + tag
        or (node.explicit_tag is None and node.style is None)
    )


def boolean(node) -> bool | None:
    if is_null(node):
        return None
    if _literal(node, "bool") and node.value in ("true", "True", "TRUE", "false", "False", "FALSE"):
        return node.value.lower() == "true"
    raise ValueError("expected a boolean")


def sequence(node):
    if not isinstance(node, SequenceNode):
        raise ValueError("expected a YAML sequence")
    return node.value


def u16(node):
    if is_null(node):
        return None
    if not _literal(node, "int") or not re.fullmatch(
        r"\+?(?:0|[1-9][0-9]*|0[xob][0-9a-fA-F]+)", node.value
    ):
        raise ValueError("expected an unsigned integer")
    value = node.value.removeprefix("+")
    number = int(value, 0 if value.startswith(("0x", "0o", "0b")) else 10)
    if not 0 <= number <= 65535:
        raise ValueError("expected a u16")
    return number
