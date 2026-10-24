"""Lossless value migration of a legacy user configuration to JSON settings."""

import json
import os
import tomllib
from pathlib import Path

import tomlkit
from tomlkit.items import Comment, Whitespace

from corki.config.document import dumps, loads
from corki.config.toml_edits import write_text


def convert_toml(contents: str, *, source: Path | None = None, target: Path | None = None) -> str:
    values = tomllib.loads(contents)
    if source is not None and target is not None:
        hooks = values.get("hooks", {})
        states = hooks.get("state", {}) if isinstance(hooks, dict) else {}
        if isinstance(states, dict):
            for prefix in ("", "file:"):
                old = f"{prefix}{os.path.abspath(source)}:"
                new = f"{prefix}{os.path.abspath(target)}:"
                for key, value in list(states.items()):
                    if key.startswith(old):
                        replacement = new + key[len(old):]
                        # An explicit new-path entry retains its authority, including revocation.
                        states.setdefault(replacement, value)
    comments = []

    def retain(comment):
        text = comment.lstrip().removeprefix("#").strip()
        if not text:
            return
        try:
            example = tomllib.loads(text)
            if example and not text.startswith("["):
                text = json.dumps(example, ensure_ascii=False)[1:-1]
        except ValueError:
            pass
        comments.extend("// " + line for line in text.splitlines())

    def visit(item):
        body = getattr(item, "body", None)
        if body is None:
            body = getattr(getattr(item, "value", None), "body", None)
        if body is not None:
            for entry in body:
                child = entry[1] if isinstance(entry, tuple) else entry
                if isinstance(child, Whitespace):
                    continue
                if isinstance(child, Comment):
                    retain(child.as_string())
                else:
                    trivia = getattr(child, "trivia", None)
                    if trivia is not None and trivia.comment:
                        retain(trivia.comment)
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(tomlkit.parse(contents))
    result = dumps(values)
    if comments:
        result += "\n// Preserved notes and disabled settings from config.toml:\n"
        result += "\n".join(comments) + "\n"
    if loads(result) != values:
        raise ValueError("Settings migration changed configuration values")
    return result


def migrate_user_file(target: Path) -> bool:
    legacy = target.with_name("config.toml")
    if target.exists() or not legacy.is_file():
        return False
    converted = convert_toml(legacy.read_text(encoding="utf-8"), source=legacy, target=target)
    # The legacy file remains an exact backup and is ignored while JSON exists.
    if os.name == "nt":
        from corki.platforms.windows_private_files import restrict_file

        restrict_file(legacy)
    legacy.chmod(0o600)
    write_text(target, converted)
    return True
