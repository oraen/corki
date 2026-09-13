"""Single-snapshot workspace evidence for the provider-neutral memory consolidator."""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath

Snapshot = dict[str, dict[str, str]]
MAX_DIFF_BYTES = 4 * 1024 * 1024


def workspace_files(root: Path) -> tuple[Path, ...]:
    if root.is_symlink():
        raise ValueError("memory root cannot be a symbolic link")
    directories, files = [root], []
    while directories:
        for path in directories.pop().iterdir():
            if path.name.startswith(".") or path == root / "phase2_workspace_diff.md":
                continue  # private state and generated evidence are not memory inputs
            if path.is_symlink():
                raise ValueError(f"memory workspace cannot contain a symbolic link: {path}")
            if path.is_dir():
                directories.append(path)
            elif path.is_file():
                files.append(path)
            else:
                raise ValueError(f"memory workspace must contain regular files: {path}")
    return tuple(sorted(files))


def capture(root: Path) -> Snapshot:
    return {
        path.relative_to(root).as_posix(): {
            "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            "mode": "100755" if path.stat().st_mode & 0o111 else "100644",
        }
        for path in workspace_files(root)
    }


def validate(value: object) -> Snapshot | None:
    if not isinstance(value, dict):
        return None
    for name, entry in value.items():
        if not isinstance(name, str):
            return None
        path = PurePosixPath(name)
        if (
            not path.parts
            or path.is_absolute()
            or path.as_posix() != name
            or any(part.startswith(".") for part in path.parts)
        ):
            return None
        if (
            not isinstance(entry, dict)
            or set(entry) != {"content", "mode"}
            or not isinstance(entry["mode"], str)
            or entry["mode"] not in {"100644", "100755"}
            or not isinstance(entry["content"], str)
        ):
            return None
        try:
            base64.b64decode(entry["content"], validate=True)
        except ValueError:
            return None
    return value


def is_output(name: str) -> bool:
    return name.startswith("skills/") or name in {"MEMORY.md", "memory_summary.md"}


def digest(snapshot: Snapshot, *, outputs: bool) -> str:
    result = hashlib.sha256()
    for name, entry in sorted(snapshot.items()):
        if is_output(name) != outputs:
            continue
        result.update(name.encode() + b"\0" + entry["mode"].encode() + b"\0")
        result.update(hashlib.sha256(base64.b64decode(entry["content"])).digest())
    return result.hexdigest()


def text(snapshot: Snapshot, name: str) -> str:
    entry = snapshot.get(name)
    return base64.b64decode(entry["content"]).decode("utf-8", errors="replace") if entry else ""


def read_previous(root: Path) -> Snapshot | None:
    path = root / ".consolidation-baseline.json"
    if path.is_symlink():
        raise ValueError("memory baseline cannot be a symbolic link")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("version") != 3:
        return None
    return validate(value.get("workspace"))


def render_diff(previous: Snapshot | None, current: Snapshot, *, max_bytes=MAX_DIFF_BYTES) -> str:
    prefix = "# Memory Workspace Diff\n\nRead this evidence before consolidating.\n\n"
    if previous is None:
        prefix += (
            "Prior content baseline unavailable (new, legacy or invalid baseline). "
            "Comparing to an empty tree; A is not proof of historical creation, "
            "and prior deletions cannot be reconstructed.\n\n"
        )
    previous = previous or {}
    changed = [
        name
        for name in sorted(previous.keys() | current.keys())
        if previous.get(name) != current.get(name)
    ]
    status, body = [], []
    for name in changed:
        old, new = previous.get(name), current.get(name)
        label = "A" if old is None else "D" if new is None else "M"
        shown = json.dumps(name, ensure_ascii=False) if "\n" in name or "\r" in name else name
        status.append(f"- {label} {shown}\n")
        body.append(f"diff --git a/{shown} b/{shown}\n")
        if old is None:
            body.append(f"new file mode {new['mode']}\n")
        elif new is None:
            body.append(f"deleted file mode {old['mode']}\n")
        elif old["mode"] != new["mode"]:
            body.append(f"old mode {old['mode']}\nnew mode {new['mode']}\n")
        for line in difflib.unified_diff(
            text(previous, name).splitlines(keepends=True),
            text(current, name).splitlines(keepends=True),
            fromfile=f"a/{shown}" if old is not None else "/dev/null",
            tofile=f"b/{shown}" if new is not None else "/dev/null",
        ):
            body.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    raw = "".join(body).encode("utf-8")
    rendered = raw[:max_bytes].decode("utf-8", errors="ignore")
    if len(raw) > max_bytes:
        rendered += f"\n[workspace diff truncated at {max_bytes} bytes]\n"
    return (
        prefix
        + "## Status\n"
        + ("".join(status) or "- none\n")
        + "\n## Diff\n\n```diff\n"
        + rendered
        + "```\n"
    )


def extension_sources(snapshot: Snapshot) -> str:
    sources = {}
    for name in sorted(snapshot):
        parts = PurePosixPath(name).parts
        if (
            len(parts) < 3
            or parts[0] != "extensions"
            or name.startswith("extensions/ad_hoc/notes/")
        ):
            continue
        extension = sources.setdefault(
            parts[1], {"instructions": "[instructions.md missing]", "resources": {}}
        )
        relative = "/".join(parts[2:])
        if relative == "instructions.md":
            extension["instructions"] = text(snapshot, name)
        else:
            extension["resources"][relative] = text(snapshot, name)
    return json.dumps(sources, ensure_ascii=False)
