import asyncio
import io
import os
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest
from PIL import Image
from prompt_toolkit import PromptSession
from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from corki.cli import image_clipboard, terminal
from corki.cli.input_owner import ImageInput
from corki.cli.reference_completion import Reference, ReferenceCompletion
from corki.config import CorkiSettings
from corki.protocol.tools import ImageAttachment


@pytest.mark.parametrize(
    "text,expected",
    [
        ('"/tmp/中 文.png"', "/tmp/中 文.png"),
        ("'/tmp/中 文.png'", "/tmp/中 文.png"),
        (r"/tmp/My\ File.png", "/tmp/My File.png"),
        ("file:///tmp/My%20File.png", "/tmp/My File.png"),
        ("file://remote/tmp/a.png", None),
        ("file://[broken/tmp/a.png", None),
        ("file:///tmp/%00.png", None),
        ("a.png b.png", None),
        ('"unclosed', None),
        ("a\nb", None),
        ("", None),
    ],
)
def test_normalize(text, expected, monkeypatch):
    monkeypatch.setattr(image_clipboard.sys, "platform", "darwin")
    value = image_clipboard.normalize_pasted_path(text)
    assert (value.as_posix() if value is not None else None) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("file:///C:/My%20Files/%E5%9B%BE.png", r"C:\My Files\图.png"),
        ("file://localhost/D:/image.png", r"D:\image.png"),
        ("file:///C:/literal%2520.png", r"C:\literal%20.png"),
        ("file:///C:/%00.png", None),
        ("file:///relative.png", None),
        ("file://remote/C:/image.png", None),
        ("file:///C:/image.png?query", None),
    ],
)
def test_windows_file_url(text, expected, monkeypatch):
    monkeypatch.setattr(image_clipboard.sys, "platform", "win32")
    value = image_clipboard.normalize_pasted_path(text)
    assert (str(value) if value is not None else None) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        (r".\图片\示例.png", r".\图片\示例.png"),
        (r"..\images\a.png", r"..\images\a.png"),
        (r'"图片 文件\a.png"', r"图片 文件\a.png"),
        (r"images\a.png", r"images\a.png"),
        (r"\images\a.png", r"\images\a.png"),
    ],
)
def test_windows_relative_path_separators_are_not_shell_escapes(text, expected, monkeypatch):
    monkeypatch.setattr(image_clipboard.sys, "platform", "win32")
    assert image_clipboard.normalize_pasted_path(text) == Path(expected)


@pytest.mark.skipif(os.name != "nt", reason="native Windows path lookup")
def test_windows_relative_paste_opens_the_intended_image(tmp_path):
    folder = tmp_path / "图片 文件"
    folder.mkdir()
    image_path = folder / "示例.png"
    with Image.new("RGB", (2, 3), "red") as picture:
        picture.save(image_path)
    relative = image_clipboard.normalize_pasted_path(r'.\图片 文件\示例.png')
    assert relative is not None
    image = image_clipboard._file_image([tmp_path / relative])
    assert image is not None
    try:
        assert image.size == (2, 3)
    finally:
        image.close()


def test_owned_path_reader_without_clipboard(tmp_path):
    path = tmp_path / "中 文.png"
    with Image.new("RGB", (2, 2), "red") as image:
        image.save(path)

    async def scenario():
        value = await image_clipboard.read_path_image(path, tmp_path)
        assert value.data_url.startswith("data:image/png;base64,")
        with pytest.raises(ValueError):
            await image_clipboard.read_path_image(tmp_path / "missing", tmp_path)

    asyncio.run(scenario())


def test_image_helper_startup_does_not_import_agent_runtime(tmp_path):
    # An invalid argument exercises the installed worker entry without reading
    # the user's clipboard. Inspect imports in a fresh isolated interpreter.
    probe = """
import runpy, sys
helper = sys.argv[1]
sys.argv = [helper, '--invalid-fixture-argument']
try:
    runpy.run_path(helper, run_name='__main__')
except SystemExit as error:
    assert error.code == 1
else:
    raise AssertionError('invalid invocation must fail')
assert 'corki.cli.main' not in sys.modules
assert 'corki.core' not in sys.modules
assert 'corki.protocol' not in sys.modules
assert 'corki.execution' not in sys.modules
assert not any(name.startswith(('langgraph', 'langchain')) for name in sys.modules)
"""
    subprocess.run(
        [sys.executable, "-I", "-c", probe, image_clipboard._HELPER_PATH],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        timeout=15,
    )


