"""Strict rendering for Corki's deliberately small template language.

Only ``#{variable_name}`` substitutions are supported.  Prompt control flow
belongs in Python so Markdown resources remain readable and predictable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from corki.prompting.models import PromptTemplate

_PLACEHOLDER_PATTERN = re.compile(r"(?<!\\)#\{([^{}]*)\}")
_VARIABLE_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ESCAPED_PLACEHOLDER_PREFIX = r"\#{"


class InvalidPromptTemplateError(ValueError):
    """Raised when a template contains an invalid placeholder."""


class MissingPromptVariablesError(ValueError):
    """Raised when rendering does not provide every required variable."""

    def __init__(self, template_name: str, missing: set[str]) -> None:
        self.template_name = template_name
        self.missing = frozenset(missing)
        missing_names = ", ".join(sorted(missing))
        super().__init__(f"prompt template {template_name!r} is missing: {missing_names}")


def parse_template(name: str, source: str) -> PromptTemplate:
    """Validate ``source`` and return its immutable parsed representation."""

    variables: set[str] = set()
    for match in _PLACEHOLDER_PATTERN.finditer(source):
        variable_name = match.group(1)
        if _VARIABLE_NAME_PATTERN.fullmatch(variable_name) is None:
            raise InvalidPromptTemplateError(
                f"prompt template {name!r} contains invalid placeholder {match.group(0)!r}"
            )
        variables.add(variable_name)

    unmatched_source = _PLACEHOLDER_PATTERN.sub("", source).replace(_ESCAPED_PLACEHOLDER_PREFIX, "")
    if "#{" in unmatched_source:
        raise InvalidPromptTemplateError(
            f"prompt template {name!r} contains an incomplete placeholder"
        )

    return PromptTemplate(name=name, source=source, variables=frozenset(variables))


def render_template(template: PromptTemplate, variables: Mapping[str, str]) -> str:
    """Render a parsed template, failing when required values are absent."""

    missing = set(template.variables).difference(variables)
    if missing:
        raise MissingPromptVariablesError(template.name, missing)

    def replace(match: re.Match[str]) -> str:
        return variables[match.group(1)]

    rendered = _PLACEHOLDER_PATTERN.sub(replace, template.source)
    return rendered.replace(_ESCAPED_PLACEHOLDER_PREFIX, "#{")
