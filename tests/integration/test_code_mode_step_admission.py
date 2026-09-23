"""Old modules retain identities, but newly admitted calls use the current worker."""

import asyncio
import json
import re
import sqlite3
from dataclasses import replace

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


def invoke(request, name, value):
    return ModelCompleted(
        (
            ToolCallItem(
                ToolCall(
                    new_tool_call_id(),
                    name,
                    None if name == "exec" else value,
                    raw_arguments=value if name == "exec" else json.dumps(value),
                    input_kind="freeform" if name == "exec" else "json",
                ),
                request.items[-1].turn_id,
                new_step_id(),
            ),
        )
    )


@pytest.mark.parametrize("next_turn", [False, True])
@pytest.mark.parametrize("kind", ["json", "freeform"])
@pytest.mark.parametrize(
    "change",
    ["settings", "schema", "invalid_schema", "input_kind", "hidden", "model_only", "removed"],
)
def test_old_cell_new_calls_use_current_step(tmp_path, next_turn, kind, change):
    async def scenario():
        held, release, both = asyncio.Event(), asyncio.Event(), asyncio.Event()
        requests, executed, cell_ids = [], [], []
        original = ToolSpec(
            "look-up",
            "old definition",
            {"type": "object"},
            input_kind=kind,
            concurrency=ToolConcurrency.EXCLUSIVE,
        )
        updated = replace(
            original,
            description="updated definition",
            output_char_budget=80,
            input_kind=("freeform" if kind == "json" else "json")
            if change == "input_kind"
            else kind,
            concurrency=ToolConcurrency.PARALLEL,
            parameters={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            }
            if change in {"schema", "invalid_schema"}
            else original.parameters,
            exposure=ToolExposure.HIDDEN
            if change == "hidden"
            else ToolExposure.DIRECT_MODEL_ONLY
            if change == "model_only"
            else ToolExposure.DIRECT,
        )

        class Hold:
            spec = ToolSpec("hold", "wait for next step", {"type": "object"})

            async def execute(self, call, context):
                held.set()
                await release.wait()
                return ToolResult(call.id, call.name, "released")

        class Lookup:
            def __init__(self, spec, label):
                self.spec, self.label = spec, label

            async def execute(self, call, context):
                executed.append((self.label, call.name, call.input_kind, call.arguments))
                if len(executed) == 2:
                    both.set()
                if change not in {"hidden", "removed"}:
                    await asyncio.wait_for(both.wait(), 2)
                return ToolResult(call.id, call.name, self.label + "X" * 400)

        registry = ToolRegistry()
        owner = registry.create_owner()
        registry.register(Hold())
        registry.replace_owned(owner, (Lookup(original, "old"),))
        # Replacing the sanitized alias must not redirect a module's old real name.
        current = Lookup(
            replace(updated, name="look_up") if change == "removed" else updated, "current"
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                results = [item for item in request.items if isinstance(item, ToolResultItem)]
                if len(requests) == 1:
                    registry.replace_owned(owner, (current,))
                    argument = (
                        '"raw"'
                        if kind == "freeform"
                        else '{value:"not-an-integer"}'
                        if change == "invalid_schema"
                        else "{value:7}"
                    )
                    yield invoke(
                        request,
                        "exec",
                        '// @exec: {"yield_time_ms":10}\n'
                        "await tools.hold({}); text(await Promise.all(["
                        f"tools.look_up({argument}).catch(e=>String(e)), "
                        f"tools.look_up({argument}).catch(e=>String(e))]));",
                    )
                elif len(requests) == 2 and next_turn:
                    await asyncio.wait_for(held.wait(), 2)
                    cell_ids.append(re.search(r"cell ID (\S+)", results[-1].content)[1])
                    yield ModelCompleted(())
                elif len(requests) == (3 if next_turn else 2):
                    await asyncio.wait_for(held.wait(), 2)
                    if not cell_ids:
                        cell_ids.append(re.search(r"cell ID (\S+)", results[-1].content)[1])
                    # Current Step is already captured: a newer publication must not win.
                    registry.replace_owned(owner, (Lookup(current.spec, "too-new"),))
                    release.set()
                    yield invoke(request, "wait", {"cell_id": cell_ids[0]})
                else:
                    result = results[-1]
                    assert "Script completed" in result.content, result.content
                    if change == "removed":
                        assert "unknown tool: look-up" in result.content
                        assert not executed
                    elif change == "invalid_schema" and kind == "json":
                        assert "arguments.value must be integer" in result.content
                        assert not executed
                    elif change == "input_kind":
                        assert "incompatible payload" in result.content
                        assert not executed
                    else:
                        assert [row[:3] for row in executed] == [
                            ("current", "look-up", kind),
                            ("current", "look-up", kind),
                        ], result.content
                        # Nested callers receive the original; the old per-tool
                        # character cap belongs to direct model history only.
                        assert "current" + "X" * 400 in result.content
                        if kind == "json":
                            assert all(row[3] == {"value": 7} for row in executed)
                    direct = json.loads(
                        next(t for t in request.tools if t.name == "exec").description.split(
                            "Direct nested definitions: ", 1
                        )[1]
                    )
                    assert ("look_up" in direct) == (change not in {"hidden", "model_only"})
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("start")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            if next_turn:
                events = [event async for event in runtime.stream("continue")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == (4 if next_turn else 3)
            with sqlite3.connect(tmp_path / "sessions.db") as db:
                rows = db.execute(
                    "SELECT status,result_json FROM tool_executions WHERE tool_name='look-up'"
                ).fetchall()
            assert len(rows) == 2
            errors = change in {"removed", "input_kind"} or (
                change == "invalid_schema" and kind == "json"
            )
            for status, payload in rows:
                result = json.loads(payload)
                assert status == "completed" and result["is_error"] is errors
                assert result.get("dispatch_error", False) is errors
                if not errors:
                    assert result["content"] == "current" + "X" * 400
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
