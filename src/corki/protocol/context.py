"""Distinct raw, usable, and automatic-compaction model window limits."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from corki.protocol.instruction_template import ModelInstructionTemplate
from corki.protocol.model_authority import ModelAuthority
from corki.protocol.permission_messages import ModelPermissionMessages
from corki.protocol.truncation import TruncationPolicy


@dataclass(frozen=True, slots=True)
class ModelContextInfo:
    """Static model-specific context/request metadata, not provider-wide capability."""

    model: str
    context_window: int | None = None
    max_context_window: int | None = None
    effective_context_window_percent: int = 95
    truncation_policy: TruncationPolicy = field(default_factory=TruncationPolicy)
    comp_hash: str | None = None
    supported_reasoning_levels: tuple[str, ...] = ()
    default_reasoning_level: str | None = None
    multi_agent_reasoning_effort: str | None = None
    default_reasoning_summary: str = "auto"
    supports_reasoning_summary_parameter: bool = True
    service_tiers: tuple[str, ...] = ()
    default_service_tier: str | None = None
    # Legacy checkpoint tombstone; never enables an alternate transport.
    use_responses_lite: bool = False
    # None means provenance was not recorded (including legacy checkpoints).
    # A catalog lookup sets False; synthesized metadata sets True.
    used_fallback_model_metadata: bool | None = None
    activation_authority: ModelAuthority | None = None
    permission_messages: ModelPermissionMessages | None = None
    tool_mode: str | None = None
    supports_search_tool: bool = False
    base_instructions: str | None = None
    instruction_template: ModelInstructionTemplate | None = None

    def __post_init__(self) -> None:
        if self.instruction_template is not None and not isinstance(
            self.instruction_template, ModelInstructionTemplate
        ):
            raise ValueError("instruction_template must be ModelInstructionTemplate or None")
        if self.base_instructions is not None and (
            not isinstance(self.base_instructions, str)
            or len(self.base_instructions.encode("utf-8")) > 30_000
        ):
            raise ValueError(
                "model base_instructions must be a string of at most 30000 UTF-8 bytes"
            )
        if not isinstance(self.supports_search_tool, bool):
            raise ValueError("supports_search_tool must be a bool")
        if self.tool_mode is not None:
            if not isinstance(self.tool_mode, str):
                raise ValueError("model tool_mode must be a string or None")
            if self.tool_mode not in {"direct", "code_mode", "code_mode_only"}:
                object.__setattr__(self, "tool_mode", None)
        if self.permission_messages is not None and not isinstance(
            self.permission_messages, ModelPermissionMessages
        ):
            raise ValueError("model permission_messages must be ModelPermissionMessages or None")
        if self.activation_authority is not None and not isinstance(
            self.activation_authority, ModelAuthority
        ):
            raise ValueError("model activation_authority must be a ModelAuthority or None")
        # Msgpack restores arrays as lists even for frozen dataclass fields.
        # Re-freeze the finite sequences before validation so a valid checkpoint
        # cannot silently deserialize this model (and its parent snapshot) to None.
        for name in ("service_tiers", "supported_reasoning_levels"):
            value = getattr(self, name)
            if isinstance(value, list):
                object.__setattr__(self, name, tuple(value))
        if self.used_fallback_model_metadata is not None and not isinstance(
            self.used_fallback_model_metadata, bool
        ):
            raise ValueError("used_fallback_model_metadata must be a bool or None")
        object.__setattr__(self, "use_responses_lite", False)
        if not isinstance(self.service_tiers, tuple) or any(
            not isinstance(tier, str) for tier in self.service_tiers
        ):
            raise ValueError("service_tiers must be a tuple of strings")
        if self.default_service_tier is not None and not isinstance(self.default_service_tier, str):
            raise ValueError("default_service_tier must be a string or None")
        if self.default_reasoning_summary not in ("auto", "concise", "detailed", "none"):
            raise ValueError("invalid default_reasoning_summary")
        if not isinstance(self.supports_reasoning_summary_parameter, bool):
            raise ValueError("supports_reasoning_summary_parameter must be a bool")
        if not isinstance(self.supported_reasoning_levels, tuple) or any(
            not isinstance(effort, str) or not effort for effort in self.supported_reasoning_levels
        ):
            raise ValueError("supported_reasoning_levels must be a tuple of nonempty strings")
        if self.default_reasoning_level is not None and (
            not isinstance(self.default_reasoning_level, str) or not self.default_reasoning_level
        ):
            raise ValueError("default_reasoning_level must be a nonempty string or None")
        if self.multi_agent_reasoning_effort is not None and (
            not isinstance(self.multi_agent_reasoning_effort, str)
            or not self.multi_agent_reasoning_effort
        ):
            raise ValueError("multi_agent_reasoning_effort must be a nonempty string or None")
        if self.comp_hash is not None and not isinstance(self.comp_hash, str):
            raise ValueError("model comp_hash must be a string or None")
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

    def get_model_instructions(
        self, personality: str | None = None, *, personality_enabled: bool = True
    ) -> str | None:
        """Explicit legacy catalog text overrides an optional local template."""
        if self.base_instructions is not None:
            return self.base_instructions
        if self.instruction_template is not None:
            return self.instruction_template.render(personality, enabled=personality_enabled)
        return None

    def reasoning_effort_for_model_switch(self, selected: str | None) -> str | None:
        """Resolve a temporary model switch using the catalog's ordered presets."""
        levels = self.supported_reasoning_levels
        if selected is not None and selected in levels:
            return selected
        return levels[(len(levels) - 1) // 2] if levels else self.default_reasoning_level

    def reasoning_effort_for_request(self, selected: str | None) -> str | None:
        """Translate selected effort at the request boundary without changing selection."""
        effort = selected if selected is not None else self.default_reasoning_level
        if effort == "persistent":
            return "disabled"
        if effort != "ultra":
            return effort
        levels = self.supported_reasoning_levels
        preferred = self.multi_agent_reasoning_effort
        if preferred is not None and preferred != "ultra" and preferred in levels:
            return preferred
        if "max" in levels:
            return "max"
        return next((level for level in reversed(levels) if level != "ultra"), "medium")

    def with_override(self, window: int | None) -> ModelContextInfo:
        if window is None:
            return self
        return replace(
            self,
            context_window=min(window, self.max_context_window)
            if self.max_context_window is not None
            else window,
        )

    def service_tier_for_request(self, selected: str | None) -> str | None:
        """Filter explicit selection; catalog defaults never select a request tier."""
        return selected if selected != "default" and selected in self.service_tiers else None


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
