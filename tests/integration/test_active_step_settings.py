"""Live publication cannot retarget captured requests, tools or future Turns."""

import asyncio
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.model_settings import capture_model_settings
from corki.core.runtime import _initial_state
from corki.core.step_settings import StepSettingsState, resolve_model_metadata
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.protocol.collaboration import CollaborationMode
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, UserMessageItem, new_step_id
from corki.protocol.model_authority import ModelAuthority
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.skills.context import SkillContextContributor
from corki.skills.service import SkillService
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["direct", "code_mode"])
@pytest.mark.parametrize("collaboration_mode", ["default", "plan"])
def test_active_update_changes_next_step_not_admitted_tool_or_future_turn(
    tmp_path, mode, collaboration_mode
):
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
        configured = settings(
            tmp_path,
            tool_mode=mode,
            step_model_switching=True,
            collaboration_mode=collaboration_mode,
        )
        model = Model(calls=True, code_mode=mode == "code_mode")
        runtime = await make_runtime(tmp_path, model, registry=registry, configured=configured)

        async def consume():
            return [e async for e in runtime.stream("first")]

        work = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            turn_id = runtime._active_run.turn_id
            if collaboration_mode == "plan":
                # A later Thread default must not leak into model resolution
                # for the already admitted Plan-mode task.
                await runtime.update_thread_settings(
                    collaboration_mode=CollaborationMode("default", "large", "low")
                )
            original = runtime.thread_settings
            result = await runtime.update_turn_settings(
                turn_id, model="small", reasoning_effort="high"
            )
            assert result.status == "applied"
            assert runtime.active_turn_settings.model == "small"
            assert runtime.active_turn_settings.collaboration_mode == collaboration_mode
            assert runtime.thread_settings == original
            assert len(model.requests) == 1 and not work.done()
            release.set()
            assert isinstance((await work)[-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("second")][-1], TurnCompleted)
            assert [r.model for r in model.requests] == ["large", "small", "large", "large"]
            assert [r.reasoning_effort for r in model.requests] == ["low", "high", "low", "low"]
            assert [c.model_output_policy.limit for c in contexts] == [1200, 1200]
            assert await contexts[0].remaining_context_tokens() > 100000
            assert runtime.active_turn_settings is None
        finally:
            release.set()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


class HeldModel:
    def __init__(self, *, calls=True):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.requests = []
        self.calls = calls

    async def stream(self, request):
        self.requests.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        if len(self.requests) == 1:
            self.entered.set()
            await self.release.wait()
        if len(self.requests) == 1 and self.calls:
            yield ModelCompleted((ToolCallItem(ToolCall("one", "missing", {}), turn, step),))
        else:
            yield ModelCompleted((AssistantMessageItem("done", turn, step),))

    async def aclose(self):
        pass


async def collect(runtime):
    return [event async for event in runtime.stream("request")]


