import asyncio
import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelErrorKind, ModelItemCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("change", ["same", "schema", "hidden", "removed"])
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("phase", ["model", "prepare"])
def test_model_step_executes_captured_handler_after_registry_publication(
    tmp_path, change, streamed, phase
):
    asyncio.run(run_replacement(tmp_path, change, streamed, "direct", phase))


@pytest.mark.parametrize("change", ["same", "schema"])
@pytest.mark.parametrize("streamed", [False, True])
def test_code_mode_table_and_dispatch_use_captured_step(tmp_path, change, streamed):
    asyncio.run(run_replacement(tmp_path, change, streamed, "code_mode"))


async def run_replacement(tmp_path, change, streamed, tool_mode, phase="model"):
    executed, requests = [], []
    original = ToolSpec(
        "lookup",
        "Look up a record",
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )

    class Tool:
        def __init__(self, spec, name):
            self.spec, self.name = spec, name

        async def execute(self, call, context):
            executed.append((self.name, call.arguments["value"]))
            return ToolResult(call.id, call.name, self.name)

    replacement = (
        replace(
            original,
            parameters={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        )
        if change == "schema"
        else replace(original, exposure=ToolExposure.HIDDEN)
        if change == "hidden"
        else original
    )
    registry = ToolRegistry()
    owner = registry.create_owner()
    registry.replace_owned(owner, (Tool(original, "old-handler"),))

    class Model:
        async def stream(self, request):
            requests.append(request)
            turn, step = request.items[-1].turn_id, new_step_id()
            if len(requests) == 1:
                if phase == "model":
                    registry.replace_owned(
                        owner, () if change == "removed" else (Tool(replacement, "new-handler"),)
                    )
                call = (
                    ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        raw_arguments='text(await tools.lookup({value:"old"}));',
                        input_kind="freeform",
                    )
                    if tool_mode == "code_mode"
                    else ToolCall(new_tool_call_id(), "lookup", {"value": "old"})
                )
                item = ToolCallItem(call, turn, step)
                if streamed:
                    yield ModelItemCompleted(item)
                yield ModelCompleted((item,))
            else:
                assert executed == [("old-handler", "old")]
                if tool_mode == "code_mode":
                    spec = next(t for t in request.tools if t.name == "exec")
                    definitions = json.loads(
                        spec.description.split("Direct nested definitions: ", 1)[1]
                    )
                    assert definitions["lookup"]["parameters"] == replacement.parameters
                outputs = [i for i in request.items if isinstance(i, ToolResultItem)]
                assert any("old-handler" in i.content and not i.is_error for i in outputs)
                yield ModelCompleted((AssistantMessageItem("done", turn, step),))

        async def aclose(self):
            pass

    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, tool_mode=tool_mode
        ),
        database_path=tmp_path / "snapshot.db",
        registry=registry,
        model=Model(),
    )
    if phase == "prepare":
        build = runtime._graph._context_builder.build
        changed = False

        async def build_and_publish(**kwargs):
            nonlocal changed
            result = await build(**kwargs)
            if not changed:
                changed = True
                registry.replace_owned(
                    owner, () if change == "removed" else (Tool(replacement, "new-handler"),)
                )
            return result

        runtime._graph._context_builder.build = build_and_publish
    try:
        events = [e async for e in runtime.stream("use the captured tool")]
        assert isinstance(events[-1], TurnCompleted), events[-1]
        assert len(requests) == 2
    finally:
        await runtime.aclose()


def test_retry_retains_the_sampling_step_router_until_next_step(tmp_path):
    async def scenario():
        requests, executed = [], []
        registry = ToolRegistry()
        owner = registry.create_owner()

        class Tool:
            def __init__(self, word):
                self.spec = ToolSpec("lookup", word, {})
                self.word = word

            async def execute(self, call, context):
                executed.append(self.word)
                return ToolResult(call.id, call.name, self.word)

        registry.replace_owned(owner, (Tool("first"),))

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    registry.replace_owned(owner, (Tool("second"),))
                    raise ModelError("retry fixture", kind=ModelErrorKind.TRANSPORT, retryable=True)
                if len(requests) == 2:
                    assert (
                        next(t for t in request.tools if t.name == "lookup").description == "first"
                    )
                    registry.replace_owned(owner, (Tool("third"),))
                    yield ModelCompleted(
                        (ToolCallItem(ToolCall(new_tool_call_id(), "lookup", {}), turn, step),)
                    )
                else:
                    assert executed == ["first"]
                    assert (
                        next(t for t in request.tools if t.name == "lookup").description == "third"
                    )
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, model_retry_base_seconds=0.001
            ),
            database_path=tmp_path / "retry.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("retry with current tools")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
