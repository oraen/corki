from io import StringIO
from types import SimpleNamespace

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.keys import Keys
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.pending_input import pending_input_lines
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings


def test_pending_preview_wraps_and_limits_each_message_without_changing_source():
    messages = ("first\nsecond\nthird\nfourth\nfifth", "<b>literal</b>")
    assert pending_input_lines(messages, 40) == [
        "• Queued follow-up inputs",
        "  ↳ first",
        "    second",
        "    third",
        "    …",
        "  ↳ <b>literal</b>",
    ]
    narrow = pending_input_lines(("word " * 30,), 12)
    assert narrow[-1] == "    …"
    assert all(len(line) <= 12 for line in narrow)
    assert messages[0].endswith("fifth")
    assert pending_input_lines(messages, 3) == []
    assert pending_input_lines((), 40) == []


def test_pending_preview_is_transient_and_does_not_write_history(tmp_path):
    output = StringIO()
    history = tmp_path / "input-history"
    ui = TerminalUI(CorkiSettings(tmp_path), history, console=Console(file=output))
    ui.set_pending_inputs(("<b>unsent input</b>",))
    assert "<b>unsent input</b>" in "".join(text for _, text in ui._pending_input_fragments())
    assert ui._transcript.calls == []
    assert ui._transcript.render(40) == output.getvalue() == ""
    assert not history.exists()
    ui.set_pending_inputs(())
    assert ui._pending_input_fragments() == []
    ui._draft = "existing draft"
    ui.restore_queued_inputs(("first", "second"))
    assert ui._draft == "first\nsecond\nexisting draft"
    assert ui._transcript.calls == [] and not history.exists()


def test_edit_last_queued_input_restores_buffer_without_submitting(tmp_path):
    settings = CorkiSettings(tmp_path)
    ui = TerminalUI(settings, tmp_path / "history", console=Console(file=StringIO()))
    app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), object(), ui)
    app._pending_messages.extend(("first", "last\nmultiline"))
    app._refresh_pending_inputs()
    buffer = Buffer(validate_while_typing=False)
    buffer.text = "existing draft"
    bindings = ui._bindings.get_bindings_for_keys((Keys.Escape, Keys.Up))
    assert bindings, "Alt+Up must be wired to queue editing"
    edit = bindings[-1].handler
    edit(SimpleNamespace(current_buffer=buffer))
    assert buffer.text == "last\nmultiline" and buffer.cursor_position == len(buffer.text)
    assert tuple(app._pending_messages) == ui._pending_inputs == ("first",)
    edit(SimpleNamespace(current_buffer=buffer))
    assert buffer.text == "first" and not app._pending_messages and not ui._pending_inputs
    edit(SimpleNamespace(current_buffer=buffer))
    assert buffer.text == "first"
    assert ui._transcript.calls == [] and not (tmp_path / "history").exists()
    assert not ui._form_bindings.get_bindings_for_keys((Keys.Escape, Keys.Up))