def test_active_model_switch_preserves_admitted_personality_after_thread_update(tmp_path):
    async def scenario():
        model = HeldModel()
        configured = replace(settings(tmp_path, step_model_switching=True), personality="friendly")
        runtime = await make_runtime(tmp_path, model, configured=configured)
        work = asyncio.create_task(collect(runtime))
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            turn = runtime._active_run.turn_id
            await runtime.update_thread_settings(personality="pragmatic")
            assert runtime.thread_settings.personality == "pragmatic"
            result = await runtime.update_turn_settings(turn, model="small")
            assert result.status == "applied"
            assert runtime.active_turn_settings.personality == "friendly"
            assert len(model.requests) == 1 and not work.done()
            model.release.set()
            assert isinstance((await work)[-1], TurnCompleted)
            checkpoint = await runtime._checkpointer.aget_tuple(runtime._graph_config(turn))
            channels = checkpoint.checkpoint["channel_values"]
            assert channels["turn_model_settings"].personality == "friendly"
            assert channels["step_model_settings"].personality == "friendly"
            assert runtime.thread_settings.personality == "pragmatic"
        finally:
            model.release.set()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_sparse_updates_serialize_resolution_and_preserve_captured_request(tmp_path):
    async def scenario():
        resolving, release_lookup = asyncio.Event(), asyncio.Event()
        lookups = []

        async def resolve(host, model):
            lookups.append(model)
            resolving.set()
            await release_lookup.wait()
            return await resolve_model_metadata(host, model)

        model = HeldModel()
        runtime = await LangGraphRuntime.acreate(
            settings=settings(tmp_path, step_model_switching=True),
            model=model,
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            model_metadata_resolver=resolve,
        )
        work = asyncio.create_task(collect(runtime))
        updates = []
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            turn = runtime._active_run.turn_id
            original = runtime.active_turn_settings
            updates.append(asyncio.create_task(runtime.update_turn_settings(turn, model="small")))
            await asyncio.wait_for(resolving.wait(), 5)
            updates.append(
                asyncio.create_task(runtime.update_turn_settings(turn, reasoning_effort=None))
            )
            updates.append(
                asyncio.create_task(runtime.update_turn_settings(turn, service_tier=None))
            )
            assert runtime.active_turn_settings is original
            release_lookup.set()
            assert [r.status for r in await asyncio.gather(*updates)] == ["applied"] * 3
            selected = runtime.active_turn_settings
            assert (
                selected.model,
                selected.reasoning_effort,
                selected.reasoning_summary,
                selected.service_tier,
            ) == (
                "small",
                None,
                "concise",
                "default",
            )
            assert (
                await runtime.update_turn_settings(
                    turn, model="small", reasoning_summary="detailed"
                )
            ).status == "applied"
            assert runtime.active_turn_settings.model_info is selected.model_info
            assert lookups == ["small"]
            assert model.requests[0].model == "large" and len(model.requests) == 1
            model.release.set()
            assert isinstance((await work)[-1], TurnCompleted)
            assert [r.model for r in model.requests] == ["large", "small"]
            assert model.requests[-1].reasoning_summary == "detailed"
            assert runtime.thread_settings == original
        finally:
            release_lookup.set()
            model.release.set()
            await asyncio.gather(work, *updates, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_publication_during_async_prepare_does_not_change_captured_step(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = Model(calls=True)
        runtime = await make_runtime(
            tmp_path, model, configured=settings(tmp_path, step_model_switching=True)
        )
        refresh = runtime._graph._refresh_input_tools
        first = True

        async def held_refresh(state):
            nonlocal first
            if first:
                first = False
                entered.set()
                await release.wait()
            return await refresh(state)

        runtime._graph._refresh_input_tools = held_refresh
        work = asyncio.create_task(collect(runtime))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            turn = runtime._active_run.turn_id
            checkpoint = await runtime._checkpointer.aget_tuple(runtime._graph_config(turn))
            assert checkpoint.checkpoint["channel_values"]["step_model_settings"].model == "large"
            assert (await runtime.update_turn_settings(turn, model="small")).status == "applied"
            assert not model.requests
            release.set()
            assert isinstance((await work)[-1], TurnCompleted)
            assert [request.model for request in model.requests] == ["large", "small"]
        finally:
            release.set()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "action", ["finish", "cancel", "close", "caller_cancel", "caller_cancel_error", "done_identity"]
)
def test_delayed_resolution_never_retargets_or_outlives_owned_work(tmp_path, action):
    async def scenario():
        entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def resolve(host, model):
            entered.set()
            try:
                await release.wait()
                raise ValueError("prepared rejection loses to unavailable target")
            finally:
                cleaned.set()
                if action == "caller_cancel_error":
                    raise OSError("resolver cleanup fixture")

        model = HeldModel(calls=False)
        runtime = await LangGraphRuntime.acreate(
            settings=settings(tmp_path, step_model_switching=True),
            model=model,
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            model_metadata_resolver=resolve,
        )
        work = asyncio.create_task(collect(runtime))
        update = None
        original_done = None
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            run = runtime._active_run
            update = asyncio.create_task(runtime.update_turn_settings(run.turn_id, model="small"))
            await asyncio.wait_for(entered.wait(), 5)
            if action == "finish":
                model.release.set()
                assert isinstance((await work)[-1], TurnCompleted)
                assert isinstance((await collect(runtime))[-1], TurnCompleted)
            elif action == "cancel":
                await runtime.cancel_active()
            elif action == "close":
                await asyncio.wait_for(runtime.aclose(), 5)
            elif action in ("caller_cancel", "caller_cancel_error"):
                update.cancel()
            else:
                original_done = run.done
                run.done = asyncio.Event()
            if action in ("close", "caller_cancel", "caller_cancel_error"):
                with pytest.raises(asyncio.CancelledError):
                    await update
                assert cleaned.is_set()
            else:
                release.set()
                assert (await update).status == "target_unavailable"
            assert runtime.thread_settings.model == "large"
            if action in ("caller_cancel", "caller_cancel_error"):
                assert runtime.active_turn_settings.model == "large" and not work.done()
            assert not runtime._turn_updates._tasks
        finally:
            if original_done is not None:
                run.done = original_done
            release.set()
            model.release.set()
            await asyncio.gather(work, *([update] if update else []), return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["prepare_model_context", "call_model", "execute_tools"])
def test_cold_checkpoint_restores_step_without_resampling_or_retargeting(tmp_path, boundary):
    async def scenario():
        class Sink:
            async def emit(self, event):
                pass

        configured = settings(tmp_path, step_model_switching=True)
        budgets = []

        class Probe:
            spec = ToolSpec("hold", "read retained turn budget", {"type": "object"})

            async def execute(self, call, context):
                budgets.append(await context.remaining_context_tokens())
                return ToolResult(call.id, call.name, "done")

        registry = ToolRegistry()
        registry.register(Probe())
        first_model = Model(calls=True)
        runtime = await make_runtime(
            tmp_path, first_model, configured=configured, registry=registry
        )
        await runtime._ensure_ready()
        turn = new_turn_id()
        user = UserMessageItem("persisted", turn)
        state = _initial_state(runtime.thread_id, turn, configured, user)
        initial = state["turn_model_settings"]
        selected = capture_model_settings(
            replace(configured, model="small", reasoning_effort="high")
        )
        await runtime._repository.save_turn(
            TurnRecord(
                turn,
                runtime.thread_id,
                TurnStatus.RUNNING,
                user.content,
                model_settings=initial,
            )
        )
        await runtime._compiled.ainvoke(
            state,
            config=runtime._graph_config(turn),
            context=GraphRunContext(events=Sink(), models=StepSettingsState(initial, selected)),
            interrupt_before=[boundary],
        )
        checkpoint = await runtime._checkpointer.aget_tuple(runtime._graph_config(turn))
        assert checkpoint.checkpoint["channel_values"]["step_model_settings"] == selected
        await runtime.aclose()
        model = Model()
        registry = ToolRegistry()
        registry.register(Probe())
        cold = await make_runtime(
            tmp_path,
            model,
            configured=replace(configured, model_contexts=()),
            thread=runtime.thread_id,
            registry=registry,
        )
        try:
            assert isinstance([e async for e in cold.resume_pending()][-1], TurnCompleted)
            assert [r.model for r in model.requests] == ["small"]
            assert model.requests[0].model_info == selected.model_info
            assert len(first_model.requests) == (1 if boundary == "execute_tools" else 0)
            assert cold.thread_settings.model == "large"
            if boundary == "execute_tools":
                assert len(budgets) == 1 and budgets[0] > 100000
            else:
                assert budgets == []
            items = await cold._repository.load_items(cold.thread_id)
            assert sum(isinstance(item, UserMessageItem) for item in items) == 1
            assert sum(isinstance(item, ToolCallItem) for item in items) == (
                1 if boundary == "execute_tools" else 0
            )
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_retry_keeps_captured_step_after_successful_update(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Retrying:
            def __init__(self):
                self.requests = []

            async def stream(self, request):
                self.requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(self.requests) == 1:
                    entered.set()
                    await release.wait()
                    raise ModelError(
                        "retry",
                        kind=ModelErrorKind.TRANSPORT,
                        retryable=True,
                        retry_after_seconds=0,
                    )
                if len(self.requests) == 2:
                    yield ModelCompleted(
                        (ToolCallItem(ToolCall("one", "missing", {}), turn, step),)
                    )
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        model = Retrying()
        runtime = await make_runtime(
            tmp_path, model, configured=settings(tmp_path, step_model_switching=True)
        )
        work = asyncio.create_task(collect(runtime))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert (
                await runtime.update_turn_settings(runtime._active_run.turn_id, model="small")
            ).status == "applied"
            release.set()
            assert isinstance((await work)[-1], TurnCompleted)
            assert [r.model for r in model.requests] == ["large", "large", "small"]
            assert model.requests[0].items == model.requests[1].items
        finally:
            release.set()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_successful_publication_does_not_force_another_sample(tmp_path):
    async def scenario():
        model = HeldModel(calls=False)
        runtime = await make_runtime(
            tmp_path, model, configured=settings(tmp_path, step_model_switching=True)
        )
        work = asyncio.create_task(collect(runtime))
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            result = await runtime.update_turn_settings(runtime._active_run.turn_id, model="small")
            assert result.status == "applied"
            model.release.set()
            assert isinstance((await work)[-1], TurnCompleted)
            assert [r.model for r in model.requests] == ["large"]
        finally:
            model.release.set()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"cyber": True},
        {"computer_use_review_required": True},
        {"node_repl_disabled": True},
        {"reviewer": "other"},
        {"policy": "0" * 64},
        {"node_policy": "0" * 64},
        {"policy_template": "0" * 64},
        {"guardian": "0" * 64},
    ],
)
def test_runtime_rejects_changes_to_admitted_model_authority(tmp_path, change):
    async def scenario():
        configured = settings(tmp_path, step_model_switching=True)
        large, small = configured.model_contexts
        configured = replace(
            configured,
            model_contexts=(large, replace(small, activation_authority=ModelAuthority(**change))),
        )
        model = HeldModel(calls=False)
        runtime = await make_runtime(tmp_path, model, configured=configured)
        work = asyncio.create_task(collect(runtime))
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            original = runtime.active_turn_settings
            result = await runtime.update_turn_settings(runtime._active_run.turn_id, model="small")
            assert result.status == "rejected" and "authority" in result.reason
            assert runtime.active_turn_settings is original
            model.release.set()
            assert isinstance((await work)[-1], TurnCompleted)
            assert [r.model for r in model.requests] == ["large"]
        finally:
            model.release.set()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_feature_gate_does_not_start_a_turn(tmp_path):
    async def scenario():
        runtime = await make_runtime(tmp_path, Model())
        try:
            result = await runtime.update_turn_settings("absent", model="small")
            assert result.status == "rejected" and "step_model_switching" in result.reason
            assert runtime._active_run is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("fast_mode", [False, True])
