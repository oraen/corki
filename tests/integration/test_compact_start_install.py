"""Only installed compaction markers grant pending SessionStart obligations."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import AssistantMessageItem, CompactionItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "window", ["source_before", "source_after", "install_before", "install_after"]
)
@pytest.mark.parametrize("stop", [False, True])
def test_failed_manual_install_never_consumes_orphan_source(tmp_path, monkeypatch, window, stop):
    async def scenario():
        source = tmp_path / "config.toml"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "inspect"}, event_name="SessionStart", matcher="compact"
        )
        document = (
            '[[hooks.SessionStart]]\nmatcher="compact"\n[[hooks.SessionStart.hooks]]\n'
            'type="command"\ncommand="inspect"\n'
            f"[hooks.state.{json.dumps(f'{source}:session_start:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            compact_prompt="SUMMARY_REQUEST",
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        calls, summaries, normal = [], [], []

        async def runner(command, payload, **kwargs):
            calls.append(payload)
            return {"exit_code": 0, "stdout": json.dumps({"continue": not stop})}

        class Model:
            async def stream(self, request):
                target = summaries if request.items[-1].content == "SUMMARY_REQUEST" else normal
                target.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
            )

        monkeypatch.setattr("corki.core.start_hooks.run_command", runner)
        warm = await create()
        try:
            await warm._ensure_ready()
            save, append = warm._repository.save_hook_batch, warm._repository.append_items

            async def source_boundary(*args):
                target = args[2].startswith("compact_start_source:")
                if target and window == "source_before":
                    raise OSError("source/install boundary")
                await save(*args)
                if target and window == "source_after":
                    raise OSError("source/install boundary")

            async def install_boundary(thread, items):
                target = any(isinstance(i, CompactionItem) for i in items)
                if target and window == "install_before":
                    raise OSError("source/install boundary")
                await append(thread, items)
                if target and window == "install_after":
                    raise OSError("source/install boundary")

            with monkeypatch.context() as patch:
                patch.setattr(warm._repository, "save_hook_batch", source_boundary)
                patch.setattr(warm._repository, "append_items", install_boundary)
                events = [e async for e in warm.compact()]
            assert isinstance(events[-1], TurnFailed)
            assert "source/install boundary" in events[-1].error
            assert len(summaries) == 1 and not calls and not normal
            thread = warm.thread_id
            prefix = await warm._repository.load_items(thread)
            installed = window == "install_after"
            assert sum(isinstance(i, CompactionItem) for i in prefix) == int(installed)
            sources = await warm._repository.load_hook_batch_keys(thread, "compact_start_source:")
            assert len(sources) == int(window != "source_before")
        finally:
            await warm.aclose()
        cold = await create(thread)
        try:
            assert [e async for e in cold.resume_pending()] == []
            assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
            assert len(calls) == int(installed)
            assert len(normal) == int(not (installed and stop))
            assert len(summaries) == 1
            history = await cold._repository.load_items(thread)
            assert history[: len(prefix)] == prefix
            assert isinstance([e async for e in cold.stream("again")][-1], TurnCompleted)
            assert len(calls) == int(installed) and len(summaries) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())
