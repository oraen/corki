"""Bounded failure diagnostics, with configured credentials removed before storage."""

import re
import traceback

from corki.config import document
from corki.memory.sanitizer import redact_secrets


def safe_log(home, text):
    def secrets(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    isinstance(child, str)
                    and child
                    and any(
                        word in key.lower()
                        for word in ("api_key", "token", "secret", "password", "authorization")
                    )
                ):
                    yield child
                else:
                    yield from secrets(child)
        elif isinstance(value, list):
            for child in value:
                yield from secrets(child)

    try:
        config = document.loads((home / "settings.json").read_text(encoding="utf-8"))
        for secret in sorted(set(secrets(config)), key=len, reverse=True):
            text = text.replace(secret, "[已隐藏]")
    except (OSError, ValueError):
        pass
    text = redact_secrets(text)
    text = re.sub(r'(?i)(Bearer\s+)[^\s"\\]+|\bsk-[\w-]+|\bgh[pousr]_[\w]+', "[已隐藏]", text)
    return text[-64000:]


def exception_log(home, error):
    text = "".join(traceback.format_exception(error))
    detail = getattr(error, "diagnostic", "")
    if detail:
        text += "\nAgent error:\n" + detail
    return safe_log(home, text)