@pytest.mark.parametrize("destination", [None, "small"])
def test_active_tier_alias_retains_selection_independent_of_fast_mode(
    tmp_path, fast_mode, destination
):
    async def scenario():
        model = HeldModel()
        runtime = await make_runtime(
            tmp_path,
            model,
            configured=settings(
                tmp_path,
                step_model_switching=True,
                fast_mode=fast_mode,
            ),
        )
        work = asyncio.create_task(collect(runtime))
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            result = await runtime.update_turn_settings(
                runtime._active_run.turn_id,
                model=destination,
                service_tier="fast",
            )
            assert result.status == "applied"
            assert runtime.active_turn_settings.service_tier == "priority"
            model.release.set()
            assert isinstance((await work)[-1], TurnCompleted)
            assert model.requests[-1].service_tier == ("priority" if fast_mode else None)
        finally:
            model.release.set()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_present_invalid_step_checkpoint_fails_before_sampling(tmp_path):
    async def scenario():
        class Sink:
            async def emit(self, event):
                pass

        configured = settings(tmp_path, step_model_switching=True)
        model = Model()
        runtime = await make_runtime(tmp_path, model, configured=configured)
        await runtime._ensure_ready()
        turn = new_turn_id()
        user = UserMessageItem("pending", turn)
        state = _initial_state(runtime.thread_id, turn, configured, user)
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
        await runtime._compiled.aupdate_state(
            runtime._graph_config(turn), {"step_model_settings": None}
        )
        try:
            with pytest.raises(ValueError, match="invalid checkpoint Step"):
                _ = [e async for e in runtime.resume_pending()]
            assert not model.requests and runtime._active_run is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("hook_event", [None, "PreCompact", "PostCompact"])
