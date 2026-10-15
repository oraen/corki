"""Model output policy in explicit UTF-8 bytes or approximate four-byte tokens."""

import math
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class TruncationPolicy:
    mode: str = "bytes"
    limit: int = 10_000

    def __post_init__(self):
        if self.mode not in {"bytes", "tokens"}:
            raise ValueError("truncation policy mode must be bytes or tokens")
        if type(self.limit) is not int or self.limit < 0:
            raise ValueError("truncation policy limit must be a non-negative integer")

    @classmethod
    def from_mapping(cls, value):
        if value is None:
            return cls()
        if not isinstance(value, dict) or set(value) != {"mode", "limit"}:
            raise ValueError("truncation_policy requires mode and limit")
        return cls(**value)

    @property
    def byte_budget(self):
        return self.limit * 4 if self.mode == "tokens" else self.limit

    def history_allowance(self):
        return replace(self, limit=math.ceil(self.limit * 1.2))

    def with_token_override(self, tokens):
        return replace(self, limit=tokens * 4 if self.mode == "bytes" else tokens)
