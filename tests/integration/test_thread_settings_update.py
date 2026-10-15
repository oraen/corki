"""Thread defaults never retarget an admitted Turn or its recovery snapshot."""

import asyncio
import json
import re
import sqlite3
import threading
from dataclasses import replace

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import ContextCompacted, TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.protocol.truncation import TruncationPolicy
from corki.sessions import TurnRecord, TurnStatus
from corki.skills.context import SkillContextContributor
from corki.skills.service import SkillService
from corki.tools import ToolRegistry


def settings(tmp_path, **changes):
    return CorkiSettings(
        working_directory=tmp_path,
        model="large",
        reasoning_effort="low",
        reasoning_summary="concise",
        service_tier="priority",
        skills_enabled=False,
        include_environment_context=False,
        model_contexts=(
            ModelContextInfo(
                "large",
                200000,
                truncation_policy=TruncationPolicy("bytes", 1200),
                service_tiers=("priority",),
            ),
            ModelContextInfo(
                "small",
                20000,
                truncation_policy=TruncationPolicy("bytes", 120),
                service_tiers=("priority",),
            ),
        ),
        **changes,
    )


class Model:
    def __init__(self, calls=False, code_mode=False):
        self.requests = []
        self.calls = calls
        self.code_mode = code_mode

    async def stream(self, request):
        self.requests.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        if self.calls and len(self.requests) in (1, 3):
            call = (
                ToolCall(
                    f"exec-{len(self.requests)}",
                    "exec",
                    None,
                    raw_arguments="text(await tools.hold({}));",
                    input_kind="freeform",
                )
                if self.code_mode
                else ToolCall(f"call-{len(self.requests)}", "hold", {})
            )
            yield ModelCompleted((ToolCallItem(call, turn, step),))
        else:
            yield ModelCompleted((AssistantMessageItem("done", turn, step),))

    async def aclose(self):
        pass


async def make_runtime(tmp_path, model, *, registry=None, configured=None, thread=None):
    return await LangGraphRuntime.acreate(
        settings=configured or settings(tmp_path),
        model=model,
        database_path=tmp_path / "sessions.db",
        registry=registry or ToolRegistry(),
        home_path=tmp_path / "home",
        thread_id=thread,
    )


