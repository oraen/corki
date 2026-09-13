import pytest

from corki.models import (
    StructuredOutputProtocol,
    resolve_capabilities,
)


def test_generic_chat_profile_does_not_guess_private_features() -> None:
    capabilities = resolve_capabilities(
        base_url="https://proxy.internal/v1", api_mode="chat_completions"
    )

    assert not capabilities.supports_thinking_toggle
    assert not capabilities.supports_reasoning_effort
    assert capabilities.structured_output_protocol is StructuredOutputProtocol.NONE


def test_api_mode_not_official_hostname_selects_default_schema_protocol() -> None:
    chat = resolve_capabilities(base_url="https://api.openai.com/v1", api_mode="chat_completions")
    responses = resolve_capabilities(base_url="https://proxy.internal/v1", api_mode="responses")

    assert chat.structured_output_protocol is StructuredOutputProtocol.NONE
    assert responses.structured_output_protocol is StructuredOutputProtocol.JSON_SCHEMA


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("provider", [None, "openai", "azure", "independent", "deepseek"])
@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://sample.openai.azure.com/v1",
        "https://fixture.invalid/v1",
        "https://deepseek.com.fixture.invalid/v1",
        "https://api.deepseek.com/v1",
    ],
)
def test_official_labels_do_not_change_ordinary_capabilities(mode, provider, url):
    expected = resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode=mode)
    assert resolve_capabilities(base_url=url, api_mode=mode, provider_name=provider) == expected
