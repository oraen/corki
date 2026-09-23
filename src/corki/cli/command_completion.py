"""Completion for local commands; command execution remains in the dispatcher."""

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.filters import Condition

COMMANDS = (
    ("help", "Show available commands"),
    ("status", "Show current session configuration"),
    ("clear", "Clear the terminal"),
    ("model", "Show or change the session model"),
    ("plan", "Enter Plan mode"),
    ("compact", "Summarize conversation history"),
    ("memory", "Memory reset and source settings"),
    ("mcp", "List discovered MCP tools"),
    ("realtime", "Configure live steering"),
    ("stop", "Stop the active turn"),
)


class CommandCompleter(Completer):
    def __init__(self):
        self.dismissed_text = None

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
