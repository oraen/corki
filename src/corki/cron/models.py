"""Validate task model identities against live configuration; never persist credentials."""

from corki.config.document import loads
from corki.config.providers import select_provider


def validate_model(home, provider, model):
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 500
        for value in (provider, model)
    ):
        raise ValueError("请选择模型供应商和运行模型")
    try:
        config = loads((home / "settings.json").read_text())
        _, profile = select_provider(config, provider)
        models = profile.get("models", {})
        if not isinstance(models, dict) or model not in models:
            raise ValueError("model unavailable")
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise ValueError("所选供应商或模型已不可用，请重新选择") from error
    return provider, model
