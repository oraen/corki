import asyncio
import io
from functools import partial

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from corki.cli import terminal
from corki.cli.draft_history import DraftEntry
from corki.cli.inline_images import ImageDraft
from corki.cli.input_owner import (
    DraftText,
    ImageInput,
    QueuedInput,
    submission_text,
    submission_value,
)
from corki.config import CorkiSettings
from corki.protocol.tools import ImageAttachment


def test_queue_restore_keeps_pastes_and_rebases_images(tmp_path):
    first = ImageDraft()
    first.paste("a" * 1001, 0, 0)
    first.attach(ImageAttachment("data:image/png;base64,a"), len(first.text), len(first.text))
    text, images, positions = first.expanded()
    queued = submission_value(
        QueuedInput(ImageInput(text, images, positions, DraftEntry.capture(first)))
    )
    assert submission_text(queued) == "a" * 1001 + "[Image #1]"
    second = ImageDraft()
    second.paste("b" * 1001, 0, 0)
    second.attach(ImageAttachment("data:image/png;base64,b"), len(second.text), len(second.text))
    ui = terminal.TerminalUI.__new__(terminal.TerminalUI)
    ui._inline_images = second
    ui._draft = second.text
    ui.restore_queued_inputs((queued,))
    assert [p[1] for p in ui._inline_images.pastes] == [
        "[Pasted Content 1001 chars]",
        "[Pasted Content 1001 chars] #2",
    ]
    expanded, images, positions = ui._inline_images.expanded()
    assert expanded == "a" * 1001 + "[Image #1]\n" + "b" * 1001 + "[Image #2]"
    assert positions == (1001, 2013)
    assert len(images) == 2
    plain = DraftText(
        "a" * 1001,
        DraftEntry(
            "[Pasted Content 1001 chars]", pastes=((0, "[Pasted Content 1001 chars]", "a" * 1001),)
        ),
    )
    assert submission_value(QueuedInput(plain)) is plain


def test_numbering_continues_after_deleting_first_and_resets_when_empty():
    draft = ImageDraft()
    for _ in range(2):
        draft.paste("x" * 1004, len(draft.text), len(draft.text))
    start, label, _ = draft.pastes[0]
    draft.sync(draft.text[len(label) :], 0)
    draft.paste("y" * 1004, len(draft.text), len(draft.text))
    assert [p[1] for p in draft.pastes] == [
        "[Pasted Content 1004 chars] #2",
        "[Pasted Content 1004 chars] #3",
    ]
    assert draft.expanded()[0] == "x" * 1004 + "y" * 1004
    draft.sync("", 0)
    draft.paste("z" * 1004, 0, 0)
    assert draft.text == "[Pasted Content 1004 chars]"


def test_expand_rebases_images_and_does_not_expand_literal_labels():
    draft = ImageDraft()
    draft.clear("[Pasted Content 1001 chars]前")
    draft.paste("中" * 1001, len(draft.text), len(draft.text))
    draft.attach(ImageAttachment("data:image/png;base64,test"), len(draft.text), len(draft.text))
    text, images, positions = draft.expanded()
    assert text == "[Pasted Content 1001 chars]前" + "中" * 1001 + "[Image #1]"
    assert len(images) == 1
    assert positions == (len(text) - 10,)
    original = draft.text
    start, label, _ = draft.pastes[0]
    draft.sync(original[:start] + original[start + 1 :], start)
    assert not draft.pastes
    draft.undo()
    assert draft.text == original and len(draft.pastes) == 1


def test_large_paste_submits_expanded_and_recalls_folded(tmp_path, monkeypatch):
    payload = "中文\n" * 400
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )

        async def wait(predicate):
            while not predicate():
                await asyncio.sleep(0)

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(5):
                    await wait(lambda: ui._session.app.is_running)
                    pipe.send_text("\x1b[200~" + payload + "\x1b[201~")
                    await wait(lambda: bool(ui._inline_images.pastes))
                    assert ui._session.default_buffer.text == "[Pasted Content 1200 chars]"
                    assert not task.done()
                    pipe.send_text("\r")
                    assert await task == payload
                    task = asyncio.create_task(ui.read_message())
                    await wait(lambda: ui._session.app.is_running)
                    pipe.send_text("\x1b[A")
                    await wait(lambda: bool(ui._inline_images.pastes))
                    assert ui._session.default_buffer.text == "[Pasted Content 1200 chars]"
                    pipe.send_text("\r")
                    assert await task == payload
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
        stored = (tmp_path / "history").read_text()
        assert "[Pasted Content" not in stored
        assert "中文" in stored


@pytest.mark.parametrize("undo", ["\x1f", "\x18\x15"], ids=["ctrl-underscore", "ctrl-x-ctrl-u"])
def test_undo_restores_folded_payload_without_plain_text_shadow_history(
    tmp_path, monkeypatch, undo
):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )

        async def wait(predicate):
            while not predicate():
                await asyncio.sleep(0)

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(5):
                    await wait(lambda: ui._session.app.is_running)
                    pipe.send_text("\x1b[200~" + "中" * 1001 + "\x1b[201~")
                    await wait(lambda: bool(ui._inline_images.pastes))
                    pipe.send_text("\x7f")
                    await wait(lambda: not ui._inline_images.pastes)
                    assert ui._session.default_buffer.text == ""
                    pipe.send_text(undo)
                    await wait(lambda: bool(ui._inline_images.pastes))
                    assert ui._inline_images.expanded()[0] == "中" * 1001
                    buffer = ui._session.default_buffer
                    assert buffer._undo_stack == [] and buffer._redo_stack == []
                    pipe.send_text("\r")
                    assert await task == "中" * 1001
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


def test_rich_undo_capacity_drops_old_snapshots_not_current_paste():
    draft = ImageDraft()
    for index in range(100):
        draft.sync(str(index) + "中" * 20000, 0)
    assert len(draft.history) <= 64
    assert sum(len(text) for text, _, _ in draft.history) <= 1_000_000
    draft.paste("文" * 1001, len(draft.text), len(draft.text))
    assert draft.expanded()[0].endswith("文" * 1001)
    draft.clear()
    assert draft.history == [] and draft.elements == []
