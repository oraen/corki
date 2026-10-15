"""Diagnose product-metadata protection using only a disposable owned workspace."""

import asyncio
import json
import shlex
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from corki.config import CorkiSettings
from corki.config.instructions import ProjectInstructionsConfig
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall


async def probe(root):
    workspace = root / "workspace"
    workspace.mkdir()
    for name in (".codex", ".corki"):
        (workspace / name).mkdir()
        (workspace / name / "config.toml").write_text("# host original\n")
    script = r"""
from pathlib import Path
for name in ['.codex', '.corki']:
    try:
        (Path(name) / 'config.toml').write_text('# model replacement\n')
    except PermissionError:
        pass
"""

    class Model:
        called = False

        async def stream(self, request):
            if self.called:
                yield ModelCompleted(())
                return
            self.called = True
            call = ToolCall(
                new_tool_call_id(),
                "exec_command",
                {"cmd": shlex.join([sys.executable, "-I", "-c", script]), "login": False},
            )
            yield ModelCompleted((ToolCallItem(call, request.items[-1].turn_id, new_step_id()),))

        async def aclose(self):
            pass

    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=workspace,
            skills_enabled=False,
            project_instructions=ProjectInstructionsConfig(trust_level="trusted"),
        ),
        database_path=root / "state.db",
        home_path=root / "host",
        model=Model(),
    )
    try:
        events = [event async for event in runtime.stream("exercise owned metadata fixtures")]
        result = {
            "terminal": type(events[-1]).__name__,
            "model_wrote": {
                name: "model replacement" in (workspace / name / "config.toml").read_text()
                for name in (".codex", ".corki")
            },
        }
        print(json.dumps(result, sort_keys=True))
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    with TemporaryDirectory(prefix="corki-metadata-probe-") as directory:
        asyncio.run(probe(Path(directory).resolve()))
