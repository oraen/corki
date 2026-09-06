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
    supports_parallel_tools: bool = True
    supports_stream_usage: bool = False
    supports_thinking_toggle: bool = False
    supports_reasoning_effort: bool = False
    reasoning_protocol: ReasoningProtocol = ReasoningProtocol.NONE
    requires_reasoning_replay: bool = False
    structured_output_protocol: StructuredOutputProtocol = StructuredOutputProtocol.NONE


def resolve_capabilities(
    *, base_url: str, api_mode: str, provider_name: str | None = None
) -> ProviderCapabilities:
    """Resolve protocol behavior without leaking hostname checks into adapters."""

    mode = ApiMode(api_mode)
    detected_name = (provider_name or "").strip().lower()
    if not detected_name and "deepseek.com" in base_url.lower():
        detected_name = "deepseek"
    if detected_name == "deepseek":
        return ProviderCapabilities(
            name="deepseek",
            api_mode=mode,
            supports_stream_usage=True,
            supports_thinking_toggle=True,
            supports_reasoning_effort=True,
            reasoning_protocol=ReasoningProtocol.DEEPSEEK,
            requires_reasoning_replay=True,
            structured_output_protocol=StructuredOutputProtocol.JSON_OBJECT,
        )
    if mode is ApiMode.RESPONSES:
        return ProviderCapabilities(
            name="openai-compatible-responses",
            api_mode=mode,
            supports_stream_usage=True,
            supports_reasoning_effort=True,
            reasoning_protocol=ReasoningProtocol.OPENAI_RESPONSES,
            structured_output_protocol=StructuredOutputProtocol.JSON_SCHEMA,
        )
    if detected_name == "openai" or "api.openai.com" in base_url.lower():
        return ProviderCapabilities(
            name="openai-compatible",
            api_mode=mode,
            structured_output_protocol=StructuredOutputProtocol.JSON_SCHEMA,
        )
    return ProviderCapabilities(name="openai-compatible", api_mode=mode)
