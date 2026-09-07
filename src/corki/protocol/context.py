"""Distinct raw, usable, and automatic-compaction model window limits."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from corki.protocol.truncation import TruncationPolicy


@dataclass(frozen=True, slots=True)
class ModelContextInfo:
    """Static model-specific window metadata, not a provider-wide capability."""

    model: str
    context_window: int | None = None
    max_context_window: int | None = None
    effective_context_window_percent: int = 95
    truncation_policy: TruncationPolicy = field(default_factory=TruncationPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.truncation_policy, TruncationPolicy):
            raise ValueError("model truncation_policy must be a TruncationPolicy")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model context entry requires a non-empty model name")
        for name, value in (
            ("context_window", self.context_window),
            ("max_context_window", self.max_context_window),
            ("effective_context_window_percent", self.effective_context_window_percent),
        ):
            if value is None and name != "effective_context_window_percent":
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"model {name} must be a positive integer")
        if self.effective_context_window_percent > 100:
            raise ValueError("model effective_context_window_percent must not exceed 100")

    @property
    def resolved_context_window(self) -> int | None:
        return self.context_window if self.context_window is not None else self.max_context_window

    def with_override(self, window: int | None) -> ModelContextInfo:
        if window is None:
            return self
        return replace(
            self,
            context_window=min(window, self.max_context_window)
            if self.max_context_window is not None
            else window,
        )


@dataclass(frozen=True, slots=True)
class ContextLimits:
    raw_tokens: int
    auto_compact_tokens: int | None = None
    effective_percent: int = 95

    def __post_init__(self) -> None:
        for name, value in (
            ("context_window_tokens", self.raw_tokens),
            ("auto_compact_tokens", self.auto_compact_tokens),
            ("effective_context_window_percent", self.effective_percent),
        ):
            if value is None and name == "auto_compact_tokens":
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not 1 <= self.effective_percent <= 100:
            raise ValueError("effective_context_window_percent must be between 1 and 100")
        if self.usable_tokens < 1:
            raise ValueError("effective context window must contain at least one token")

    @property
    def usable_tokens(self) -> int:
        return self.raw_tokens * self.effective_percent // 100

    @property
    def auto_compact_limit(self) -> int:
        default = self.raw_tokens * 9 // 10
        return (
            min(default, self.auto_compact_tokens)
            if self.auto_compact_tokens is not None
            else default
        )

    @property
    def trigger_tokens(self) -> int:
        return min(self.auto_compact_limit, self.usable_tokens)
