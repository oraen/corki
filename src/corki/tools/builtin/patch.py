"""Structured workspace editing through the ``apply_patch`` tool."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from corki.execution.approvals import ExecutionApprovals
from corki.execution.backend import patch_operation
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools.base import ToolContext
from corki.tools.owned_work import joined_tool_work


@dataclass(frozen=True, slots=True)
class _Operation:
    kind: str
    path: str
    body: tuple[str, ...]
    move_to: str | None = None


class ApplyPatchTool:
    def __init__(self, approvals: ExecutionApprovals | None = None):
        self._approvals = approvals

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="apply_patch",
            description=(
                "Apply a patch using *** Begin Patch / Add File / Update File / "
                "Delete File directives. Relative paths resolve against the working directory; "
                "configured execution permissions govern file access."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "description": (
                            "The patch text, supplied as this JSON string, not a shell command. "
                            "Start with *** Begin Patch and end with *** End Patch, each on its "
                            "own line. Include one or more file operations. "
                            "*** Add File: path is followed by content lines, "
                            "each prefixed with +. "
                            "*** Delete File: path has no content lines. "
                            "*** Update File: path is followed by @@ hunks: prefix unchanged "
                            "context lines with a space, removed lines with -, and added lines "
                            "with +. Include enough unchanged context to identify the edit. "
                            "To move a file while editing it, place *** Move to: new/path "
                            "immediately after its Update File header, before the @@ hunks. "
                            "Use workspace-relative paths and actual newline characters "
                            "in the decoded string."
                        ),
                    }
                },
                "required": ["patch"],
                "additionalProperties": False,
            },
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        assert call.arguments is not None
        if context.execution_permissions is None:
            # Explicit embedding-host compatibility path, not the default native
            # parser or its sequential / partial-failure execution semantics.
            operations = _parse_patch(str(call.arguments["patch"]))
            summary = await joined_tool_work(_apply_operations, context.cwd, operations)
        else:
            try:
                outcome = await patch_operation(
                    context.execution_permissions,
                    context.cwd,
                    {"patch": str(call.arguments["patch"])},
                    approvals=self._approvals,
                    call_id=call.id,
                    on_started=context.on_patch_started,
                )
            except Exception as error:
                # Backend captures all post-start ordinary failures as inexact
                # outcomes. These exceptions precede execution; no writes occurred.
                return ToolResult(
                    call.id,
                    call.name,
                    f"{type(error).__name__}: {error}",
                    is_error=True,
                    patch_delta_json='{"version":1,"exact":true,"changes":[]}',
                )
            return ToolResult(
                call.id,
                call.name,
                outcome.output,
                is_error=not outcome.success,
                display_content=outcome.output,
                patch_delta_json=outcome.delta_json,
            )
        return ToolResult(call.id, call.name, summary, display_content=summary)


def _apply_operations(cwd: Path, operations: tuple[_Operation, ...]) -> str:
    """Preflight and atomically commit a multi-file logical transaction.

    Atomic rename gives each individual file a clean replacement. A retained
    byte-for-byte snapshot provides transaction-style rollback if a later
    rename or deletion fails, so a multi-file patch is never left half-applied.
    """

    virtual: dict[Path, str | None] = {}
    desired_modes: dict[Path, int] = {}
    changed: list[str] = []

    def current(path: Path) -> str | None:
        if path in virtual:
            return virtual[path]
        # Preserve original line endings for the compatibility hunk editor.
        # Text-mode universal newline conversion would erase them before editing.
        return path.read_bytes().decode("utf-8") if path.is_file() else None

    for operation in operations:
        path = _workspace_path(cwd, operation.path)
        existing = current(path)
        if operation.kind == "add":
            if existing is not None:
                raise ValueError(f"cannot add existing file: {operation.path}")
            virtual[path] = _added_content(operation.body)
            changed.append(f"added {operation.path}")
        elif operation.kind == "delete":
            if existing is None:
                raise ValueError(f"cannot delete missing file: {operation.path}")
            virtual[path] = None
            changed.append(f"deleted {operation.path}")
        else:
            if existing is None:
                raise ValueError(f"cannot update missing file: {operation.path}")
            updated = _apply_hunks(existing, operation.body, operation.path)
            destination = _workspace_path(cwd, operation.move_to) if operation.move_to else path
            if destination != path and current(destination) is not None:
                raise ValueError(f"move destination already exists: {operation.move_to}")
            virtual[destination] = updated
            if path.is_file():
                desired_modes[destination] = stat.S_IMODE(path.stat().st_mode)
            if destination != path:
                virtual[path] = None
                changed.append(f"moved {operation.path} -> {operation.move_to}")
            else:
                changed.append(f"updated {operation.path}")

    originals: dict[Path, tuple[bytes, int] | None] = {}
    staged: dict[Path, Path] = {}
    try:
        for path, content in virtual.items():
            originals[path] = (
                (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.is_file() else None
            )
            if content is None:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.corki-", dir=path.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content.encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                previous = originals[path]
                mode = desired_modes.get(path, previous[1] if previous is not None else 0o644)
                temporary.chmod(mode)
                staged[path] = temporary
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise

        for path, temporary in staged.items():
            os.replace(temporary, path)
        for path, content in virtual.items():
            if content is None and path.exists():
                path.unlink()
    except BaseException:
        _restore_originals(originals)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
    return "\n".join(changed)


def _restore_originals(originals: dict[Path, tuple[bytes, int] | None]) -> None:
    """Best-effort rollback; surface a rollback failure over silent damage."""

    errors: list[OSError] = []
    for path, original in originals.items():
        try:
            if original is None:
                path.unlink(missing_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.corki-rollback-", dir=path.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(original[0])
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.chmod(original[1])
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError(f"patch rollback failed: {errors[0]}") from errors[0]


def _parse_patch(patch: str) -> tuple[_Operation, ...]:
    lines = patch.splitlines()
    if len(lines) < 2 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("patch must start with '*** Begin Patch' and end with '*** End Patch'")
    operations: list[_Operation] = []
    index = 1
    while index < len(lines) - 1:
        header = lines[index]
        if header.startswith("*** Add File: "):
            kind, path = "add", header.removeprefix("*** Add File: ")
        elif header.startswith("*** Update File: "):
            kind, path = "update", header.removeprefix("*** Update File: ")
        elif header.startswith("*** Delete File: "):
            kind, path = "delete", header.removeprefix("*** Delete File: ")
        else:
            raise ValueError(f"unexpected patch directive: {header}")
        if not path.strip():
            raise ValueError("patch path must not be empty")
        index += 1
        move_to: str | None = None
        if kind == "update" and index < len(lines) - 1 and lines[index].startswith("*** Move to: "):
            move_to = lines[index].removeprefix("*** Move to: ")
            index += 1
        body: list[str] = []
        while index < len(lines) - 1 and not lines[index].startswith(
            ("*** Add File: ", "*** Update File: ", "*** Delete File: ")
        ):
            body.append(lines[index])
            index += 1
        if kind != "delete" and not body:
            raise ValueError(f"{kind} operation for {path} has no content")
        operations.append(_Operation(kind, path, tuple(body), move_to))
    if not operations:
        raise ValueError("patch contains no operations")
    return tuple(operations)


def _workspace_path(cwd: Path, raw_path: str | None) -> Path:
    if not raw_path:
        raise ValueError("patch path must not be empty")
    relative = Path(raw_path)
    if relative.is_absolute():
        raise ValueError(f"patch path must be relative: {raw_path}")
    resolved = (cwd / relative).resolve()
    try:
        resolved.relative_to(cwd.resolve())
    except ValueError as exc:
        raise ValueError(f"patch path escapes workspace: {raw_path}") from exc
    return resolved


def _added_content(body: tuple[str, ...]) -> str:
    if any(not line.startswith("+") for line in body):
        raise ValueError("every Add File content line must start with '+'")
    return "\n".join(line[1:] for line in body) + "\n"


def _apply_hunks(original: str, body: tuple[str, ...], path: str) -> str:
    raw_lines = original.splitlines(keepends=True)
    source: list[str] = []
    endings: list[str] = []
    for raw in raw_lines:
        line = raw.rstrip("\r\n")
        source.append(line)
        endings.append(raw[len(line) :])
    preferred_ending = next((ending for ending in endings if ending), "\n")
    final_newline = original.endswith("\n")
    cursor = 0
    index = 0
    while index < len(body):
        if not body[index].startswith("@@"):
            raise ValueError(f"update for {path} expected a @@ hunk header")
        index += 1
        hunk: list[str] = []
        while index < len(body) and not body[index].startswith("@@"):
            if body[index] == "\\ No newline at end of file":
                index += 1
                continue
            if not body[index].startswith((" ", "+", "-")):
                raise ValueError(f"invalid hunk line in {path}: {body[index]!r}")
            hunk.append(body[index])
            index += 1
        old = [line[1:] for line in hunk if line.startswith((" ", "-"))]
        new = [line[1:] for line in hunk if line.startswith((" ", "+"))]
        location = _find_sequence(source, old, cursor)
        if location is None:
            context = "\n".join(old[:6])
            raise ValueError(f"patch context not found in {path}:\n{context}")
        original_endings = endings[location : location + len(old)]
        replacement_endings: list[str] = []
        consumed = 0
        for line in hunk:
            if line.startswith(" "):
                replacement_endings.append(original_endings[consumed])
                consumed += 1
            elif line.startswith("-"):
                consumed += 1
            else:
                replacement_endings.append(preferred_ending)
        source[location : location + len(old)] = new
        endings[location : location + len(old)] = replacement_endings
        cursor = location + len(new)
    if source and final_newline and not endings[-1]:
        endings[-1] = preferred_ending
    if source and not final_newline:
        endings[-1] = ""
    return "".join(line + ending for line, ending in zip(source, endings, strict=True))


def _find_sequence(source: list[str], wanted: list[str], start: int) -> int | None:
    if not wanted:
        return start
    for index in range(start, len(source) - len(wanted) + 1):
        if source[index : index + len(wanted)] == wanted:
            return index
    return None
