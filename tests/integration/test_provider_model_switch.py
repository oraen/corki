"""A provider switch must change the wire, while admitted Turns keep their client."""

import asyncio
import json

import pytest
from test_resume_model_settings import host  # noqa: F401 - shared isolated HTTP host fixture

from corki.cli.main import build_application_async
from corki.config.document import loads
from corki.protocol.events import ContextCompacted, TurnCompleted


def configure(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "default": {"provider": "alpha", "model": "shared"},
                "skills": {"enabled": False},
                "providers": {
                    "alpha": {
                        "api_mode": "responses",
                        "base_url": "https://alpha.invalid/v1",
                        "api_key": "alpha-key",
                        "max_retries": 0,
                        "models": {
                            "shared": {"context_window": 200000, "reasoning_effort": "medium"}
                        },
                    },
                    "beta": {
                        "base_url": "https://beta.invalid/v1",
                        "api_key": "beta-key",
                        "max_retries": 0,
                        "models": {"shared": {"context_window": 20000, "reasoning_effort": "high"}},
                    },
                },
            }
        )
    )


def test_switch_keeps_inflight_transport_then_changes_wire_and_compaction(host):  # noqa: F811
    path, requests = host
    configure(path)

    async def scenario():
        runtime = (await build_application_async())._runtime
        entered, release = asyncio.Event(), asyncio.Event()
        original = runtime._model.stream

        async def held(request):
            entered.set()
            await release.wait()
            async for event in original(request):
                yield event

        runtime._model.stream = held

        async def consume():
            return [e async for e in runtime.stream("first")]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await runtime.update_thread_settings(provider="beta", model="shared")
            assert runtime.active_turn_settings.provider == "alpha"
            release.set()
            assert isinstance((await task)[-1], TurnCompleted)
            assert requests[0][0] == "https://alpha.invalid/v1/responses"
            assert requests[0][2] == "Bearer alpha-key"
            assert isinstance([e async for e in runtime.stream("second")][-1], TurnCompleted)
            assert requests[-1][0] == "https://beta.invalid/v1/chat/completions"
            assert requests[-1][2] == "Bearer beta-key"
            assert runtime._graph._settings.main_context_limits.raw_tokens == 20000
            assert runtime._graph._window_manager._model is runtime._graph._model
            events = [e async for e in runtime.compact()]
            assert any(isinstance(e, ContextCompacted) for e in events)
            assert requests[-1][0] == "https://beta.invalid/v1/chat/completions"
            await runtime.update_thread_settings(provider="alpha", model="shared")
            assert isinstance([e async for e in runtime.stream("third")][-1], TurnCompleted)
            assert requests[-1][0] == "https://alpha.invalid/v1/responses"
            assert requests[-1][2] == "Bearer alpha-key"
        finally:
            release.set()
            await task
            await runtime.aclose()
        assert all(model._client.is_closed for model in runtime._provider_models.values())

    asyncio.run(scenario())


