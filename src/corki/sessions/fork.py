"""Portable historical facts; no leases, checkpoints or execution obligations."""

from collections.abc import Mapping
from dataclasses import dataclass

from corki.protocol.ids import ThreadId
from corki.protocol.items import ConversationItem


@dataclass(frozen=True)
class ForkSnapshot:
    source_thread_id: ThreadId
    items: tuple[ConversationItem, ...]
    turns: tuple[Mapping[str, str | None], ...]
    usage: tuple[tuple[int | None, str | None, int | None, str | None], ...]
    base_instructions: tuple[str, str] | None = None
    base_instructions_provenance: str | None = "model"

    def __post_init__(self) -> None:
        if self.base_instructions_provenance not in (None, "model", "custom"):
            raise ValueError("invalid fork base instruction provenance")
        if self.base_instructions is not None and (
            not isinstance(self.base_instructions, tuple)
            or len(self.base_instructions) != 2
            or any(not isinstance(value, str) for value in self.base_instructions)
            or not self.base_instructions[0]
        ):
            raise ValueError("fork base instructions require an immutable model/text pair")
