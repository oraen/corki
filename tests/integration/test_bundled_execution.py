import asyncio
import json
import os
import shlex
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.execution import bundled
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.fixture
def compiler(tmp_path, monkeypatch):
    if sys.platform != "darwin":
        pytest.skip("real bundled enforcement proof requires macOS")
    supplied = os.environ.get("CORKI_TEST_BUNDLED_COMPILER")
    if supplied:
        source = Path(supplied)
        root = tmp_path / "host-installation"
        root.mkdir()
        shutil.copy2(source, root / "corki-sandbox")
        shutil.copy2(source.with_name(source.name + ".json"), root / "manifest.json")
        monkeypatch.setattr(bundled, "_BUNDLE", root)
    binary = bundled.bundled_compiler()
    if binary is None:
        pytest.skip("requires a platform wheel or a freshly built compiler with receipt")
    return binary


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("raw", [False, True])
def test_no_manual_compiler_config_reaches_real_runtime_enforcement(
    tmp_path, compiler, nested, raw
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = workspace / "config.toml"
    config.write_text(
        '[execution]\nprofile={type="read-only"}' if raw else 'default_permissions=":read-only"'
    )
    chosen = replace(
        CorkiSettings.for_directory(workspace, config_file=config),
        skills_enabled=False,
        tool_mode="code_mode_only" if nested else "direct",
    )
    assert chosen.execution_permissions.compiler == compiler
    destination = workspace / "must-not-exist"
    requests = []
    script = (
        "from pathlib import Path\n"
        "try:\n"
        f" Path({str(destination)!r}).write_text('not allowed')\n"
        " print('unexpected write')\n"
        "except PermissionError:\n"
        " print('native write denial observed')\n"
    )

    class Model:
        async def stream(self, request):
            requests.append(request)
            turn, step = request.items[-1].turn_id, new_step_id()
            if len(requests) == 1:
                args = {"cmd": shlex.join([sys.executable, "-I", "-c", script]), "login": False}
                call = (
                    ToolCall(new_tool_call_id(), "exec_command", args)
                    if not nested
                    else ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        raw_arguments="text(await tools.exec_command(" + json.dumps(args) + "));",
                        input_kind="freeform",
                    )
                )
                item = ToolCallItem(call, turn, step)
            else:
                item = AssistantMessageItem("done", turn, step)
            yield ModelCompleted((item,))

        async def aclose(self):
            pass

    async def scenario():
        runtime = LangGraphRuntime.create(
            settings=chosen, model=Model(), database_path=tmp_path / "state.db"
        )
        try:
            events = [event async for event in runtime.stream("exercise bundled permissions")]
            assert isinstance(events[-1], TurnCompleted)
            effective = runtime._graph._settings.execution_permissions
            assert effective.compiler == compiler
            assert not effective.needs_resolution
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
    results = [item for item in requests[-1].items if isinstance(item, ToolResultItem)]
    assert any("native write denial observed" in item.content for item in results)
    assert not destination.exists()
