"""Resolve a provider identity from current trusted configuration, not history."""

from typing import Any


def select_provider(document: dict[str, Any], selected: str | None) -> tuple[str, dict[str, Any]]:
    """Select a named profile without inheriting another provider's credentials.

    The existing inline table remains a profile (unnamed means `default`). Its
    fields may override a same-named registry entry, never a different one.
    """
    inline = document.get("provider", {})
    profiles = document.get("providers", {})
    if (
        not isinstance(inline, dict)
        or not isinstance(profiles, dict)
        or any(not isinstance(profile, dict) for profile in profiles.values())
    ):
        raise ValueError("provider and providers entries must be objects")
    inline_name = inline.get("id", inline.get("name", "default"))
    name = inline_name if selected is None else selected
    if not isinstance(name, str) or not name.strip():
        raise ValueError("provider name must be a non-empty string")
    alias_matches = [
        {"name": identity, **profile}
        for identity, profile in profiles.items()
        if name in profile.get("aliases", [])
    ]
    if name == inline_name and (inline or name in profiles or not alias_matches):
        return name, {**profiles.get(name, {}), **inline}
    if name not in profiles:
        if len(alias_matches) == 1:
            return name, alias_matches[0]
        raise ValueError(
            f"Provider profile {name!r} is unavailable; configure [providers.{name}] "
            "or explicitly override the resume model/provider/effort."
        )
    # Profile identity and the adapter capability name are independent. A
    # private deployment can use DeepSeek/OpenAI behavior under its own ID.
    fallback = {} if name == "default" else {"name": name}
    return name, {**fallback, **profiles[name]}


def provider_settings(provider_id, provider_values, defaults):
    """Shared transport options; never inherit credentials from another profile."""
    return dict(
        api_base=provider_values.get("base_url", defaults.api_base),
        provider_name=provider_values.get("name", defaults.provider_name),
        provider_id=provider_id,
        api_key=provider_values.get("api_key"),
        api_mode=provider_values.get("api_mode", defaults.api_mode),
        supports_audio_input=provider_values.get(
            "supports_audio_input", defaults.supports_audio_input
        ),
        supports_encrypted_tool_output=provider_values.get(
            "supports_encrypted_tool_output", defaults.supports_encrypted_tool_output
        ),
        supports_image_input=provider_values.get(
            "supports_image_input", defaults.supports_image_input
        ),
        supports_image_detail_original=provider_values.get(
            "supports_image_detail_original", defaults.supports_image_detail_original
        ),
        unified_image_budget=provider_values.get(
            "unified_image_budget", defaults.unified_image_budget
        ),
        thinking_enabled=provider_values.get("thinking", defaults.thinking_enabled),
        reasoning_effort=provider_values.get("reasoning_effort", defaults.reasoning_effort),
        reasoning_summary=provider_values.get("reasoning_summary", defaults.reasoning_summary),
        service_tier=provider_values.get("service_tier"),
        supports_service_tier=provider_values.get("supports_service_tier"),
        provider_memory_extraction_model=provider_values.get("memory_extraction_model"),
        provider_memory_consolidation_model=provider_values.get("memory_consolidation_model"),
        model_max_retries=provider_values.get(
            "stream_max_retries",
            provider_values.get("max_retries", defaults.model_max_retries),
        ),
        model_request_max_retries=provider_values.get(
            "request_max_retries", defaults.model_request_max_retries
        ),
        model_unbounded_connection_retries=provider_values.get(
            "unbounded_connection_retries", defaults.model_unbounded_connection_retries
        ),
        model_retry_base_seconds=provider_values.get(
            "retry_base_seconds", defaults.model_retry_base_seconds
        ),
        model_response_char_limit=provider_values.get(
            "response_char_limit", defaults.model_response_char_limit
        ),
    )


def resolve_model_document(document, provider=None, model=None):
    """Project provider/model nesting onto the established settings parser.

    Legacy inline providers/catalogs remain readable. Model metadata belongs to
    its provider, even when two endpoints serve the same model identifier.
    """
    from copy import deepcopy

    from corki.config.model_context import parse_model_contexts

    result = deepcopy(document)
    default = result.get("default", {})
    if not isinstance(default, dict):
        raise ValueError("default must be an object with provider and model")
    if default and not {"provider", "model"}.issubset(default):
        raise ValueError("default must contain both provider and model")
    for key in ("provider", "model"):
        if key in default and (not isinstance(default[key], str) or not default[key].strip()):
            raise ValueError(f"default.{key} must be a non-empty string")
    profiles = result.get("providers", {})
    if not isinstance(profiles, dict):
        raise ValueError("providers must be an object")
    aliases_seen = set(profiles)
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise ValueError("providers entries must be objects")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("provider names must be non-empty strings")
        aliases = profile.get("aliases", [])
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in aliases
        ):
            raise ValueError("provider aliases must be an array of non-empty strings")
        for alias in aliases:
            if alias in aliases_seen:
                raise ValueError(
                    "provider aliases must be unique and must not shadow profile names"
                )
            aliases_seen.add(alias)
        if "models" in profile:
            if not isinstance(profile["models"], dict) or not profile["models"]:
                raise ValueError("provider.models must be a non-empty object")
            parse_model_contexts(profile["models"])
    provider = provider or default.get("provider")
    if (
        provider is not None
        and provider not in aliases_seen
        and "provider" not in result
        and any("models" in profile for profile in profiles.values())
    ):
        raise ValueError(f"Provider profile {provider!r} is unavailable")
    identity, profile = select_provider(result, provider)
    catalog = profile.get("models")
    if model is None:
        if identity == default.get("provider"):
            model = default.get("model")
            if catalog is not None and model is not None and model not in catalog:
                raise ValueError("default.model is not configured under default.provider")
        if model is None and catalog is not None:
            model = next(iter(catalog))
    if model is None:
        model = result.get("agent", {}).get("model", "gpt-5")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    if catalog is not None:
        entry = catalog.get(model, {})
        # Model fields can tune requests, but cannot redirect credentials/endpoints.
        request_fields = {"reasoning_effort", "reasoning_summary", "thinking", "service_tier"}
        profile = {**profile, **{key: entry[key] for key in request_fields if key in entry}}
        result["models"] = {**result.get("models", {}), "catalog": catalog}
    result["provider"] = {**profile, "id": identity}
    result["agent"] = {**result.get("agent", {}), "model": model}
    return result