@pytest.mark.parametrize("mode", ["direct", "code_mode"])
def test_thread_update_during_tool_keeps_old_turn_and_updates_next_turn(tmp_path, mode):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        contexts = []

        class Hold:
            spec = ToolSpec("hold", "hold one call", {"type": "object"})

            async def execute(self, call, context):
                contexts.append(context)
                if len(contexts) == 1:
                    entered.set()
                    await release.wait()
                return ToolResult(call.id, call.name, "x" * 2000)

        registry = ToolRegistry()
        registry.register(Hold())
        model = Model(calls=True, code_mode=mode == "code_mode")
        runtime = await make_runtime(
            tmp_path, model, registry=registry, configured=settings(tmp_path, tool_mode=mode)
        )

        async def consume():
            return [e async for e in runtime.stream("first")]

        first = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            old_graph = runtime._graph
            changed = await asyncio.wait_for(
                runtime.update_thread_settings(model="small", reasoning_effort="high"), 3
            )
            assert changed.model == "small" and changed.reasoning_effort == "high"
            assert runtime._graph is old_graph and not first.done()
            assert len(model.requests) == 1
            release.set()
            assert isinstance((await first)[-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("second")][-1], TurnCompleted)
            assert [r.model for r in model.requests] == ["large", "large", "small", "small"]
            assert [r.reasoning_effort for r in model.requests] == ["low", "low", "high", "high"]
            assert [c.model_output_policy.limit for c in contexts] == [1200, 120]
            assert old_graph._settings.model == "large"
            assert runtime._graph._window_manager._context_window_tokens == 19000
            assert runtime.thread_settings == changed
            saved = await runtime._repository.load_thread_model_settings(runtime.thread_id)
            assert (saved.model, saved.reasoning_effort) == ("small", "high")
            with sqlite3.connect(runtime._repository.path) as db:
                saved_turns = db.execute(
                    "SELECT model_settings_json FROM turns ORDER BY rowid"
                ).fetchall()
            assert [json.loads(row[0])["model"] for row in saved_turns] == ["large", "small"]
        finally:
            release.set()
            await asyncio.gather(first, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_budget_tool_and_skill_catalog_use_admitted_window_after_update(tmp_path):
    async def scenario():
        skill_root = tmp_path / "skills-home" / "skills"
        for index in range(20):
            directory = skill_root / f"fixture-{index:02d}"
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text(
                f"---\nname: fixture-{index:02d}\ndescription: "
                + "unique-description " * 30
                + "\n---\nBody"
            )
        service = SkillService(
            home=skill_root.parent,
            project_root=tmp_path,
            bundled_enabled=False,
            context_window_tokens=200000,
        )

        class BudgetModel(Model):
            async def stream(self, request):
                self.requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(self.requests) in (1, 3):
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    f"budget-{len(self.requests)}", "get_context_remaining", {}
                                ),
                                turn,
                                step,
                            ),
                        )
                    )
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

        model = BudgetModel()
        runtime = await LangGraphRuntime.acreate(
            settings=settings(tmp_path, token_budget_enabled=True),
            model=model,
            database_path=tmp_path / "s.db",
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            context_contributors=(SkillContextContributor(service),),
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            old_graph = runtime._graph
            await runtime.update_thread_settings(model="small")
            assert isinstance([e async for e in runtime.stream("second")][-1], TurnCompleted)
            first_catalog = next(
                i.content
                for i in model.requests[0].items
                if getattr(i, "key", None) == "extensions.skills.catalog"
            )
            last_catalog = next(
                i.content
                for i in reversed(model.requests[2].items)
                if getattr(i, "key", None) == "extensions.skills.catalog"
            )
            assert "unique-description" in first_catalog
            assert len(last_catalog) < len(first_catalog)
            assert service._catalog_budget.limit == 4000
            assert old_graph._window_manager._context_window_tokens == 190000
            results = [
                i
                for i in await runtime._repository.load_items(runtime.thread_id)
                if getattr(i, "tool_name", None) == "get_context_remaining"
            ]
            remaining = [int(r.content.split("You have ")[1].split(" tokens")[0]) for r in results]
            assert len(remaining) == 2 and remaining[0] > 100000 and 0 < remaining[1] < 20000
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("change_provider", [False, True])
def test_real_checkpoint_keeps_pinned_model_metadata_and_checks_provider_owner(
    tmp_path, change_provider
):
    async def scenario():
        class Sink:
            async def emit(self, event):
                pass

        runtime = await make_runtime(tmp_path, Model())
        await runtime._ensure_ready()
        turn = new_turn_id()
        user = UserMessageItem("prepared once", turn)
        state = _initial_state(runtime.thread_id, turn, runtime._settings, user)
        await runtime._repository.save_turn(
            TurnRecord(
                turn,
                runtime.thread_id,
                TurnStatus.RUNNING,
                user.content,
                model_settings=state["turn_model_settings"],
            )
        )
        await runtime._compiled.ainvoke(
            state,
            config=runtime._graph_config(turn),
            context=GraphRunContext(events=Sink()),
            interrupt_before=["call_model"],
        )
        await runtime.update_thread_settings(model="small", reasoning_effort="high")
        await runtime.aclose()
        current = replace(
            settings(tmp_path),
            model="small",
            reasoning_effort="high",
            provider_name="different" if change_provider else None,
            model_contexts=(ModelContextInfo("large", 3000), ModelContextInfo("small", 20000)),
        )
        model = Model()
        cold = await make_runtime(tmp_path, model, configured=current, thread=runtime.thread_id)
        try:
            if change_provider:
                with pytest.raises(ValueError, match="different provider"):
                    _ = [e async for e in cold.resume_pending()]
                assert not model.requests
            else:
                assert isinstance([e async for e in cold.resume_pending()][-1], TurnCompleted)
                assert (model.requests[0].model, model.requests[0].model_info.context_window) == (
                    "large",
                    200000,
                )
                assert model.requests[0].model_info.truncation_policy.limit == 1200
                assert cold.thread_settings.model == "small"
                assert isinstance([e async for e in cold.stream("future")][-1], TurnCompleted)
                assert model.requests[-1].model == "small"
                assert len(model.requests) == 2
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "close", "fail"])
def test_settings_commit_is_atomic_with_publication_and_lifecycle(tmp_path, monkeypatch, action):
    async def scenario():
        runtime = await make_runtime(tmp_path, Model())
        await runtime._ensure_ready()
        before = runtime.thread_settings
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        write = runtime._repository._save_thread_model_settings

        def held(*args):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)
            if action == "fail":
                raise OSError("fixture rejected commit")
            write(*args)

        monkeypatch.setattr(runtime._repository, "_save_thread_model_settings", held)
        caller = asyncio.create_task(runtime.update_thread_settings(model="small"))
        closer = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert runtime.thread_settings == before
            assert not runtime._model.requests
            if action == "cancel":
                caller.cancel()
                await asyncio.sleep(0)
                caller.cancel()
            elif action == "close":
                closer = asyncio.create_task(runtime.aclose())
                async with asyncio.timeout(3):
                    while not runtime._closed:
                        await asyncio.sleep(0)
            assert not caller.done()
            assert closer is None or not closer.done()
            release.set()
            outcome = (await asyncio.gather(caller, return_exceptions=True))[0]
            if closer is not None:
                await closer
            if action == "cancel":
                assert isinstance(outcome, asyncio.CancelledError)
            elif action == "fail":
                assert isinstance(outcome, OSError)
            else:
                assert outcome.model == "small"
            expected = "large" if action == "fail" else "small"
            assert runtime.thread_settings.model == expected
            saved = await runtime._repository.load_thread_model_settings(runtime.thread_id)
            assert saved.model == expected and not runtime._model.requests
        finally:
            release.set()
            await asyncio.gather(caller, return_exceptions=True)
            if closer is not None:
                await closer
            await runtime.aclose()

    asyncio.run(scenario())