def test_local_auto_compaction_keeps_initial_turn_model_after_step_activation(
    tmp_path, monkeypatch, hook_event
):
    async def scenario():
        import json

        from corki.config.layers import ConfigLayer, LocalConfigState
        from corki.core.stop_hooks import command_identity

        entered, release = asyncio.Event(), asyncio.Event()
        payloads = []
        configuration = LocalConfigState(())
        if hook_event is not None:
            source = tmp_path / "config.toml"
            key = "pre_compact" if hook_event == "PreCompact" else "post_compact"
            fingerprint, _ = command_identity(
                {"type": "command", "command": "review"}, event_name=hook_event
            )
            document = (
                f'[[hooks.{hook_event}]]\n[[hooks.{hook_event}.hooks]]\ntype="command"\ncommand="review"\n'
                f"[hooks.state.{json.dumps(f'{source}:{key}:0:0')}]\n"
                f"trusted_hash={json.dumps(fingerprint)}\n"
            )
            configuration = LocalConfigState((ConfigLayer(source, "user", contents=document),))

            async def runner(command, payload, **kwargs):
                payloads.append(payload)
                return {"exit_code": 0, "stdout": "{}", "stderr": ""}

            monkeypatch.setattr("corki.core.compact_hooks.run_command", runner)

        class Compacting:
            def __init__(self):
                self.requests = []

            async def stream(self, request):
                self.requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(self.requests) == 1:
                    entered.set()
                    await release.wait()
                    yield ModelCompleted(
                        (
                            AssistantMessageItem("large body " * 14000, turn, step),
                            ToolCallItem(ToolCall("one", "missing", {}), turn, step),
                        )
                    )
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        model = Compacting()
        runtime = await make_runtime(
            tmp_path,
            model,
            configured=settings(
                tmp_path,
                step_model_switching=True,
                compact_prompt="SUMMARIZE NOW",
                configuration=configuration,
            ),
        )
        work = asyncio.create_task(collect(runtime))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert (
                await runtime.update_turn_settings(
                    runtime._active_run.turn_id, model="small", reasoning_effort="high"
                )
            ).status == "applied"
            release.set()
            assert isinstance((await work)[-1], TurnCompleted)
            assert [r.model for r in model.requests] == ["large", "large", "small"]
            assert [r.reasoning_effort for r in model.requests] == ["low", "low", "high"]
            assert model.requests[1].items[-1].content == "SUMMARIZE NOW"
            assert model.requests[1].model_info.context_window == 200000
            if hook_event is not None:
                assert len(payloads) == 1
                assert payloads[0]["model"] == model.requests[1].model
                assert payloads[0]["trigger"] == "auto"
        finally:
            release.set()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_skill_catalog_budget_keeps_thread_extension_model_after_activation(tmp_path):
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
        model = HeldModel()
        runtime = await LangGraphRuntime.acreate(
            settings=settings(tmp_path, step_model_switching=True),
            model=model,
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            context_contributors=(SkillContextContributor(service),),
        )
        work = asyncio.create_task(collect(runtime))
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            assert (
                await runtime.update_turn_settings(runtime._active_run.turn_id, model="small")
            ).status == "applied"
            model.release.set()
            assert isinstance((await work)[-1], TurnCompleted)
            catalogs = [
                next(
                    item.content
                    for item in reversed(request.items)
                    if getattr(item, "key", None) == "extensions.skills.catalog"
                )
                for request in model.requests
            ]
            assert [request.model for request in model.requests] == ["large", "small"]
            assert "unique-description" in catalogs[0]
            assert catalogs[0] == catalogs[1]
        finally:
            model.release.set()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_close_cancels_turn_before_joining_slow_catalog_cleanup(tmp_path):
    async def scenario():
        resolving, cleaning, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def resolve(host, model):
            resolving.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release_cleanup.wait()

        model = HeldModel()
        runtime = await LangGraphRuntime.acreate(
            settings=settings(tmp_path, step_model_switching=True),
            model=model,
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            model_metadata_resolver=resolve,
        )
        work = asyncio.create_task(collect(runtime))
        update = closing = None
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            run = runtime._active_run
            update = asyncio.create_task(runtime.update_turn_settings(run.turn_id, model="small"))
            await asyncio.wait_for(resolving.wait(), 5)
            closing = asyncio.create_task(runtime.aclose())
            await asyncio.wait_for(cleaning.wait(), 5)
            assert run.cancel_requested
            assert not closing.done()
            release_cleanup.set()
            await asyncio.wait_for(closing, 5)
            with pytest.raises(asyncio.CancelledError):
                await work
            assert len(model.requests) == 1
        finally:
            release_cleanup.set()
            model.release.set()
            await asyncio.gather(
                work, *[task for task in (update, closing) if task], return_exceptions=True
            )
            await runtime.aclose()

    asyncio.run(scenario())


def test_invalid_resolver_output_does_not_fall_back_to_static_catalog(tmp_path):
    async def scenario():
        async def invalid_resolver(host, model):
            return None

        model = HeldModel(calls=False)
        runtime = await LangGraphRuntime.acreate(
            settings=settings(tmp_path, step_model_switching=True),
            model=model,
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            model_metadata_resolver=invalid_resolver,
        )
        work = asyncio.create_task(collect(runtime))
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            original = runtime.active_turn_settings
            result = await runtime.update_turn_settings(runtime._active_run.turn_id, model="small")
            assert result.status == "rejected"
            assert runtime.active_turn_settings is original
        finally:
            model.release.set()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