@pytest.mark.parametrize("invalid", ["missing", "directory", "text", "bytes", "pixels"])
def test_file_image_rejects_invalid_or_over_limit_inputs(tmp_path, monkeypatch, invalid):
    path = tmp_path / "图片.png"
    if invalid == "directory":
        path.mkdir()
    elif invalid == "text":
        path.write_text("not an image", encoding="utf-8")
    elif invalid in {"bytes", "pixels"}:
        with Image.new("RGB", (2, 2)) as picture:
            picture.save(path)
        monkeypatch.setattr(
            image_clipboard, "MAX_IMAGE_BYTES" if invalid == "bytes" else "MAX_PIXELS", 1
        )
    assert image_clipboard._file_image([path]) is None


def test_rejected_pixel_image_closes_decoder(tmp_path, monkeypatch):
    path = tmp_path / "picture.png"
    with Image.new("RGB", (2, 2)) as picture:
        picture.save(path)
    opened = []
    original = image_clipboard.Image.open

    def open_image(*args, **kwargs):
        picture = original(*args, **kwargs)
        opened.append(picture)
        return picture

    monkeypatch.setattr(image_clipboard.Image, "open", open_image)
    monkeypatch.setattr(image_clipboard, "MAX_PIXELS", 1)
    assert image_clipboard._file_image([path]) is None
    assert len(opened) == 1 and opened[0].fp is None


@pytest.mark.parametrize("outcome", ["oversized", "invalid"])
def test_png_encoding_failure_closes_image(monkeypatch, outcome):
    picture = Image.new("RGB", (2, 2))
    monkeypatch.setattr(image_clipboard, "MAX_IMAGE_BYTES", 1)
    if outcome == "invalid":
        monkeypatch.setattr(image_clipboard, "MAX_PIXELS", 1)
    with pytest.raises(ValueError, match="limit"):
        image_clipboard.encode_png(picture)
    with pytest.raises(ValueError, match="closed"):
        picture.getpixel((0, 0))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("plain", "plain"),
        ("中文文本", "中文文本"),
        ("missing.png", "missing.png"),
        ("draft\x1b[2Jtext", "drafttext"),
    ],
)
def test_non_file_paste_accepts_immediate_enter_without_decoder(
    tmp_path, monkeypatch, text, expected
):
    async def unexpected(*args):
        raise AssertionError("ordinary text must not start an image decoder")

    monkeypatch.setattr(image_clipboard, "read_path_image", unexpected)
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
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text(f"\x1b[200~{text}\x1b[201~\r")
                    assert await task == expected
                    assert ui._paste_task is None
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["success", "invalid", "edited", "cancelled"])
@pytest.mark.parametrize("source", ["paste", "candidate"])
def test_pasted_path_is_attached_or_preserved(tmp_path, monkeypatch, outcome, source):
    image_path = tmp_path / "中 文.png"
    image_path.touch()
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )

        async def scenario():
            entered, release, joined = asyncio.Event(), asyncio.Event(), asyncio.Event()

            async def read(path, cwd):
                entered.set()
                try:
                    await release.wait()
                    if outcome == "invalid":
                        raise ValueError("not image")
                    return ImageAttachment("data:image/png;base64,test")
                finally:
                    joined.set()

            monkeypatch.setattr(image_clipboard, "read_path_image", read)
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    quoted_path = f'"{image_path}"'
                    expected = quoted_path + (" " if source == "candidate" else "")
                    if source == "paste":
                        pipe.send_text(f"\x1b[200~{quoted_path}\x1b[201~")
                    else:
                        buffer = ui._session.default_buffer
                        buffer.complete_while_typing = lambda: False
                        buffer.document = Document("@image")
                        completion = ReferenceCompletion(
                            Reference(str(image_path), "File"), buffer.document, 0
                        )
                        buffer.complete_state = CompletionState(buffer.document, [completion], 0)
                        pipe.send_text("\r")
                    await entered.wait()
                    if outcome == "cancelled":
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        assert joined.is_set()
                        assert ui._draft == expected
                    else:
                        if outcome == "edited":
                            pipe.send_text("后")
                            while not ui._session.default_buffer.text.endswith("后"):
                                await asyncio.sleep(0)
                        release.set()
                        while not ui._paste_task.done():
                            await asyncio.sleep(0)
                        pipe.send_text("\r")
                        result = await task
                        if outcome == "success":
                            assert isinstance(result, ImageInput)
                            assert result.text == "[Image #1] "
                            assert result.image_positions == (0,)
                        else:
                            assert result == expected + ("后" if outcome == "edited" else "")
                    assert not ui._session.app._background_tasks
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