def test_failed_commit_keeps_provider_and_default(host, monkeypatch):  # noqa: F811
    path, requests = host
    configure(path)

    async def scenario():
        runtime = (await build_application_async())._runtime
        try:
            await runtime._ensure_ready()

            async def fail(*args, **kwargs):
                raise OSError("storage unavailable")

            monkeypatch.setattr(runtime._repository, "save_thread_model_settings", fail)
            with pytest.raises(OSError):
                await runtime.update_thread_settings(provider="beta", model="shared")
            assert runtime.thread_settings.provider == "alpha"
            assert loads(path.read_text())["default"]["provider"] == "alpha"
            assert isinstance([e async for e in runtime.stream("still alpha")][-1], TurnCompleted)
            assert requests[-1][2] == "Bearer alpha-key"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_cli_pair_selection_persists_default_and_reports_write_failure(host, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from corki.config import edits

    path, _ = host
    configure(path)

    async def scenario():
        app = await build_application_async()
        notices = []
        app._ui = SimpleNamespace(show_notice=notices.append)
        try:
            await app._set_model("shared(beta)", reasoning_effort="low")
            saved = loads(path.read_text())
            assert saved["default"] == {"provider": "beta", "model": "shared"}
            assert saved["providers"]["beta"]["models"]["shared"]["reasoning_effort"] == "low"
            assert app._runtime.thread_settings.provider == "beta"
            assert "saved as the default" in notices[-1]

            def fail(*args):
                raise OSError("secret must not leak")

            monkeypatch.setattr(edits, "save_model_default", fail)
            await app._set_model("shared(alpha)")
            assert app._runtime.thread_settings.provider == "alpha"
            assert loads(path.read_text())["default"]["provider"] == "beta"
            assert "Default could not be saved" in notices[-1]
            assert "secret" not in notices[-1]
            assert "alpha-key" not in app._display_model_error("alpha-key beta-key")
            assert "beta-key" not in app._display_model_error("alpha-key beta-key")
        finally:
            await app._runtime.aclose()

    asyncio.run(scenario())


def test_pending_old_provider_recovers_after_future_default_changed(host):  # noqa: F811
    from corki.core.graph import GraphRunContext
    from corki.core.runtime import _initial_state
    from corki.protocol.ids import new_turn_id
    from corki.protocol.items import UserMessageItem
    from corki.sessions import TurnRecord, TurnStatus

    path, requests = host
    configure(path)

    async def scenario():
        runtime = (await build_application_async())._runtime

        class Sink:
            async def emit(self, event):
                pass

        try:
            await runtime._ensure_ready()
            turn = new_turn_id()
            user = UserMessageItem("recover on alpha", turn)
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
            await runtime.update_thread_settings(provider="beta", model="shared")
        finally:
            await runtime.aclose()
        cold = (await build_application_async(resume=str(runtime.thread_id)))._runtime
        try:
            assert cold.thread_settings.provider == "beta"
            assert isinstance([e async for e in cold.resume_pending()][-1], TurnCompleted)
            assert requests[-1][2] == "Bearer alpha-key"
            assert isinstance([e async for e in cold.stream("future beta")][-1], TurnCompleted)
            assert requests[-1][2] == "Bearer beta-key"
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_switch_updates_media_projection_and_image_tool_without_mutating_old_view(host):  # noqa: F811
    from test_image_submission import attachment

    from corki.protocol.tools import ToolCall
    from corki.tools.base import ToolContext

    path, requests = host
    configure(path)
    document = loads(path.read_text())
    document["providers"]["alpha"]["supports_image_detail_original"] = True
    document["providers"]["beta"]["supports_image_input"] = False
    path.write_text(json.dumps(document))

    async def scenario():
        runtime = (await build_application_async())._runtime
        try:
            assert isinstance(
                [e async for e in runtime.stream("first", attachments=(attachment(),))][-1],
                TurnCompleted,
            )
            old = runtime._graph
            assert '"input_image"' in json.dumps(requests[-1][1])
            await runtime.update_thread_settings(provider="beta", model="shared")
            assert isinstance(
                [e async for e in runtime.stream("second", attachments=(attachment(),))][-1],
                TurnCompleted,
            )
            assert '"image_url"' not in json.dumps(requests[-1][1])
            graph = runtime._graph
            assert old._media._images.policy.supports_images
            assert not graph._media._images.policy.supports_images
            assert graph._window_manager._media is graph._media
            plan = graph._tool_router.build(runtime._registry.snapshot(), graph._settings)
            tool = plan.get("view_image")
            assert "detail" not in tool.spec.parameters["properties"]
            with pytest.raises(ValueError, match="not allowed"):
                await tool.execute(
                    ToolCall("image", "view_image", {"path": "absent.png"}),
                    ToolContext(path.parent),
                )
            assert old._registry.snapshot().get("view_image")._supports_images
            await runtime.update_thread_settings(provider="alpha", model="shared")
            assert isinstance([e async for e in runtime.stream("third")][-1], TurnCompleted)
            graph = runtime._graph
            plan = graph._tool_router.build(runtime._registry.snapshot(), graph._settings)
            assert "detail" in plan.get("view_image").spec.parameters["properties"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_plan_return_restores_the_saved_provider_model_pair(host):  # noqa: F811
    from types import SimpleNamespace

    from corki.protocol.collaboration import CollaborationMode

    path, _ = host
    configure(path)

    async def scenario():
        app = await build_application_async()
        app._ui = SimpleNamespace(show_notice=lambda text: None)
        try:
            await app._enter_plan_mode()
            await app._set_model("shared(beta)")
            assert app._runtime.thread_settings.provider == "beta"
            base = app._default_collaboration_settings
            assert base.provider == "alpha"
            await app._set_collaboration_mode(
                CollaborationMode("default", base.model, base.reasoning_effort),
                provider=base.provider,
            )
            assert app._runtime.thread_settings.provider == "alpha"
            assert app._runtime.thread_settings.collaboration_mode == "default"
        finally:
            await app._runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("legacy_marker", [False, True])
def test_provider_change_with_distinct_models_does_not_fallback_or_misroute_compaction(
    host,  # noqa: F811
    caplog,
    legacy_marker,
):
    from corki.context.model_transition import model_snapshot
    from corki.protocol.ids import new_turn_id

    path, requests = host
    configure(path)
    document = loads(path.read_text())
    document["agent"] = {"auto_compact_tokens": 50}
    document["default"]["model"] = "gpt-6-astra"
    a, b = document["providers"]["alpha"], document["providers"]["beta"]
    a["models"]["gpt-6-astra"] = a["models"].pop("shared")
    b["models"]["deepseek-v4-pro"] = b["models"].pop("shared")
    path.write_text(json.dumps(document))

    async def scenario():
        runtime = (await build_application_async())._runtime
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            if legacy_marker:
                info = runtime._graph._settings.model_context_info("gpt-6-astra")
                await runtime._repository.append_items(
                    runtime.thread_id, (model_snapshot(info, new_turn_id()),)
                )
            await runtime.update_thread_settings(provider="beta", model="deepseek-v4-pro")
            before = len(requests)
            events = [e async for e in runtime.stream("second")]
            assert isinstance(events[-1], TurnCompleted)
            assert any(isinstance(e, ContextCompacted) for e in events)
            assert len(requests) > before + 1
            for url, body, auth in requests[before:]:
                assert url == "https://beta.invalid/v1/chat/completions"
                assert body["model"] == "deepseek-v4-pro"
                assert auth == "Bearer beta-key"
            assert not any(
                "Unknown model gpt-6-astra" in record.message for record in caplog.records
            )
            assert runtime._graph._settings.main_context_limits.raw_tokens == 20000
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
