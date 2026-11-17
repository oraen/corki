"""Public, provider-neutral transport construction for embedding hosts."""

from dataclasses import replace

from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities


def model_capabilities(settings):
    capabilities = resolve_capabilities(
        base_url=settings.api_base, api_mode=settings.api_mode, provider_name=settings.provider_name
    )
    return replace(
        capabilities,
        supports_native_tool_search=settings.tool_search_mode == "native",
        supports_native_namespaces=settings.tool_namespace_mode == "native",
        supports_audio_input=settings.supports_audio_input,
        supports_service_tier=capabilities.supports_service_tier
        if settings.supports_service_tier is None
        else settings.supports_service_tier,
    )


def create_model(settings, capabilities=None):
    if not settings.provider_enabled:
        raise ValueError("供应商已关闭，请开启供应商或选择其他模型")
    common = dict(
        api_key=settings.api_key,
        base_url=settings.api_base,
        capabilities=capabilities or model_capabilities(settings),
        reasoning_effort=settings.reasoning_effort,
        max_retries=settings.model_max_retries,
        request_max_retries=settings.model_request_max_retries,
        retry_base_seconds=settings.model_retry_base_seconds,
        response_char_limit=settings.model_response_char_limit,
    )
    if settings.api_mode == "responses":
        return OpenAIResponsesModel(**common)
    return OpenAICompatibleModel(**common, thinking_enabled=settings.thinking_enabled)
