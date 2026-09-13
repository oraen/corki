"""Host policy changes must reach the first sample of a resumed pending Turn."""

import asyncio
import json

import pytest
from test_thread_settings_update import Model, settings

from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models.base import ModelErrorKind
from corki.models.failure import ModelFailure
from corki.prompting.store import PromptStore
from corki.protocol.events import ContextCompacted, TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("after_tools", [False, True, "failed_sample"])
@pytest.mark.parametrize("policy_text", ["NEW POLICY", ""])
@pytest.mark.parametrize("crash_after_refresh", [False, True])
def test_pending_turn_uses_current_host_policy_before_first_resumed_sample(
    tmp_path, monkeypatch, after_tools, policy_text, crash_after_refresh
):
    async def scenario():
        executions = []
        unexpected_contributions = []

        class UnavailableContributor:
            def contributions(self, **kwargs):
                unexpected_contributions.append(kwargs)
                raise OSError("unrelated contributor unavailable after cold restart")

        class Sink:
            async def emit(self, event):
                pass

        class Tool:
            spec = ToolSpec("hold", "fixture", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "committed")

        def create(text, model, thread=None):
            registry = ToolRegistry()
            registry.register(Tool())
            runtime = LangGraphRuntime.create(
                settings=settings(tmp_path),
                model=model,
                registry=registry,
                database_path=tmp_path / "policy.db",
                home_path=tmp_path / "home",
                thread_id=thread,
                mcp_requirements=compose_mcp_requirements(
                    (
                        MCPRequirementsLayer(
                            "host", "additional_developer_instructions = " + json.dumps(text)
                        ),
                    )
                ),
            )
            if thread is not None and after_tools is not True:
                # Already prepared requests need only the current host policy.
                # A new Step after tools must still run normal contributors.
                runtime._graph._context_builder._contributors += (UnavailableContributor(),)
            return runtime

        source = create("OLD POLICY", Model(calls=after_tools is True))
        try:
            await source._ensure_ready()
            turn = new_turn_id()
            user = UserMessageItem("admitted input", turn)
            state = _initial_state(source.thread_id, turn, source._settings, user)
            await source._repository.save_turn(
                TurnRecord(
                    turn,
                    source.thread_id,
                    TurnStatus.RUNNING,
                    user.content,
                    model_settings=state["turn_model_settings"],
                )
            )
            await source._compiled.ainvoke(
                state,
                config=source._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                **(
                    {"interrupt_after": ["execute_tools"]}
                    if after_tools is True
                    else {"interrupt_before": ["call_model"]}
                ),
            )
            if after_tools == "failed_sample":
                # Failure committed before its graph result/checkpoint: recovery
                # must consume that fact, advance the attempt, then refresh policy.
                await source._repository.save_model_failure(
                    source.thread_id,
                    turn,
                    0,
                    ModelFailure(
                        "fixture retry",
                        ModelErrorKind.CONNECTION.value,
                        True,
                        0,
                        retry_after_seconds=0,
                    ),
                )
            prefix = await source._repository.load_items(source.thread_id)
            thread = source.thread_id
        finally:
            await source.aclose()
        model = Model()
        cold = create(policy_text, model, thread)
        if crash_after_refresh:
            prepare = cold._graph._window_manager.prepare
            save_turn = cold._repository.save_turn

            async def committed_then_crash(*args, **kwargs):
                await prepare(*args, **kwargs)
                raise OSError("fixture crash after context commit")

            async def keep_running(record):
                if record.status is not TurnStatus.RUNNING:
                    raise OSError("fixture terminal write unavailable")
                await save_turn(record)

            with monkeypatch.context() as patch:
                patch.setattr(cold._graph._window_manager, "prepare", committed_then_crash)
                patch.setattr(cold._repository, "save_turn", keep_running)
                patch.setattr(cold._repository, "retry_turn_terminal", keep_running)
                try:
                    assert isinstance([e async for e in cold.resume_pending()][-1], TurnFailed)
                    assert not model.requests
                    assert await cold._repository.latest_running_turn(thread) is not None
                finally:
                    # Lose in-memory pending writes as in the simulated process crash.
                    cold._pending_terminals.clear()
                    await cold.aclose()
            cold = create(policy_text, model, thread)
        try:
            assert isinstance([e async for e in cold.resume_pending()][-1], TurnCompleted)
            assert len(model.requests) == 1
            policies = [
                i
                for i in model.requests[0].items
                if isinstance(i, ContextItem) and i.key == "managed_developer_instructions"
            ]
            assert len(policies) == 2
            assert ("NEW POLICY" if policy_text else "no longer apply") in policies[-1].content
            assert len(executions) == int(after_tools is True)
            assert not unexpected_contributions
            stored = await cold._repository.load_items(thread)
            assert stored[: len(prefix)] == prefix
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("fits_after_compaction", [False, True])
def test_recovered_policy_obeys_window_and_compaction_budget(tmp_path, fits_after_compaction):
    async def scenario():
        class Sink:
            async def emit(self, event):
                pass

        def create(policy, model, thread=None):
            return LangGraphRuntime.create(
                settings=settings(tmp_path, model_context_window_override=12000),
                model=model,
                registry=ToolRegistry(),
                database_path=tmp_path / "budget.db",
                home_path=tmp_path / "home",
                thread_id=thread,
                mcp_requirements=compose_mcp_requirements(
                    (
                        MCPRequirementsLayer(
                            "host", "additional_developer_instructions = " + json.dumps(policy)
                        ),
                    )
                ),
            )

        source = create("OLD POLICY", Model())
        try:
            await source._ensure_ready()
            if fits_after_compaction:
                await source._repository.append_items(
                    source.thread_id,
                    (AssistantMessageItem("old history " * 2000, new_turn_id(), new_step_id()),),
                )
            turn = new_turn_id()
            user = UserMessageItem("admitted input", turn)
            state = _initial_state(source.thread_id, turn, source._settings, user)
            await source._repository.save_turn(
                TurnRecord(
                    turn,
                    source.thread_id,
                    TurnStatus.RUNNING,
                    user.content,
                    model_settings=state["turn_model_settings"],
                )
            )
            await source._compiled.ainvoke(
                state,
                config=source._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                interrupt_before=["call_model"],
            )
            checkpoint = await source._compiled.aget_state(source._graph_config(turn))
            assert checkpoint.next == ("call_model",)
            thread = source.thread_id
            prefix = await source._repository.load_items(thread)
            assert not any(isinstance(item, CompactionItem) for item in prefix)
        finally:
            await source.aclose()

        # Both are valid host policies (<40k UTF-8 bytes). The smaller one fits
        # the frozen 12k window only after the old assistant history is compacted.
        model = Model()
        policy = "界" * (4000 if fits_after_compaction else 12000)
        cold = create(policy, model, thread)
        try:
            events = [event async for event in cold.resume_pending()]
            if fits_after_compaction:
                assert isinstance(events[-1], TurnCompleted)
                assert sum(isinstance(event, ContextCompacted) for event in events) == 1
                assert len(model.requests) == 2
                final = model.requests[-1]
                policies = [
                    item
                    for item in final.items
                    if isinstance(item, ContextItem)
                    and item.key == "managed_developer_instructions"
                ]
                assert len(policies) == 1
                assert policies[0].content == (
                    "<managed_developer_instructions>\n"
                    + policy
                    + "\n</managed_developer_instructions>"
                )
                assert (
                    sum(
                        isinstance(item, UserMessageItem) and item.content == user.content
                        for item in final.items
                    )
                    == 1
                )
                assert not any(
                    isinstance(item, AssistantMessageItem) and "old history " in item.content
                    for item in final.items
                )
                summary_requests = model.requests[:-1]
            else:
                assert isinstance(events[-1], TurnFailed)
                assert events[-1].error_kind == ModelErrorKind.CONTEXT_WINDOW.value
                assert not events[-1].retryable
                assert not any(isinstance(event, TurnCompleted) for event in events)
                # At most the ordinary summary request may run; no final sampling.
                assert len(model.requests) <= 1
                summary_requests = model.requests
            for request in summary_requests:
                assert not request.tools
                assert request.items[-1].content == PromptStore().render("tasks/compact")
                assert not any(
                    isinstance(item, ContextItem) and policy in item.content
                    for item in request.items
                )
            stored = await cold._repository.load_items(thread)
            assert stored[: len(prefix)] == prefix
            assert await cold._repository.latest_running_turn(thread) is None
        finally:
            await cold.aclose()

    asyncio.run(scenario())
