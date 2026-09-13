"""Credential-free thread defaults, distinct from admitted Turn/Step snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Literal

from corki.protocol.collaboration import ModeKind, validate_mode
from corki.protocol.context import ModelContextInfo
from corki.protocol.model_authority import ModelAuthority
from corki.protocol.permission_messages import ModelPermissionMessages
from corki.protocol.truncation import TruncationPolicy


@dataclass(frozen=True, slots=True)
class ThreadModelSettings:
    """The model/provider/effort group used by implicit cold resume.

    An absent record means use current defaults. An absent effort in a record
    instead means clear configured effort, allowing model metadata to resolve it.
    Provider is a configuration identity, never an endpoint or a credential.
    """

    model: str
    provider: str
    reasoning_effort: str | None
    collaboration_mode: ModeKind = "default"
    collaboration_instructions: str | None = None
    personality: str | None = None

    def __post_init__(self) -> None:
        if self.personality not in (None, "none", "friendly", "pragmatic"):
            raise ValueError("invalid thread personality")
        validate_mode(self.collaboration_mode, self.collaboration_instructions)
        for name, value in (("model", self.model), ("provider", self.provider)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"thread {name} must be a non-empty string")
        if self.reasoning_effort is not None and (
            not isinstance(self.reasoning_effort, str) or not self.reasoning_effort.strip()
        ):
            raise ValueError("thread reasoning effort must be a non-empty string or None")


class UnsetSetting(Enum):
    """An omitted sparse edit, distinct from an explicit clear."""

    UNSET = "unset"


UNSET = UnsetSetting.UNSET


@dataclass(frozen=True, slots=True)
class ModelSettingsSnapshot:
    """Immutable model choices and metadata owned by an admitted Turn."""

    model: str
    provider: str
    reasoning_effort: str | None
    reasoning_summary: str | None
    service_tier: str | None
    model_info: ModelContextInfo
    raw_context_window: int
    collaboration_mode: ModeKind = "default"
    collaboration_instructions: str | None = None
    personality: str | None = None
    personality_enabled: bool = True

    def __post_init__(self) -> None:
        if type(self.personality_enabled) is not bool:
            raise ValueError("invalid snapshot personality_enabled")
        if self.personality not in (None, "none", "friendly", "pragmatic"):
            raise ValueError("invalid snapshot personality")
        validate_mode(self.collaboration_mode, self.collaboration_instructions)
        ThreadModelSettings(self.model, self.provider, self.reasoning_effort)
        if self.reasoning_summary not in (None, "none", "auto", "concise", "detailed"):
            raise ValueError("invalid reasoning summary")
        if self.service_tier is not None and not isinstance(self.service_tier, str):
            raise ValueError("service tier must be a string or None")
        if not isinstance(self.model_info, ModelContextInfo) or self.model_info.model != self.model:
            raise ValueError("model snapshot metadata must match the selected model")
        if type(self.raw_context_window) is not int or self.raw_context_window <= 0:
            raise ValueError("model snapshot window must be positive")

    def to_payload(self) -> dict[str, object]:
        """Encode the finite credential-free snapshot for the business ledger."""
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: object) -> ModelSettingsSnapshot:
        """Validate a restored ledger snapshot before any model or tool dispatch."""
        if not isinstance(payload, dict) or not isinstance(payload.get("model_info"), dict):
            raise ValueError("invalid model snapshot payload")
        values = dict(payload)
        info = dict(values["model_info"])
        if info.get("instruction_template") is not None:
            from corki.protocol.instruction_template import ModelInstructionTemplate

            if not isinstance(info["instruction_template"], dict):
                raise ValueError("invalid model instruction template")
            info["instruction_template"] = ModelInstructionTemplate(**info["instruction_template"])
        if info.get("permission_messages") is not None:
            if not isinstance(info["permission_messages"], dict):
                raise ValueError("invalid model permission messages")
            info["permission_messages"] = ModelPermissionMessages(**info["permission_messages"])
        if info.get("activation_authority") is not None:
            if not isinstance(info["activation_authority"], dict):
                raise ValueError("invalid model activation authority")
            info["activation_authority"] = ModelAuthority(**info["activation_authority"])
        info["truncation_policy"] = TruncationPolicy.from_mapping(info.get("truncation_policy"))
        for name in ("service_tiers", "supported_reasoning_levels"):
            value = info.get(name, ())
            if not isinstance(value, (list, tuple)):
                raise ValueError("invalid model metadata sequence")
            info[name] = tuple(value)
        values["model_info"] = ModelContextInfo(**info)
        return cls(**values)


@dataclass(frozen=True, slots=True)
class TurnSettingsUpdateResult:
    """Publication acknowledgement, not evidence that a Step consumed the update."""

    status: Literal["applied", "target_unavailable", "rejected"]
    reason: str | None = None
