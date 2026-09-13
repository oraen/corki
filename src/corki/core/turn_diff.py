"""Turn-owned net text changes; state transitions mirror the pinned native tracker."""

import asyncio
import json
from pathlib import Path

from corki.execution.owned_process import run_owned
from corki.execution.response import decode_helper_response
from corki.protocol.events import TurnDiff, WarningEvent
from corki.protocol.patches import parse_patch_delta


async def render_diff(compiler, cwd, left_path, left, right_path, right):
    if compiler is None:
        raise ValueError("native patch diff renderer is unavailable")
    payload = {
        "render_patch_diff": {
            "cwd": Path(cwd).as_uri(),
            "left_path": Path(left_path).as_uri(),
            "right_path": Path(right_path).as_uri(),
            "left_content": left,
            "right_content": right,
        }
    }
    response = await run_owned(
        [str(compiler)],
        json.dumps(payload).encode() + b"\n",
        cwd=Path(cwd),
        output_limit=8_000_000,
    )
    value = decode_helper_response(
        response, expected=dict, error_prefix="patch diff rendering failed"
    )
    if (
        set(value) != {"version", "diff"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["diff"] is not None
        and not isinstance(value["diff"], str)
    ):
        raise ValueError("invalid native patch diff response")
    return value["diff"]


class TurnDiffTracker:
    def __init__(self, renderer=render_diff):
        self.valid = True
        self.baseline, self.current, self.origins = {}, {}, {}
        self.revision = 0
        self.rendered = {}
        self.diff = None
        self.seen = set()
        self.pending = set()
        self.clear_pending = False
        self.published = False
        self.deferred = False
        self._lock = asyncio.Lock()
        self._render = renderer

    def started(self, call_id):
        self.pending.add(call_id)

    def invalidate(self):
        self.clear_pending |= self.diff is not None
        self.valid = False
        self.rendered.clear()
        self.diff = None

    def final_clear(self, thread, turn, *, aborted=False):
        if self.pending:
            self.invalidate()
        if aborted and not self.valid and self.published:
            self.clear_pending = True
        if self.clear_pending or self.deferred:
            diff = "" if self.clear_pending else self.diff or ""
            self.clear_pending = False
            self.deferred = False
            return TurnDiff(thread, turn, diff)
        return None

    def _content(self, text):
        self.revision += 1
        return self.revision, text

    async def step_completed(self, thread, turn, events):
        async with self._lock:
            if self.diff is not None:
                await events.emit(TurnDiff(thread, turn, self.diff))
                self.published = True
                self.deferred = False

    def _baseline(self, path, text):
        if path not in self.current and path not in self.baseline and text is not None:
            self.baseline[path] = self._content(text)

    def _change(self, entry):
        path, change = entry["path"], entry["change"]
        kind = change["kind"]
        if kind == "add":
            self.origins.pop(path, None)
            self._baseline(path, change["overwritten_content"])
            self.current[path] = self._content(change["content"])
        elif kind == "delete":
            if self.current.pop(path, None) is None and path not in self.baseline:
                self.baseline[path] = self._content(change["content"])
            self.origins.pop(path, None)
        else:
            self._baseline(path, change["old_content"])
            dest = change["move_path"]
            if dest is None:
                self.current[path] = self._content(change["new_content"])
            else:
                self._baseline(dest, change["overwritten_move_content"])
                origin = self.origins.pop(path, path)
                self.current.pop(path, None)
                self.current[dest] = self._content(change["new_content"])
                self.origins.pop(dest, None)
                if dest != origin:
                    self.origins[dest] = origin

    async def _refresh(self, compiler, cwd):
        renames = {
            origin: dest
            for dest, origin in self.origins.items()
            if dest != origin
            and origin not in self.current
            and dest in self.current
            and origin in self.baseline
            and dest not in self.baseline
        }
        destinations = set(renames.values())
        rendered, parts, handled = {}, [], set()

        def display(path):
            try:
                return str(Path(path).relative_to(cwd))
            except ValueError:
                return path

        for path in sorted(self.baseline.keys() | self.current.keys(), key=display):
            if path in handled:
                continue
            handled.add(path)
            if path in destinations:
                continue
            right_path = renames.get(path, path)
            handled.add(right_path)
            left, right = self.baseline.get(path), self.current.get(right_path)
            key = path, left[0] if left else None, right_path, right[0] if right else None
            if key in self.rendered:
                diff = self.rendered[key]
            else:
                diff = await self._render(
                    compiler,
                    cwd,
                    path,
                    left[1] if left else None,
                    right_path,
                    right[1] if right else None,
                )
            rendered[key] = diff
            if diff is not None:
                parts.append(diff if diff.endswith("\n") else diff + "\n")
        self.rendered = rendered
        self.diff = "".join(parts) or None

    async def observe(self, result, *, is_patch, compiler, cwd, thread, turn, events, publish=True):
        if result.patch_delta_json is None and not is_patch:
            return
        async with self._lock:
            if result.call_id in self.seen:
                return
            self.seen.add(result.call_id)
            self.pending.discard(result.call_id)
            before = self.diff
            try:
                if result.patch_delta_json is None:
                    self.invalidate()
                else:
                    delta = parse_patch_delta(result.patch_delta_json)
                    if not delta["exact"]:
                        self.invalidate()
                    elif not delta["changes"]:
                        return
                    elif self.valid and delta["changes"]:
                        for change in delta["changes"]:
                            self._change(change)
                        await self._refresh(compiler, cwd)
                if before is not None or self.diff is not None or self.deferred:
                    if not publish:
                        self.deferred = True
                        return
                    await events.emit(TurnDiff(thread, turn, self.diff or ""))
                    self.published |= self.diff is not None
                    self.clear_pending = False
                    self.deferred = False
            except BaseException as error:
                self.invalidate()
                if not isinstance(error, Exception):
                    raise
                if not publish:
                    self.deferred |= self.clear_pending
                    return
                if self.clear_pending:
                    await events.emit(TurnDiff(thread, turn, ""))
                    self.clear_pending = False
                await events.emit(WarningEvent(thread, turn, f"Turn diff unavailable: {error}"))
