"""Host-selected collaboration settings, independent of provider protocols."""

from dataclasses import dataclass
from typing import Literal

ModeKind = Literal["default", "plan"]


def validate_mode(mode: str, instructions: str | None) -> None:
    if mode not in ("default", "plan"):
        raise ValueError("collaboration mode must be default or plan")
    if instructions is not None and not isinstance(instructions, str):
        raise ValueError("collaboration instructions must be a string or None")


@dataclass(frozen=True, slots=True)
class CollaborationMode:
    """A complete replacement; model and effort are part of the selected mode."""

    mode: ModeKind
    model: str
    reasoning_effort: str | None = None
    developer_instructions: str | None = None

    def __post_init__(self) -> None:
        validate_mode(self.mode, self.developer_instructions)
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("collaboration model must be a non-empty string")
        if self.reasoning_effort is not None and (
            not isinstance(self.reasoning_effort, str) or not self.reasoning_effort.strip()
        ):
            raise ValueError("collaboration effort must be a non-empty string or None")

    def settings_changes(self) -> dict[str, object]:
        return {
            "collaboration_mode": self.mode,
            "collaboration_instructions": self.developer_instructions,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
        }
