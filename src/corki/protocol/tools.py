"""Provider-neutral tool contracts.

The model-visible specification and executable handler deliberately travel
together through the registry, while calls and results remain serializable
facts that can be persisted in conversation history.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from corki.protocol.ids import ToolCallId


class ToolExposure(StrEnum):
    """Controls direct, deferred, model-only and hidden tool surfaces."""

    DIRECT = "direct"
    DEFERRED = "deferred"
    DIRECT_MODEL_ONLY = "direct_model_only"
    DEFERRED_MODEL_ONLY = "deferred_model_only"
    CODE_MODE_ONLY = "code_mode_only"
    HIDDEN = "hidden"

    @property
    def is_model_visible(self) -> bool:
        """Whether this tool is advertised for direct model invocation."""

        return self in {self.DIRECT, self.DIRECT_MODEL_ONLY}

    @property
    def is_deferred(self) -> bool:
        return self in {self.DEFERRED, self.DEFERRED_MODEL_ONLY}


class ToolConcurrency(StrEnum):
    """Whether calls may overlap with other calls from one model step."""

    PARALLEL = "parallel"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """JSON-schema function definition shown to the model."""

    name: str
    description: str
    parameters: Mapping[str, Any]
    exposure: ToolExposure = ToolExposure.DIRECT
    concurrency: ToolConcurrency = ToolConcurrency.EXCLUSIVE
    # Instruction and metadata tools sometimes legitimately return more than
    # a terminal command.  A per-tool override keeps the global budget small
    # while still bounding every model-visible result.
    output_char_budget: int | None = None
    search_text: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        # Keep checkpoint serialization portable. ``mappingproxy`` appears
        # immutable but cannot be encoded by LangGraph's durable serializer.
        object.__setattr__(self, "parameters", deepcopy(dict(self.parameters)))
        if self.output_char_budget is not None and self.output_char_budget <= 0:
            raise ValueError("tool output_char_budget must be positive")

    def as_chat_completion_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One complete model request to invoke a tool."""

    id: ToolCallId
    name: str
    arguments: Mapping[str, Any] | None
    raw_arguments: str = ""
    parse_error: str | None = None

    def __post_init__(self) -> None:
        if self.arguments is not None:
            object.__setattr__(self, "arguments", dict(self.arguments))


@dataclass(frozen=True, slots=True)
class ImageAttachment:
    """An image returned by a tool and eligible for multimodal model input."""

    data_url: str
    detail: str = "high"


@dataclass(frozen=True, slots=True)
class ToolStateUpdate:
    """Explicit graph-state changes requested by a tool result."""

    plan: tuple[Mapping[str, str], ...] | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Normalized tool outcome consumed by both model and presentation layers."""

    call_id: ToolCallId
    tool_name: str
    content: str
    is_error: bool = False
    display_content: str | None = None
    attachments: tuple[ImageAttachment, ...] = ()
    state_update: ToolStateUpdate = field(default_factory=ToolStateUpdate)
    discovered_tools: tuple[ToolSpec, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "discovered_tools", tuple(self.discovered_tools))


def tool_spec_from_payload(value: Mapping[str, Any]) -> ToolSpec:
    """Read a persisted definition, retaining defaults for older histories."""

    fields = dict(value)
    fields["exposure"] = ToolExposure(fields.get("exposure", "direct"))
    fields["concurrency"] = ToolConcurrency(fields.get("concurrency", "exclusive"))
    return ToolSpec(**fields)
