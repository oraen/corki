"""Owned display source, independent of model history and terminal-wrapped bytes."""

import asyncio
from copy import deepcopy
from functools import wraps
from io import StringIO
from itertools import islice

from prompt_toolkit.application.current import set_app
from prompt_toolkit.application.run_in_terminal import in_terminal
from rich.cells import cell_len
from rich.console import Console

from corki.cli.markdown import AssistantBlock, AssistantMarkdown


def remember_display(method):
    @wraps(method)
    def render(ui, *args, **kwargs):
        source = getattr(ui, "_transcript", None)
        if (
            source is not None
            and not source.replaying
            and method.__name__
            not in {
                "show_identified_tool_started",
                "show_identified_tool_completed",
                "show_tool_output_chunk",
                "show_exploration",
            }
            and (flush := getattr(ui, "flush_exploration", None)) is not None
        ):
            flush()
        if source is not None and not source.replaying and method.__name__ != "show_exploration":
            source.calls.append((method, deepcopy(args), deepcopy(kwargs)))
        view = getattr(ui, "_history_view", None)
        if view is not None:
            with view.capture_output():
                return method(ui, *args, **kwargs)
        return method(ui, *args, **kwargs)

    return render


class Transcript:
    def __init__(self, ui):
        self.ui = ui
        self.calls = []
        self.replaying = False
        self.include_reasoning = False
        self.expand_tools = False
        self._modal_depth = 0
        self.repair_pending = False
        self.stream_reflowed = False

    @property
    def modal_depth(self):
        return self._modal_depth

    @modal_depth.setter
    def modal_depth(self, value):
        self._modal_depth = value
        status = getattr(self.ui, "_working_status", None)
        if status is not None:
            status.set_paused(value > 0)

    def assistant_stream_text(self):
        """Received source for the active display, including its provisional tail."""
        parts = []
        for method, args, _ in reversed(self.calls):
            if method.__name__ == "begin_assistant_message":
                return "".join(reversed(parts))
            if method.__name__ == "append_assistant_delta":
                parts.append(args[0])
        return ""

    def assistant_stream_is_contiguous(self):
        for method, _, _ in reversed(self.calls):
            if method.__name__ == "begin_assistant_message":
                return True
            if method.__name__ != "append_assistant_delta":
                return False
        return False

    def complete_assistant(self, text, *, already_rendered=False):
        """Replace only the latest assistant run, preserving interleaved notices."""
        start = next(
            (
                i
                for i in range(len(self.calls) - 1, -1, -1)
                if self.calls[i][0].__name__ == "begin_assistant_message"
            ),
            None,
        )
        if start is None:
            return False
        stream_methods = {
            "begin_assistant_message",
            "append_assistant_delta",
            "end_assistant_message",
        }
        streamed = "".join(
            args[0]
            for method, args, _ in self.calls[start:]
            if method.__name__ == "append_assistant_delta"
        )
        retained = [
            entry for entry in self.calls[start:] if entry[0].__name__ not in stream_methods
        ]
        final = type(self.ui).show_assistant_message.__wrapped__
        self.calls[start:] = [(final, (text,), {}), *retained]
        repair = (
            streamed != text
            or self.stream_reflowed
            or (
                self.ui._console.is_terminal
                and not already_rendered
                and (
                    AssistantMarkdown(text).needs_stream_repair
                    or cell_len(text) > max(1, self.ui._console.width - 2)
                )
            )
        )
        self.stream_reflowed = False
        self.repair_pending |= repair
        return repair

    def complete_plan(self, text):
        return self._replace_plan(type(self.ui).show_proposed_plan.__wrapped__, text)

    def interrupt_plan(self, rows):
        return self._replace_plan(type(self.ui).show_proposed_plan_fragment.__wrapped__, rows)

    def _replace_plan(self, method, source):
        start = next(
            (
                i
                for i in range(len(self.calls) - 1, -1, -1)
                if self.calls[i][0].__name__ == "begin_proposed_plan"
            ),
            None,
        )
        if start is None:
            return False
        methods = {"begin_proposed_plan", "append_proposed_plan_delta", "end_proposed_plan"}
        retained = [entry for entry in self.calls[start:] if entry[0].__name__ not in methods]
        self.calls[start:] = [(method, (source,), {}), *retained]
        self.repair_pending = True
        return True

    async def repair(self):
        view = getattr(self.ui, "_history_view", None)
        if view is not None and view.active:
            self.repair_pending = True
            self.ui._session.app.invalidate()
            return
        if self.modal_depth or not self.ui._console.is_terminal:
            return
        app = self.ui._session.app
        with set_app(app):
            async with in_terminal():
                if self.modal_depth:
                    return
                self.stream_reflowed |= next(
                    (
                        method.__name__ == "begin_assistant_message"
                        for method, _, _ in reversed(self.calls)
                        if method.__name__ in {"begin_assistant_message", "end_assistant_message"}
                    ),
                    False,
                )
                text = self.render(
                    self.ui._console.width, include_reasoning=self.ui._reasoning_expanded
                )
                app.output.write_raw("\x1b[3J\x1b[2J\x1b[H" + text)
                app.output.flush()
                self.repair_pending = False

    def render(self, width, *, include_reasoning=False, expand_tools=False, output=None):
        """Render source afresh; never append replay output to the source itself."""
        own_output = output is None
        if own_output:
            output = StringIO()
        original = self.ui._console
        plan = getattr(self.ui, "_plan_stream", None)
        plan_start = (
            next(
                (
                    i
                    for i in range(len(self.calls) - 1, -1, -1)
                    if self.calls[i][0].__name__ == "begin_proposed_plan"
                ),
                None,
            )
            if plan is not None
            else None
        )
        stream_state = {
            name: getattr(self.ui, name)
            for name in (
                "_assistant_pending",
                "_assistant_started",
                "_table_source",
                "_tail_cache",
                "_stream_markdown",
                "_plan_stream",
                "_plan_table",
                "_plan_pending",
                "_reasoning_buffer",
                "_reasoning_header",
                "_reasoning_active",
            )
            if hasattr(self.ui, name)
        }
        self.ui._console = Console(
            file=output,
            width=max(1, width),
            force_terminal=original.is_terminal,
            color_system=original.color_system,
        )
        self.replaying = True
        self.include_reasoning = include_reasoning
        self.expand_tools = expand_tools
        original_previews = getattr(self.ui, "_tool_previews", {})
        self.ui._tool_previews = {}
        original_exploration = getattr(self.ui, "_exploration", None)
        self.ui._exploration = None
        try:
            for index, (method, args, kwargs) in enumerate(self.calls):
                if plan_start is not None and index >= plan_start:
                    if index == plan_start:
                        # Browsing or resizing must not promote queued stable
                        # lines. Live tails are a separate transient display.
                        fragment = type(self.ui).show_proposed_plan_fragment.__wrapped__
                        fragment(self.ui, tuple(plan.committed_rows))
                    if method.__name__ in {
                        "begin_proposed_plan",
                        "append_proposed_plan_delta",
                        "end_proposed_plan",
                    }:
                        continue
                if include_reasoning and method.__name__ in {
                    "begin_reasoning",
                    "append_reasoning_delta",
                    "end_reasoning",
                }:
                    if method.__name__ == "begin_reasoning":
                        parts = []
                        for next_method, next_args, _ in islice(self.calls, index + 1, None):
                            name = next_method.__name__
                            if name in {"begin_reasoning", "end_reasoning"}:
                                break
                            if name == "append_reasoning_delta":
                                parts.append(next_args[0])
                        if any(parts):
                            self.ui._console.print()
                            self.ui._console.print(
                                AssistantBlock("".join(parts)), style="dim italic"
                            )
                            self.ui._console.print()
                    continue
                if method.__name__ not in {
                    "show_identified_tool_started",
                    "show_identified_tool_completed",
                    "show_tool_output_chunk",
                    "show_exploration",
                } and (flush := getattr(self.ui, "flush_exploration", None)):
                    flush()
                method(self.ui, *args, **kwargs)
            if not expand_tools and (flush := getattr(self.ui, "flush_exploration", None)):
                flush()
        finally:
            self.ui._console = original
            self.replaying = False
            self.include_reasoning = False
            self.expand_tools = False
            self.ui._tool_previews = original_previews
            self.ui._exploration = original_exploration
            for name in (
                "_assistant_pending",
                "_assistant_started",
                "_table_source",
                "_tail_cache",
                "_stream_markdown",
                "_plan_stream",
                "_plan_table",
                "_plan_pending",
                "_reasoning_buffer",
                "_reasoning_header",
                "_reasoning_active",
            ):
                if name in stream_state:
                    setattr(self.ui, name, stream_state[name])
                elif hasattr(self.ui, name):
                    delattr(self.ui, name)
        return output.getvalue() if own_output else None

    async def watch(self):
        """Debounce width/height changes without taking input away from a modal."""
        if not self.ui._console.is_terminal:
            return
        observed = self.ui._console.size
        deadline = None
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(0.025)
            current = self.ui._console.size
            if current != observed:
                observed = current
                deadline = loop.time() + 0.075
            due = deadline is not None and loop.time() >= deadline
            if not (due or self.repair_pending) or self.modal_depth:
                continue
            self.repair_pending = True
            await self.repair()
            deadline = None
