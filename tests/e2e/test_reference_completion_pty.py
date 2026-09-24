"""Real composer -> runtime skill selection, with a local deterministic model."""

import os
import sys
import time

import pexpect
import pytest
from PIL import Image

PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.items import AssistantMessageItem, ContextItem, UserMessageItem, new_step_id
from corki.protocol.tools import ImageAttachment

async def main():
    cwd = Path.cwd()
    from corki.cli import native_file_search
    native_file_search.executable = lambda: Path(sys.argv[2]) if sys.argv[2] else None
    class Model:
        calls = 0
        async def stream(self, request):
            self.calls += 1
            user = [i for i in request.items if isinstance(i, UserMessageItem)][-1]
            if sys.argv[1] == "skill":
                assert user.content == "$guide "
                assert len(user.mentions) == 1
                assert user.mentions[0].path == str(cwd / ".corki/skills/guide/SKILL.md")
                assert any(isinstance(i, ContextItem)
                           and i.key.startswith("extensions.skills.selected.")
                           and "LOCAL_REFERENCE_BODY" in i.content for i in request.items)
            elif sys.argv[1] == "image":
                assert user.content == "[Image #1] "
                assert user.image_positions == (0,)
                assert sum(isinstance(p, ImageAttachment) for p in
                           (*user.attachments, *user.content_items)) == 1
            elif sys.argv[1] == "directory":
                assert user.content == '"文档 guides"', repr(user.content)
                assert not user.mentions and not user.attachments
            else:
                assert user.content == '"中文 name.txt"', repr(user.content)
                assert not user.mentions
            yield ModelCompleted((AssistantMessageItem("REFERENCE_ACCEPTED", user.turn_id,
                                                       new_step_id()),))
        async def aclose(self): pass
    settings = CorkiSettings(cwd, plugins_enabled=False)
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
    assert model.calls == 1
    assert not ui._inline_images.bindings
    print("CLEAN_EXIT", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("mode", ["skill", "file", "image", "directory"])
@pytest.mark.parametrize("backend", ["rg", "native"])
def test_reference_selection_and_runtime_binding(tmp_path, width, mode, backend):
    binary = os.environ.get("CORKI_TEST_FILE_SEARCH", "") if backend == "native" else ""
    if backend == "native" and not binary:
        pytest.skip("set CORKI_TEST_FILE_SEARCH to exercise the native search backend")
    skill = tmp_path / ".corki/skills/guide/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: guide\ndescription: Reference fixture\n---\nLOCAL_REFERENCE_BODY")
    (tmp_path / "中文 name.txt").write_text("file fixture")
    (tmp_path / "文档 guides").mkdir()
    if backend == "rg":
        (tmp_path / "文档 guides" / "intro.md").write_text("directory fixture")
    if mode == "image":
        with Image.new("RGB", (3, 2), "blue") as image:
            image.save(tmp_path / "图片 空格.png")
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, mode, binary],
        cwd=tmp_path,
        env={
            **os.environ,
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
        encoding="utf-8",
        dimensions=(30, width),
        timeout=20,
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send({"skill": "$gui", "file": "@中文", "image": "@图片", "directory": "@文档"}[mode])
        child.expect_exact(
            {
                "skill": "$guide",
                "file": "中文 name.txt",
                "image": "图片 空格.png",
                "directory": "文档 guides",
            }[mode]
        )
        time.sleep(0.15)
        child.send("\r")  # choose candidate, do not submit
        if mode == "image":
            child.expect_exact("[Image #1]")
        time.sleep(0.15)
        child.send("\r")
        child.expect_exact("REFERENCE_ACCEPTED")
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
