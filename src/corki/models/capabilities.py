"""Provider capability profiles resolved at the composition boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ApiMode(StrEnum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"


class ReasoningProtocol(StrEnum):
    NONE = "none"
    DEEPSEEK = "deepseek"
    OPENAI_RESPONSES = "openai_responses"


class StructuredOutputProtocol(StrEnum):
    """Provider wire format used for constrained JSON model output."""

    NONE = "none"
    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    name: str
    api_mode: ApiMode
    supports_tools: bool = True
    supports_native_tool_search: bool = False
    supports_native_freeform: bool = False
    supports_audio_input: bool = False
    supports_parallel_tools: bool = True
    supports_stream_usage: bool = False
    supports_thinking_toggle: bool = False
    supports_reasoning_effort: bool = False
    reasoning_protocol: ReasoningProtocol = ReasoningProtocol.NONE
    requires_reasoning_replay: bool = False
    structured_output_protocol: StructuredOutputProtocol = StructuredOutputProtocol.NONE
    # Legacy constructor data only; no transport consults this excluded extension.
    supports_encrypted_tool_output: bool = False
    supports_native_namespaces: bool = False
    supports_remote_compaction: bool = False
    supports_service_tier: bool = False
    supports_internal_metadata: bool = False
    # Provider-owned semantic ceiling, distinct from native namespace encoding.
    # Codex providers default this to true; custom adapters may lower it.
    namespace_tools: bool = True


def resolve_capabilities(
    *, base_url: str, api_mode: str, provider_name: str | None = None
) -> ProviderCapabilities:
    """Choose ordinary transport defaults; labels and addresses grant no capabilities."""

    mode = ApiMode(api_mode)
    if mode is ApiMode.RESPONSES:
        return ProviderCapabilities(
            name="openai-compatible-responses",
            api_mode=mode,
            supports_stream_usage=True,
            supports_reasoning_effort=True,
            reasoning_protocol=ReasoningProtocol.OPENAI_RESPONSES,
            structured_output_protocol=StructuredOutputProtocol.JSON_SCHEMA,
        )
    return ProviderCapabilities(name="openai-compatible", api_mode=mode)
