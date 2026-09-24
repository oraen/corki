"""Completion for local commands; command execution remains in the dispatcher."""

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.filters import Condition

COMMANDS = (
    ("help", "Show available commands"),
    ("status", "Show current session configuration"),
    ("clear", "Clear the terminal"),
    ("copy", "Copy the last response, code block, or quote"),
    ("model", "Show or change the session model"),
    ("plan", "Enter Plan mode"),
    ("compact", "Summarize conversation history"),
    ("memory", "Memory reset and source settings"),
    ("mcp", "List discovered MCP tools"),
    ("realtime", "Configure live steering"),
    ("stop", "Stop the active turn"),
)


class CommandCompleter(Completer):
    def __init__(self, cwd=None):
        self.dismissed_text = None
        self.cwd = cwd
        self.references = None
        self.reference_error = None
        self._generation = 0

    def reset_reference_error(self):
        self._generation += 1
        self.reference_error = None

    def reference_error_for(self, document):
        error = self.reference_error
        if error is not None and error[:2] == (document.text, document.cursor_position):
            return error[2]
        return None

    async def get_completions_async(self, document, complete_event):
        from corki.cli.reference_completion import (
            FileSearchUnavailable,
            ReferenceCompletion,
            catalog,
            files,
            reference_match_score,
            target,
        )

        self.reset_reference_error()
        generation = self._generation
        failures = []

        if document.text == self.dismissed_text:
            return
        token = target(document)
        if token is None:
            for completion in self.get_completions(document, complete_event):
                yield completion
            return
        sigil, query, start = token
        rows = []
        if self.references is not None:
            try:
                skills, plugins = await self.references()
                rows.extend(catalog(skills, plugins, sigil))
            except (OSError, ValueError):
                failures.append("Reference catalog unavailable. Try again.")
        # A slow catalog load may outlive the token/cwd that requested it.
        # Do not start an obsolete filesystem scan merely to discard it later.
        if generation != self._generation:
            return
        # Codex's empty @ query clears file search; local skills/plugins can
        # still be suggested without enumerating the entire working directory.
        if sigil == "@" and query and self.cwd is not None:
            try:
                rows.extend(await files(self.cwd, query))
            except FileSearchUnavailable as error:
                failures.append(str(error))
        if generation != self._generation:
            return
        if failures:
            from prompt_toolkit.application.current import get_app

            self.reference_error = (document.text, document.cursor_position, " ".join(failures))
            get_app().invalidate()
        scored = []
        for row in rows:
            score = reference_match_score(row, query)
            if score is not None:
                scored.append((row.rank, score, row.display_name or row.label, row))
        scored.sort(key=lambda entry: entry[:3])
        for _, _, _, row in scored[:100]:
            yield ReferenceCompletion(row, document, start)

    def get_completions(self, document, complete_event):
        text = document.text
        if text == self.dismissed_text:
            return
        if document.cursor_position != len(text) or not text.startswith("/"):
            return
        if any(char.isspace() for char in text):
            return
        for name, description in COMMANDS:
            command = "/" + name
            if command.startswith(text.lower()):
                yield Completion(command, start_position=-len(text), display_meta=description)


def bind_command_navigation(bindings, session, history_view):
    """Highlight candidates without replacing the draft before acceptance."""

    def select_first(buffer):
        state = buffer.complete_state
        if state is not None and state.completions and state.complete_index is None:
            if len(state.completions) == 1 and state.completions[0].text == buffer.text:
                return
            state.go_to_index(0)

    session.default_buffer.on_completions_changed += select_first
    active = Condition(
        lambda: not history_view.active and session.default_buffer.complete_state is not None
    )
    for key, delta in (("up", -1), ("c-p", -1), ("down", 1), ("c-n", 1)):

        @bindings.add(key, filter=active)
        def move_selection(event, delta=delta):
            state = event.current_buffer.complete_state
            if state is not None and state.completions:
                index = state.complete_index if state.complete_index is not None else 0
                state.go_to_index((index + delta) % len(state.completions))
                event.app.invalidate()


def complete_command(buffer, *, trailing_space=False):
    """Accept a selected/default candidate without ever submitting input."""
    state = buffer.complete_state
    selected = state.current_completion if state is not None else None
    if selected is None:
        completer = buffer.completer
        # PromptSession wraps its completer in DynamicCompleter. Delegate through
        # that wrapper so the live provider and its dismissal state are preserved.
        if completer is None:
            return False
        selected = next(completer.get_completions(buffer.document, CompleteEvent()), None)
    if selected is None:
        return False
    buffer.apply_completion(selected)
    if trailing_space and not buffer.text.endswith(" "):
        buffer.insert_text(" ")
    return True
