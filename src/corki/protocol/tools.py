"""Provider-neutral tool contracts.

The model-visible specification and executable handler deliberately travel
together through the registry, while calls and results remain serializable
facts that can be persisted in conversation history.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from corki.protocol.ids import ToolCallId
from corki.protocol.tool_names import compatible_tool_name, split_tool_name


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


class ToolInputKind(StrEnum):
    JSON = "json"
    FREEFORM = "freeform"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """JSON function or raw-input tool definition shown to the model."""

    name: str
    description: str
    parameters: Mapping[str, Any]
    exposure: ToolExposure = ToolExposure.DIRECT
    concurrency: ToolConcurrency = ToolConcurrency.EXCLUSIVE
    # Legacy compatibility cap applied only to model copies and UI previews.
    # Model byte/token policy remains authoritative; nested callers retain raw output.
    output_char_budget: int | None = None
    search_text: str | None = None
    source: str | None = None
    input_kind: ToolInputKind = ToolInputKind.JSON
    freeform_format: Mapping[str, Any] | None = None
    source_description: str | None = None
    namespace_description: str | None = None

    def __post_init__(self) -> None:
        namespace, leaf = split_tool_name(self.name)
        if namespace is not None and (
            not namespace or namespace == "functions" or not leaf or "::" in leaf
        ):
            raise ValueError(
                "use namespace::leaf for non-default namespaces, plain names for functions"
            )
        if self.namespace_description is not None and (
            namespace is None or not isinstance(self.namespace_description, str)
        ):
            raise ValueError("namespace_description requires a namespaced tool and text")
        # Keep checkpoint serialization portable. ``mappingproxy`` appears
        # immutable but cannot be encoded by LangGraph's durable serializer.
        object.__setattr__(self, "parameters", deepcopy(dict(self.parameters)))
        object.__setattr__(self, "input_kind", ToolInputKind(self.input_kind))
        if self.source_description is not None and not isinstance(self.source_description, str):
            raise ValueError("tool source_description must be a string")
        if self.freeform_format is not None:
            value = deepcopy(dict(self.freeform_format))
            if self.input_kind != ToolInputKind.FREEFORM:
                raise ValueError("freeform_format requires a freeform tool")
            if value != {"type": "text"} and not (
                value.get("type") == "grammar"
                and isinstance(value.get("syntax"), str)
                and value.get("syntax") in {"lark", "regex"}
                and isinstance(value.get("definition"), str)
                and value["definition"]
                and set(value) == {"type", "syntax", "definition"}
            ):
                raise ValueError("invalid freeform tool format")
            object.__setattr__(self, "freeform_format", value)
        if self.output_char_budget is not None and self.output_char_budget <= 0:
            raise ValueError("tool output_char_budget must be positive")

    def as_chat_completion_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": compatible_tool_name(self.name),
                "description": self.compatible_description(),
                "parameters": self.compatible_parameters(),
            },
        }

    def compatible_parameters(self) -> dict[str, Any]:
        if self.input_kind == ToolInputKind.FREEFORM:
            return {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
                "additionalProperties": False,
            }
        return deepcopy(dict(self.parameters))

    def compatible_description(self) -> str:
        if "::" not in self.name:
            return self.description
        return "\n".join(filter(None, (self.name, self.namespace_description, self.description)))

    def as_response_tool(
        self, *, native_freeform: bool = False, native_namespaces: bool = False
    ) -> dict[str, Any]:
        name = (
            split_tool_name(self.name)[1] if native_namespaces else compatible_tool_name(self.name)
        )
        description = self.description if native_namespaces else self.compatible_description()
        if native_freeform and self.input_kind == ToolInputKind.FREEFORM:
            return {
                "type": "custom",
                "name": name,
                "description": description,
                "format": deepcopy(dict(self.freeform_format or {"type": "text"})),
            }
        return {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": self.compatible_parameters(),
            "strict": False,
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One complete model request to invoke a tool."""

    id: ToolCallId
    name: str
    arguments: Mapping[str, Any] | None
    raw_arguments: str = ""
    parse_error: str | None = None
    input_kind: ToolInputKind = ToolInputKind.JSON

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_kind", ToolInputKind(self.input_kind))
        if self.input_kind == ToolInputKind.FREEFORM and (
            self.arguments is not None or not isinstance(self.raw_arguments, str)
        ):
            raise ValueError("freeform calls require raw string input and no JSON arguments")
        if self.arguments is not None:
            object.__setattr__(self, "arguments", dict(self.arguments))


@dataclass(frozen=True, slots=True)
class ImageAttachment:
    """An image returned by a tool and eligible for multimodal model input."""

    data_url: str
    detail: str = "high"


@dataclass(frozen=True, slots=True)
class TextContent:
    text: str


@dataclass(frozen=True, slots=True)
class AudioAttachment:
    data_url: str


@dataclass(frozen=True, slots=True)
class EncryptedContent:
    """Opaque model-only function output; never decode or truncate the ciphertext."""

    encrypted_content: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.encrypted_content, str):
            raise ValueError("encrypted content must be a string")
        self.encrypted_content.encode("utf-8")


ToolContent = TextContent | ImageAttachment | AudioAttachment | EncryptedContent


