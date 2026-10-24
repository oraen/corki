"""Validate plugin and marketplace identifiers before using them in commands."""

import re
import sys


def configure_stdio() -> None:
    """Keep standalone CLI output readable through Windows pipes."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def validate_plugin_identifier(plugin_name: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", plugin_name) is None:
        raise ValueError(
            "Plugin name may only contain ASCII letters, digits, `.`, `_`, and `-`, "
            "with dots separating non-empty name segments."
        )


def validate_marketplace_name(marketplace_name: str) -> None:
    if not marketplace_name:
        raise ValueError("Marketplace name must include at least one letter or digit.")
    if re.fullmatch(r"[A-Za-z0-9_-]+", marketplace_name) is None:
        raise ValueError(
            "Marketplace name may only contain ASCII letters, digits, `_`, and `-`."
        )