def test_manual_compaction_uses_next_turn_settings_without_overwriting_defaults(tmp_path):
    async def scenario():
        model = Model()
        runtime = await make_runtime(tmp_path, model)
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            selected = await runtime.update_thread_settings(model="small", reasoning_effort="high")
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnCompleted)
            assert any(isinstance(e, ContextCompacted) for e in events)
            assert (
                model.requests[-1].model == "small"
                and model.requests[-1].reasoning_effort == "high"
            )
            assert runtime.thread_settings == selected
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_settings_only_sparse_updates_clear_effort_and_default_tier_without_sampling(tmp_path):
    async def scenario():
        model = Model()
        runtime = await make_runtime(tmp_path, model)
        try:
            first = await runtime.update_thread_settings(model="small")
            assert (
                first.model,
                first.reasoning_effort,
                first.reasoning_summary,
                first.service_tier,
            ) == ("small", "low", "concise", "priority")
            second = await runtime.update_thread_settings(reasoning_effort=None, service_tier=None)
            assert (second.model, second.reasoning_effort, second.service_tier) == (
                "small",
                None,
                "default",
            )
            assert runtime.thread_settings == second and first.reasoning_effort == "low"
            assert not model.requests
            assert not await runtime._repository.load_items(runtime.thread_id)
            assert isinstance([e async for e in runtime.stream("now")][-1], TurnCompleted)
            assert (
                model.requests[0].model,
                model.requests[0].reasoning_effort,
                model.requests[0].service_tier,
            ) == ("small", None, "default")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "patch",
    [
        {"model": ""},
        {"reasoning_effort": ""},
        {"reasoning_summary": "invalid"},
        {"service_tier": 123},
    ],
)
def test_invalid_settings_do_not_replace_defaults_or_admitted_graph(tmp_path, patch):
    async def scenario():
        runtime = await make_runtime(tmp_path, Model())
        try:
            await runtime.update_thread_settings()
            before = runtime.thread_settings
            graph = runtime._graph
            with pytest.raises(ValueError):
                await runtime.update_thread_settings(**patch)
            assert runtime.thread_settings == before and runtime._graph is graph
            assert not runtime._model.requests
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_first_turn_record_preserves_settings_before_any_checkpoint(tmp_path, monkeypatch):
    async def scenario():
        model = Model()
        runtime = await make_runtime(tmp_path, model)
        await runtime._ensure_ready()
        save = runtime._repository.save_turn

        async def save_running_only(turn):
            if turn.status is not TurnStatus.RUNNING:
                raise OSError("fixture terminal commit unavailable")
            await save(turn)

        async def crash_before_checkpoint(*args, **kwargs):
            await runtime.update_thread_settings(model="small", reasoning_effort="high")
            raise OSError("fixture process death before graph checkpoint")

        monkeypatch.setattr(runtime._repository, "save_turn", save_running_only)
        monkeypatch.setattr(runtime._repository, "retry_turn_terminal", save_running_only)
        monkeypatch.setattr(runtime._compiled, "ainvoke", crash_before_checkpoint)
        try:
            assert isinstance([e async for e in runtime.stream("admitted once")][-1], TurnFailed)
            turn = await runtime._repository.latest_running_turn(runtime.thread_id)
            assert turn.model_settings.model == "large"
            assert not model.requests
        finally:
            # Simulate process loss of pending writes before releasing fixture resources.
            runtime._pending_terminals.clear()
            await runtime.aclose()
        cold_model = Model()
        cold = await make_runtime(
            tmp_path,
            cold_model,
            thread=runtime.thread_id,
            configured=replace(settings(tmp_path), model="small", reasoning_effort="high"),
        )
        try:
            assert isinstance([e async for e in cold.resume_pending()][-1], TurnCompleted)
            assert cold_model.requests[0].model == "large"
            assert cold.thread_settings.model == "small"
            assert isinstance([e async for e in cold.stream("future")][-1], TurnCompleted)
            assert cold_model.requests[-1].model == "small"
            history = await cold._repository.load_items(cold.thread_id)
            assert [i.content for i in history if isinstance(i, UserMessageItem)] == [
                "admitted once",
                "future",
            ]
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_model_update_preserves_body_prefix_and_next_growth_triggers_compaction(tmp_path):
    async def scenario():
        class UsageModel(Model):
            async def stream(self, request):
                self.requests.append(request)
                count = (6000, 8500, 20, 100)[len(self.requests) - 1]
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),),
                    ModelUsage(count, 10),
                )

        model = UsageModel()
        runtime = await make_runtime(
            tmp_path,
            model,
            configured=replace(
                settings(
                    tmp_path,
                    auto_compact_tokens=2000,
                    auto_compact_token_limit_scope="body_after_prefix",
                ),
                model="small",
            ),
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            prefix = runtime._graph._window_manager._body_prefix
            await runtime.update_thread_settings(model="large")
            assert isinstance([e async for e in runtime.stream("second")][-1], TurnCompleted)
            assert runtime._graph._window_manager._body_prefix is prefix
            events = [e async for e in runtime.stream("third")]
            assert isinstance(events[-1], TurnCompleted)
            assert any(isinstance(e, ContextCompacted) for e in events)
            assert [r.model for r in model.requests] == ["small", "large", "large", "large"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("tier_supported", [False, True])
def test_actual_provider_requests_follow_committed_thread_settings(
    tmp_path, monkeypatch, mode, tier_supported
):
    async def scenario():
        bodies = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/" + (
                "responses" if mode == "responses" else "chat/completions"
            )
            body = json.loads(request.content)
            bodies.append(body)
            if mode == "responses":
                packet = {
                    "type": "response.completed",
                    "response": {
                        "id": f"r-{len(bodies)}",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "done"}],
                            }
                        ],
                    },
                }
            else:
                packet = {
                    "choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}]
                }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings(
                tmp_path,
                api_mode=mode,
                provider_name="openai",
                api_base="https://fixture.invalid/v1",
                api_key="fixture",
                supports_service_tier=tier_supported,
            ),
            database_path=tmp_path / "s.db",
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            assert runtime.thread_settings.reasoning_effort == "low"
            await runtime.update_thread_settings(
                model="small",
                reasoning_effort=None,
                reasoning_summary="detailed",
                service_tier=None,
            )
            assert len(bodies) == 1
            assert isinstance([e async for e in runtime.stream("second")][-1], TurnCompleted)
            assert [b["model"] for b in bodies] == ["large", "small"]
            assert bodies[0].get("service_tier") == ("priority" if tier_supported else None)
            assert "service_tier" not in bodies[1]
            assert runtime.thread_settings.reasoning_effort is None
            if mode == "responses":
                assert bodies[0]["reasoning"] == {"effort": "low", "summary": "concise"}
                assert bodies[1]["reasoning"] == {"summary": "detailed"}
            else:
                assert all("reasoning_effort" not in body for body in bodies)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_concurrent_sparse_updates_merge_against_last_committed_defaults(tmp_path, monkeypatch):
    async def scenario():
        runtime = await make_runtime(tmp_path, Model())
        await runtime._ensure_ready()
        entered, release = asyncio.Event(), asyncio.Event()
        save = runtime._repository.save_thread_model_settings
        seen = []

        async def hold(thread, value):
            seen.append(value)
            if len(seen) == 1:
                entered.set()
                await release.wait()
            await save(thread, value)

        monkeypatch.setattr(runtime._repository, "save_thread_model_settings", hold)
        first = asyncio.create_task(runtime.update_thread_settings(model="small"))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            second = asyncio.create_task(runtime.update_thread_settings(reasoning_effort="high"))
            await asyncio.sleep(0)
            assert not second.done() and runtime.thread_settings.model == "large"
            release.set()
            a, b = await asyncio.gather(first, second)
            assert (a.model, a.reasoning_effort) == ("small", "low")
            assert (b.model, b.reasoning_effort) == ("small", "high")
            assert [(v.model, v.reasoning_effort) for v in seen] == [
                ("small", "low"),
                ("small", "high"),
            ]
            assert not runtime._model.requests
        finally:
            release.set()
            await asyncio.gather(
                first, *([second] if second is not None else []), return_exceptions=True
            )
            await runtime.aclose()

    asyncio.run(scenario())