def content_to_payload(item: ToolContent) -> dict[str, Any]:
    if isinstance(item, TextContent):
        return {"type": "text", "text": item.text}
    if isinstance(item, ImageAttachment):
        return {"type": "image", "data_url": item.data_url, "detail": item.detail}
    if isinstance(item, AudioAttachment):
        return {"type": "audio", "data_url": item.data_url}
    if isinstance(item, EncryptedContent):
        return {"type": "encrypted_content", "encrypted_content": item.encrypted_content}
    raise ValueError("invalid tool content item")


def content_from_payload(value: Mapping[str, Any]) -> ToolContent:
    fields = dict(value)
    kind = fields.pop("type")
    classes = {
        "text": TextContent,
        "image": ImageAttachment,
        "audio": AudioAttachment,
        "encrypted_content": EncryptedContent,
    }
    if kind not in classes:
        raise ValueError("invalid tool content type")
    return classes[kind](**fields)


def content_text(items: tuple[ToolContent, ...]) -> str:
    """Lossy diagnostic projection, never the authoritative provider content."""
    return "\n".join(
        item.text
        if isinstance(item, TextContent)
        else "[Image output]"
        if isinstance(item, ImageAttachment)
        else "[Audio output]"
        if isinstance(item, AudioAttachment)
        else "[Encrypted tool output]"
        for item in items
    )


@dataclass(frozen=True, slots=True)
class ToolStateUpdate:
    """Explicit graph-state changes requested by a tool result."""

    plan: tuple[Mapping[str, str], ...] | None = None
    new_context_requested: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.new_context_requested, bool):
            raise ValueError("new_context_requested must be a boolean")
        if self.plan is not None:
            object.__setattr__(self, "plan", tuple(self.plan))


MAX_CODE_MODE_RESULT_BYTES = 32_000_000
MAX_CODE_MODE_RESULT_NODES = 1_000_000


@dataclass(frozen=True, slots=True)
class CodeModeOutput:
    """Explicit authoritative JSON value, distinct from text and from no override.

    In particular, CodeModeOutput(None) exposes JSON null. Ordinary result text
    is never implicitly parsed as JSON. Normalization belongs to the executor
    boundary so invalid extension results become error observations.
    """

    value: Any

    def normalized(self) -> CodeModeOutput:
        _validate_json_value(self.value, budget=[MAX_CODE_MODE_RESULT_NODES])
        chunks: list[str] = []
        size = 0
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        for chunk in encoder.iterencode(self.value):
            size += len(chunk.encode("utf-8"))
            if size > MAX_CODE_MODE_RESULT_BYTES:
                raise ValueError("Code Mode result exceeds the structured result byte limit")
            chunks.append(chunk)
        return CodeModeOutput(json.loads("".join(chunks)))


def _validate_json_value(value: object, depth: int = 0, *, budget: list[int]) -> None:
    # A handler may return a cyclic or heavily shared Python object, unlike
    # already-decoded wire JSON. Bound traversal before attempting serialization.
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("Code Mode result exceeds the JSON node limit")
    if depth > 64:
        raise ValueError("Code Mode result exceeds the JSON nesting limit")
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("Code Mode result JSON object keys must be strings")
            if len(key) > MAX_CODE_MODE_RESULT_BYTES:
                raise ValueError("Code Mode result exceeds the structured result byte limit")
            _validate_json_value(child, depth + 1, budget=budget)
    elif type(value) is list:
        for child in value:
            _validate_json_value(child, depth + 1, budget=budget)
    elif type(value) is str and len(value) > MAX_CODE_MODE_RESULT_BYTES:
        raise ValueError("Code Mode result exceeds the structured result byte limit")
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ValueError("Code Mode result must contain only JSON values")


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
    code_mode_output: CodeModeOutput | None = None
    content_items: tuple[ToolContent, ...] = ()
    # Executor failure, distinct from a handler returning an error-valued result.
    # Durable internally; Code Mode rejects this instead of resolving a value.
    dispatch_error: bool = False
    # Trusted handler metadata, never inferred from result text or tool names.
    contains_external_context: bool = False
    fallback_token_limit_override: int | None = None
    # Compatibility-only extra model-view cap; never truncate the durable result.
    legacy_output_char_budget: int | None = None
    # None identifies old persisted outputs which predate typed classification.
    is_tool_search_output: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "discovered_tools", tuple(self.discovered_tools))
        object.__setattr__(self, "content_items", tuple(self.content_items))
        object.__setattr__(self, "attachments", tuple(self.attachments))


def tool_spec_from_payload(value: Mapping[str, Any]) -> ToolSpec:
    """Read a persisted definition, retaining defaults for older histories."""

    fields = dict(value)
    fields["exposure"] = ToolExposure(fields.get("exposure", "direct"))
    fields["concurrency"] = ToolConcurrency(fields.get("concurrency", "exclusive"))
    return ToolSpec(**fields)


def tool_spec_to_payload(spec: ToolSpec) -> dict[str, Any]:
    """Keep old JSON definitions byte-compatible in immutable histories."""
    value = asdict(spec)
    if spec.source_description is None:
        value.pop("source_description")
    if spec.namespace_description is None:
        value.pop("namespace_description")
    if spec.input_kind == ToolInputKind.JSON:
        value.pop("input_kind")
        value.pop("freeform_format")
    return value
