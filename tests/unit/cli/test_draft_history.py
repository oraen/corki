import asyncio
import io
from functools import partial

from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from corki.cli import draft_history, image_clipboard, terminal
from corki.cli.composer_validation import MAX_USER_INPUT_TEXT_CHARS
from corki.cli.draft_history import DraftEntry, DraftHistory, ExpandedFileHistory
from corki.cli.inline_images import ImageDraft
from corki.cli.input_owner import ImageInput
from corki.config import CorkiSettings
from corki.protocol.input_mentions import InputMention
from corki.protocol.tools import ImageAttachment


def test_same_labels_do_not_deduplicate_different_images():
    history = DraftHistory()
    first = DraftEntry("[Image #1]", (ImageAttachment("data:image/png;base64,one"),), (0,))
    second = DraftEntry("[Image #1]", (ImageAttachment("data:image/png;base64,two"),), (0,))
    history.record(first)
    history.record(second)
    scratch = DraftEntry("not submitted")
    assert history.move(-1, scratch) == second
    assert history.move(-1, second) == first
    assert history.move(1, first) == second
    assert history.move(1, second) == scratch
    for n in range(200):
        history.record(DraftEntry(str(n)))
    assert len(history.entries) == 128
    history.clear()
    assert history.entries == [] and history.scratch is None


def test_navigation_gate_matches_codex_boundaries_and_edit_protection():
    history = DraftHistory()
    history.record(DraftEntry("first"))
    history.record(DraftEntry("中文\nsecond"))
    assert history.should_navigate("", 0)
    assert not history.should_navigate("new draft", 0)
    recalled = history.move(-1, DraftEntry(""))
    assert history.should_navigate(recalled.text, 0)
    assert history.should_navigate(recalled.text, len(recalled.text))
    assert not history.should_navigate(recalled.text, 3)
    assert not history.should_navigate(recalled.text + "edited", len(recalled.text) + 6)
    assert history.move(1, recalled).text == ""
    assert not history.should_navigate(recalled.text, 0)


def test_maximum_legal_folded_submission_can_be_recalled():
    draft = ImageDraft()
    draft.paste("中" * MAX_USER_INPUT_TEXT_CHARS, 0, 0)
    entry = DraftEntry.capture(draft)
    history = DraftHistory()
    history.record(entry)
    assert history.move(-1, DraftEntry("")) == entry


def test_disk_history_and_cache_keep_newest_whole_entries_without_rewriting(tmp_path, monkeypatch):
    monkeypatch.setattr(draft_history, "MAX_HISTORY_TEXT_CHARS", 12)
    monkeypatch.setattr(draft_history, "MAX_HISTORY_ENTRIES", 2)
    path = tmp_path / "history"
    history = ExpandedFileHistory(path, lambda text: text)
    for text in ("old", "中文\nnext", "new"):
        history.append_string(text)
    original = path.read_bytes()
    assert history.get_strings() == ["中文\nnext", "new"]
    assert list(history.load_history_strings()) == ["new", "中文\nnext"]
    assert path.read_bytes() == original


def test_bounded_history_read_does_not_restore_a_partial_prompt(tmp_path, monkeypatch):
    path = tmp_path / "history"
    history = ExpandedFileHistory(path, lambda text: text)
    history.append_string("old\n" + "中" * 100)
    history.append_string("[Image #1]\n[Pasted Content 1001 chars]")
    monkeypatch.setattr(draft_history, "MAX_HISTORY_READ_BYTES", 110)
    restored = list(history.load_history_strings())
    assert restored == ["[Image #1]\n[Pasted Content 1001 chars]"]
    entry = DraftEntry(restored[0])
    assert not entry.images and not entry.pastes and not entry.positions


def test_history_navigation_preserves_typed_draft_and_restores_attachment(tmp_path, monkeypatch):
    image = ImageAttachment("data:image/png;base64,test")
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )

        async def read(cwd):
            return image

        monkeypatch.setattr(image_clipboard, "read_clipboard_image", read)

        async def wait_for(predicate):
            while not predicate():
                await asyncio.sleep(0)

        async def scenario():
            async with asyncio.timeout(5):
                task = asyncio.create_task(ui.read_message())
                try:
                    await wait_for(lambda: ui._session.app.is_running)
                    pipe.send_text("\x16")
                    await wait_for(lambda: bool(ui._images))
                    pipe.send_text("\r")
                    assert isinstance(await task, ImageInput)
                    task = asyncio.create_task(ui.read_message())
                    await wait_for(lambda: ui._session.app.is_running)
                    pipe.send_text("草稿")
                    await wait_for(lambda: ui._session.default_buffer.text == "草稿")
                    pipe.send_text("\x1b[A")
                    await asyncio.sleep(0.05)
                    assert ui._session.default_buffer.text == "草稿"
                    assert not ui._images
                    pipe.send_text("\x15")
                    await wait_for(lambda: not ui._session.default_buffer.text)
                    pipe.send_text("\x1b[A")
                    await wait_for(lambda: bool(ui._images))
                    assert ui._session.default_buffer.text == "[Image #1]"
                    pipe.send_text("\x1b[B")
                    await wait_for(lambda: not ui._session.default_buffer.text)
                    assert not ui._images
                    pipe.send_text("\x1b[A")
                    await wait_for(lambda: bool(ui._images))
                    pipe.send_text("\r")
                    value = await task
                    assert value.attachments == (image,)
                    assert value.image_positions == (0,)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
        assert "base64" not in (tmp_path / "history").read_text()


def test_mixed_rich_submission_restores_exact_local_draft(tmp_path, monkeypatch):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(5):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    draft = ui._inline_images
                    draft.clear("前 [Image #9] $skill 后\n")
                    draft.bind(13, "$skill", InputMention("skill", "/local/SKILL.md", "skill"))
                    draft.paste("中文\n" * 400, len(draft.text), len(draft.text))
                    draft.attach(ImageAttachment("data:image/png;base64,test"), 2, 2)
                    ui._set_image_document(len(draft.text))
                    saved = DraftEntry.capture(draft)
                    pipe.send_text("\r")
                    first = await task
                    task = asyncio.create_task(ui.read_message())
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("\x1b[A")
                    while not ui._inline_images.images:
                        await asyncio.sleep(0)
                    assert DraftEntry.capture(ui._inline_images) == saved
                    pipe.send_text("\r")
                    second = await task
                    assert first == second
                    assert first.attachments == saved.images
                    assert first.image_positions == (2,)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
