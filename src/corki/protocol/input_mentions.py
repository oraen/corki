"""Host-owned structured input selectors; never tool output or model instructions."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class InputMention:
    name: str
    path: str
    kind: Literal["mention", "skill"] = "mention"

    def __post_init__(self):
        if not isinstance(self.name, str) or not isinstance(self.path, str):
            raise ValueError("input mention requires string name and path")
        if self.kind not in ("mention", "skill"):
            raise ValueError("invalid input mention kind")


def validate_mentions(values) -> tuple[InputMention, ...]:
    if not isinstance(values, (tuple, list)) or any(
        not isinstance(v, InputMention) for v in values
    ):
        raise ValueError("input mentions must be typed InputMention values")
    return tuple(values)
