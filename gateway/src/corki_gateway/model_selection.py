"""Connection-owned model choices; inference still uses Corki's config resolver."""


def validate_selection(document, provider, model):
    if not provider and not model:
        return
    if not provider or not model:
        raise ValueError("请同时选择供应商和模型，或沿用全局配置")
    profile = document.get("providers", {}).get(provider)
    if not isinstance(profile, dict) or profile.get("enabled", True) is False:
        raise ValueError("所选供应商已不可用，请重新选择")
    if model not in profile.get("models", {}):
        raise ValueError("所选模型已不可用，请重新选择")
