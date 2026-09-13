"""Incremental authoritative content from one cell observation."""

from dataclasses import dataclass

from corki.protocol.tools import ToolContent, content_text


@dataclass(frozen=True, slots=True)
class CellObservation:
    content_items: tuple[ToolContent, ...]
    is_error: bool = False
    lifecycle_json: str | None = None

    @property
    def content(self) -> str:
        return content_text(self.content_items)
