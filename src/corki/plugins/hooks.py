"""Capture Legacy hook definitions without granting authority or executing code."""

import json
from dataclasses import dataclass
from pathlib import Path

from corki.plugins.agent_hooks import _file


@dataclass(frozen=True, slots=True)
class PluginHookFile:
    relative_path: str
    contents: str
    data_root: Path


def load_plugin_hooks(root: Path, selection, *, data_root: Path | None = None):
    sources, warnings = [], []
    data_root = data_root or root / ".plugin-data"
    paths, inline = [], []
    values = selection if isinstance(selection, list) else [selection]
    if selection is not None and values and all(isinstance(value, str) for value in values):
        for value in values:
            relative = Path(value[2:]) if value.startswith("./") else None
            if relative is None or relative.is_absolute() or ".." in relative.parts:
                warnings.append("ignoring hooks: expected a contained ./ path")
                continue
            paths.append(root / relative)
    elif selection is not None and values and all(isinstance(value, dict) for value in values):
        try:
            for value in values:
                _file(value)
            inline = values
        except ValueError as error:
            warnings.append(f"ignoring hooks: {error}")
    elif selection is not None:
        warnings.append("ignoring hooks: expected paths or inline hook files")
    if not paths and not inline:
        default = root / "hooks/hooks.json"
        if default.is_file():
            paths.append(default)
    for index, value in enumerate(inline):
        sources.append(PluginHookFile(f"plugin.json#hooks[{index}]", json.dumps(value), data_root))
    for path in paths:
        try:
            contents = path.read_text(encoding="utf-8")
            _file(json.loads(contents))
            relative = str(path.relative_to(root)).replace("\\", "/")
            sources.append(PluginHookFile(relative, contents, data_root))
        except (OSError, UnicodeError, ValueError) as error:
            warnings.append(f"Failed to load plugin hooks in {path}: {error}")
    return tuple(sources), tuple(warnings)
