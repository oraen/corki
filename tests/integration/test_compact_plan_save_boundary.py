"""A failed compaction plan save cannot authorize an orphaned cold-start hook."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, CompactionItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("event", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("save_after_write", [False, True])
@pytest.mark.parametrize("automatic", [False, True])
def test_failed_plan_save_does_not_activate_orphaned_hook(
    tmp_path, monkeypatch, event, save_after_write, automatic
):
    async def scenario():
        source = tmp_path / "config.toml"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "inspect"}, event_name=event
        )
        key = "pre_compact" if event == "PreCompact" else "post_compact"
        document = (
            f"[[hooks.{event}]]\n[[hooks.{event}.hooks]]\n"
            'type="command"\ncommand="inspect"\n'
            f"[hooks.state.{json.dumps(f'{source}:{key}:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            context_window_tokens=8000,
            auto_compact_tokens=3000,
            compact_prompt="SUMMARY_REQUEST",
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        normal, summaries, effects, calls = [], [], [], []

        class Large:
            spec = ToolSpec("large", "large output", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "x" * 16000)

        class Model:
            async def stream(self, request):
                turn = request.items[-1].turn_id
                if getattr(request.items[-1], "content", None) == "SUMMARY_REQUEST":
                    summaries.append(request)
                    yield ModelCompleted((AssistantMessageItem("summary", turn, new_step_id()),))
                    return
                normal.append(request)
                if automatic and len(normal) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "large", {}), turn, new_step_id()
                            ),
                        )
                    )
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        async def runner(command, payload, **kwargs):
            calls.append(payload)
            return {"exit_code": 0, "stdout": "{}", "stderr": ""}

        async def create(thread=None, *, cold=False):
            registry = ToolRegistry()
            registry.register(Large())
            return await LangGraphRuntime.acreate(
                settings=(
                    replace(settings, context_window_tokens=30000, auto_compact_tokens=27000)
                    if cold
                    else settings
                ),
                model=Model(),
                registry=registry,
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
            )

        monkeypatch.setattr("corki.core.compact_hooks.run_command", runner)
        warm = await create()
        try:
            save = warm._repository.save_hook_batch

            async def boundary(*args):
                target = f":{event}:" in args[2] and not args[2].endswith(":receipt")
                if target and not save_after_write:
                    raise OSError("plan save boundary")
                await save(*args)
                if target and save_after_write:
                    raise OSError("plan save boundary")

            with monkeypatch.context() as patch:
                patch.setattr(warm._repository, "save_hook_batch", boundary)
                events = [
                    item async for item in (warm.stream("work") if automatic else warm.compact())
                ]
            assert isinstance(events[-1], TurnFailed)
            assert "plan save boundary" in events[-1].error
            assert len(summaries) == int(event == "PostCompact")
            assert len(effects) == int(automatic)
            assert calls == []
            thread = warm.thread_id
            prefix = await warm._repository.load_items(thread)
            assert not any(isinstance(item, CompactionItem) for item in prefix)
            keys = await warm._repository.load_hook_batch_keys(thread, "compact_hook:")
            target_keys = [
                key for _, key in keys if f":{event}:" in key and not key.endswith(":receipt")
            ]
            assert bool(target_keys) is save_after_write
        finally:
            await warm.aclose()

        cold = await create(thread, cold=True)
        try:
            assert [item async for item in cold.resume_pending()] == []
            assert isinstance([item async for item in cold.stream("next")][-1], TurnCompleted)
            assert calls == []
            assert len(summaries) == int(event == "PostCompact")
            assert len(effects) == int(automatic)
            history = await cold._repository.load_items(thread)
            assert history[: len(prefix)] == prefix
            assert not any(isinstance(item, CompactionItem) for item in history)
        finally:
            await cold.aclose()

    asyncio.run(scenario())
