"""Real terminal -> image draft -> real runtime; never reads the host clipboard."""

import os
import sys
import time

import pexpect
import pytest
from PIL import Image

PROGRAM = r"""
import asyncio, base64, io, sys
from pathlib import Path
from PIL import Image
from corki.cli import image_clipboard
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.protocol.tools import ImageAttachment

async def main():
    cwd = Path.cwd()
    raw = io.BytesIO()
    with Image.new("RGB", (3, 2), "red") as image:
        image.save(raw, format="PNG")
    attachment = ImageAttachment(
        "data:image/png;base64," + base64.b64encode(raw.getvalue()).decode())
    async def read(cwd):
        await asyncio.sleep(.05)
        return attachment
    image_clipboard.read_clipboard_image = read
    class Model:
        calls = 0
        async def stream(self, request):
            self.calls += 1
            if sys.argv[1] == "queued" and self.calls == 1:
                print("MODEL_WAITING", flush=True)
                yield ModelTextDelta("FIRST_TURN")
                await asyncio.sleep(2)
                yield ModelCompleted((AssistantMessageItem("FIRST_DONE", request.items[-1].turn_id,
                                                           new_step_id()),))
                return
            users = [i for i in request.items if isinstance(i, UserMessageItem)]
            assert sum(isinstance(p, ImageAttachment) for p in
                       (*users[-1].attachments, *users[-1].content_items)) == 1
            if sys.argv[1] == "path":
                assert users[-1].image_positions == (0,)
                assert users[-1].content == "[Image #1] "
            if sys.argv[1] == "burst":
                assert users[-1].content == "x" * 1100 + "\n后文[Image #1]"
                assert users[-1].image_positions == (1103,)
            yield ModelCompleted((AssistantMessageItem("IMAGE_RECEIVED", request.items[-1].turn_id,
                                                       new_step_id()),))
        async def aclose(self): pass
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False)
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / "state.db", home_path=cwd / "home")
    ui = TerminalUI(settings, cwd / "history")
    class App(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            print("TURN_SETTLED", flush=True)
    app = App(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert model.calls == (2 if sys.argv[1] in {"queued", "recall"} else 1)
    assert ui._paste_task is None and not ui._images
    print("CLEAN_EXIT", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("mode", ["idle", "queued", "path", "recall", "burst"])
def test_paste_remove_and_submit_image_only(tmp_path, width, mode):
    if mode == "path":
        with Image.new("RGB", (2, 2), "blue") as image:
            image.save(tmp_path / "中 文.png")
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, mode],
        cwd=tmp_path,
        env={
            **os.environ,
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
        encoding="utf-8",
        dimensions=(30, width),
        timeout=15,
    )
    try:
        child.expect("Ask Corki to do anything")
        if mode == "queued":
            child.send("start")
            time.sleep(0.15)  # deliberate submit, not a raw multiline paste
            child.send("\r")
            child.expect_exact("MODEL_WAITING")
        if mode == "burst":
            child.send("x" * 1100 + "\r后文")
            child.expect_exact("[Pasted Content 1103 chars]")
        if mode == "path":
            child.send('\x1b[200~"中 文.png"\x1b[201~')
        else:
            child.send("\x16")
        child.expect_exact("[Image #1]")
        if mode != "burst":
            child.send("\x16")
            child.expect_exact("[Image #2]")
            child.send("\x7f")  # atomic removal is exercised by the other four modes
        child.send("\t" if mode == "queued" else "\r")
        child.expect_exact("IMAGE_RECEIVED")
        child.expect_exact("TURN_SETTLED")
        child.expect("Ask Corki to do anything")
        if mode == "recall":
            child.send("\x1b[A")
            child.expect_exact("[Image #1]")
            child.send("\r")
            child.expect_exact("IMAGE_RECEIVED")
            child.expect_exact("TURN_SETTLED")
            child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect_exact("CLEAN_EXIT")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
