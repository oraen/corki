"""Value objects used by the prompt template subsystem."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """An immutable prompt template loaded from one Markdown resource.

    ``variables`` is collected when the file is loaded.  Keeping that metadata
    beside the source lets callers inspect a template without parsing it again.
    """

    name: str
    source: str
    variables: frozenset[str]