def test_code_mode_cell_retains_admitted_call_but_new_calls_use_current_worker(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        contexts, cell_ids = [], []

        class Hold:
            spec = ToolSpec("hold", "fixture", {"type": "object"})

            async def execute(self, call, context):
                contexts.append(context)
                if len(contexts) == 1:
                    entered.set()
                    await release.wait()
                return ToolResult(call.id, call.name, "finished")

        class CellModel(Model):
            async def stream(self, request):
                self.requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(self.requests) == 1:
                    call = ToolCall(
                        "cell-exec",
                        "exec",
                        None,
                        raw_arguments=(
                            '// @exec: {"yield_time_ms":10}\n'
                            "await tools.hold({}); text(await tools.hold({}));"
                        ),
                        input_kind="freeform",
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                elif len(self.requests) == 2:
                    await asyncio.wait_for(entered.wait(), 3)
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    cell_ids.append(re.search(r"cell ID (\S+)", result.content)[1])
                    yield ModelCompleted((AssistantMessageItem("background running", turn, step),))
                elif len(self.requests) == 3:
                    release.set()
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall("cell-wait", "wait", {"cell_id": cell_ids[0]}), turn, step
                            ),
                        )
                    )
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

        registry = ToolRegistry()
        registry.register(Hold())
        model = CellModel()
        runtime = await make_runtime(
            tmp_path, model, registry=registry, configured=settings(tmp_path, tool_mode="code_mode")
        )
        try:
            assert isinstance(
                [e async for e in runtime.stream("start background")][-1], TurnCompleted
            )
            assert len(contexts) == 1
            await runtime.update_thread_settings(model="small")
            assert isinstance([e async for e in runtime.stream("wait for it")][-1], TurnCompleted)
            assert len(contexts) == 2
            # Native broker receives later invocations through the current Step
            # worker; only already-admitted calls retain the previous owner.
            assert [c.model_output_policy.limit for c in contexts] == [1200, 120]
            assert [r.model for r in model.requests] == ["large", "large", "small", "small"]
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())
