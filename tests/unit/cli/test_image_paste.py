import asyncio
import io
from functools import partial

import pytest
from PIL import Image
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from corki.cli import image_clipboard, terminal
from corki.cli.input_owner import ImageInput, QueuedInput, input_attachments
from corki.config import CorkiSettings
from corki.protocol.tools import ImageAttachment

IMAGE = ImageAttachment("data:image/png;base64,test")


@pytest.mark.parametrize("keys,queued", [("\r", False), ("\t", True)])
def test_image_only_submission_and_no_leaked_reader(tmp_path, monkeypatch, keys, queued):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )

        async def read(cwd):
            return IMAGE

        monkeypatch.setattr(image_clipboard, "read_clipboard_image", read)

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("\x16")
                    while not ui._images:
                        await asyncio.sleep(0)
                    assert "[Image #1]" in ui._session.default_buffer.text
                    pipe.send_text("\x04")
                    await asyncio.sleep(0.02)
                    assert not task.done()
                    pipe.send_text(keys)
                    result = await task
                    assert isinstance(result, QueuedInput) == queued
                    assert input_attachments(result) == (IMAGE,)
                    assert not ui._images
                    assert ui._paste_task is None
                    assert not ui._session.app._background_tasks
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


def test_cancelled_paste_is_joined_and_cannot_attach_to_next_prompt(tmp_path, monkeypatch):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")

        async def scenario():
            entered, joined = asyncio.Event(), asyncio.Event()

            async def read(cwd):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    joined.set()

            monkeypatch.setattr(image_clipboard, "read_clipboard_image", read)
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("draft\x16")
                    await entered.wait()
                    pipe.send_text("\r")  # a pending read must not race submission
                    await asyncio.sleep(0.02)
                    assert not task.done()
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    assert joined.is_set()
                    assert ui._draft == "draft"
                    assert not ui._images and not ui._image_notice
                    assert ui._paste_task is None
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


def test_draft_restore_carries_images_and_preview_hides_bytes(tmp_path):
    ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")
    ui.restore_queued_inputs((ImageInput("caption", (IMAGE,)),))
    assert ui._draft == "caption[Image #1]" and ui._images == [IMAGE]
    ui.set_pending_inputs((ImageInput("", (IMAGE,)),))
    assert "Images: 1" in ui._pending_inputs[0]
    assert "base64" not in ui._pending_inputs[0]


def test_capture_validates_and_encodes_without_temp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(image_clipboard.sys, "platform", "linux")
    monkeypatch.setattr(
        image_clipboard.ImageGrab, "grabclipboard", lambda: Image.new("RGB", (2, 3), "red")
    )
    raw = image_clipboard.capture_png()
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (2, 3) and image.format == "PNG"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("image", [None, "large"])
def test_empty_or_oversized_clipboard_fails_cleanly(monkeypatch, image):
    monkeypatch.setattr(image_clipboard.sys, "platform", "linux")
    monkeypatch.setattr(image_clipboard, "MAX_PIXELS", 1)
    monkeypatch.setattr(
        image_clipboard.ImageGrab,
        "grabclipboard",
        lambda: None if image is None else Image.new("RGB", (2, 2)),
    )
    with pytest.raises(ValueError):
        image_clipboard.capture_png()


def test_remote_clipboard_is_not_read(tmp_path, monkeypatch):
    monkeypatch.setenv("SSH_CONNECTION", "test")
    with pytest.raises(ValueError, match="SSH"):
        asyncio.run(image_clipboard.read_clipboard_image(tmp_path))


@pytest.mark.skipif(image_clipboard.sys.platform != "darwin", reason="macOS pasteboard API")
def test_native_file_url_script_uses_private_pasteboard(tmp_path):
    import json
    import subprocess

    # Exercise the real bridge on an isolated board, never the user's clipboard.
    path = str(tmp_path / "图片 with spaces.png")
    script = image_clipboard.FILE_URL_SCRIPT.replace(
        "var p = $.NSPasteboard.generalPasteboard;",
        "var p = $.NSPasteboard.pasteboardWithUniqueName;\n"
        f"p.writeObjects($.NSArray.arrayWithObject($.NSURL.fileURLWithPath({json.dumps(path)})));",
    ).replace("JSON.stringify(paths);", "p.releaseGlobally; JSON.stringify(paths);")
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
        capture_output=True,
        check=True,
        timeout=5,
    )
    assert json.loads(result.stdout) == [path]


def test_macos_prefers_copied_file_before_pixel_clipboard(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(image_clipboard.sys, "platform", "darwin")
    calls = []

    def run(argv, **kwargs):
        assert argv[:3] == ["/usr/bin/osascript", "-l", "JavaScript"]
        assert kwargs["timeout"] == 2
        return SimpleNamespace(stdout=b'["/copied/image.png"]')

    def open_files(paths):
        calls.append(paths)
        return Image.new("RGB", (2, 3))

    def unexpected_pixels():
        pytest.fail("file images must take precedence")

    monkeypatch.setattr(image_clipboard.subprocess, "run", run)
    monkeypatch.setattr(image_clipboard, "_file_image", open_files)
    monkeypatch.setattr(image_clipboard.ImageGrab, "grabclipboard", unexpected_pixels)
    assert image_clipboard.capture_png().startswith(b"\x89PNG")
    assert calls == [["/copied/image.png"]]


def test_paste_failure_and_slash_command_keep_existing_draft(tmp_path, monkeypatch):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )
        ui._inline_images.restore("", (IMAGE,))
        ui._draft = ui._inline_images.text

        async def fail(cwd):
            raise ValueError("PRIVATE CLIPBOARD DATA MUST NOT BE PRINTED")

        monkeypatch.setattr(image_clipboard, "read_clipboard_image", fail)

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("/status\x16")
                    while "Could not paste" not in ui._image_notice:
                        await asyncio.sleep(0)
                    assert "PRIVATE" not in ui._image_notice
                    assert ui._images == [IMAGE]
                    pipe.send_text("\r")
                    assert await task == "/status"
                    assert ui._images == [IMAGE]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


def test_inline_paste_tracks_cursor_and_typed_text_during_clipboard_read(tmp_path, monkeypatch):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )

        async def scenario():
            ready, release = asyncio.Event(), asyncio.Event()

            async def read(cwd):
                ready.set()
                await release.wait()
                return IMAGE

            monkeypatch.setattr(image_clipboard, "read_clipboard_image", read)
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("甲乙\x02\x16")
                    await ready.wait()
                    pipe.send_text("后")
                    while ui._session.default_buffer.text != "甲后乙":
                        await asyncio.sleep(0)
                    release.set()
                    while not ui._images:
                        await asyncio.sleep(0)
                    assert ui._session.default_buffer.text == "甲[Image #1]后乙"
                    # Delete atomically and undo without losing the attachment.
                    pipe.send_text("\x02\x7f")
                    while ui._images:
                        await asyncio.sleep(0)
                    assert ui._session.default_buffer.text == "甲后乙"
                    pipe.send_text("\x1f")
                    while not ui._images:
                        await asyncio.sleep(0)
                    pipe.send_text("\r")
                    result = await task
                    assert result.text == "甲[Image #1]后乙"
                    assert result.image_positions == (1,)
                    assert result.attachments == (IMAGE,)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
