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
        raise ValueError("provider and providers entries must be TOML tables")
    inline_name = inline.get("id", inline.get("name", "default"))
    name = inline_name if selected is None else selected
    if not isinstance(name, str) or not name.strip():
        raise ValueError("provider name must be a non-empty string")
    if name == inline_name:
        return name, {**profiles.get(name, {}), **inline}
    if name not in profiles:
        raise ValueError(
            f"Provider profile {name!r} is unavailable; configure [providers.{name}] "
            "or explicitly override the resume model/provider/effort."
        )
    # Profile identity and the adapter capability name are independent. A
    # private deployment can use DeepSeek/OpenAI behavior under its own ID.
    fallback = {} if name == "default" else {"name": name}
    return name, {**fallback, **profiles[name]}
