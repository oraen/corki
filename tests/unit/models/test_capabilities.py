from corki.models import (
    ApiMode,
    ReasoningProtocol,
    StructuredOutputProtocol,
    resolve_capabilities,
)


def test_explicit_deepseek_profile_controls_private_request_fields() -> None:
    capabilities = resolve_capabilities(
        base_url="https://proxy.internal/v1",
        api_mode="chat_completions",
        provider_name="deepseek",
    )

    assert capabilities.name == "deepseek"
    assert capabilities.api_mode is ApiMode.CHAT_COMPLETIONS
    assert capabilities.supports_thinking_toggle
    assert capabilities.requires_reasoning_replay
    assert capabilities.reasoning_protocol is ReasoningProtocol.DEEPSEEK
    assert capabilities.structured_output_protocol is StructuredOutputProtocol.JSON_OBJECT


def test_generic_chat_profile_does_not_guess_private_features() -> None:
    capabilities = resolve_capabilities(
        base_url="https://proxy.internal/v1", api_mode="chat_completions"
    )

    assert not capabilities.supports_thinking_toggle
    assert not capabilities.supports_reasoning_effort
    assert capabilities.structured_output_protocol is StructuredOutputProtocol.NONE


def test_openai_profiles_use_strict_json_schema_output() -> None:
    chat = resolve_capabilities(base_url="https://api.openai.com/v1", api_mode="chat_completions")
    responses = resolve_capabilities(base_url="https://proxy.internal/v1", api_mode="responses")

    assert chat.structured_output_protocol is StructuredOutputProtocol.JSON_SCHEMA
    assert responses.structured_output_protocol is StructuredOutputProtocol.JSON_SCHEMA
