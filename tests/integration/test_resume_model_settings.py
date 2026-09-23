"""Cold defaults are resolved before the CLI composes the actual harness clients."""

import asyncio
import importlib
import json
import sqlite3
import threading

import httpx
import pytest

from corki import http_client
from corki.cli.main import build_application_async, build_parser
from corki.context.history import active_history
from corki.protocol.collaboration import CollaborationMode
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.items import UserMessageItem
from corki.protocol.session_source import SessionSource
from corki.storage import SQLiteSessionRepository


def config(path, *, current="alpha", model="large", effort="high", wire="responses"):
    def provider(name, mode, selected_effort):
        fields = [
            f'base_url = "https://{name}.invalid/v1"',
            f'api_key = "secret-{name}-rotated"',
            f'api_mode = "{mode}"',
            'reasoning_summary = "concise"',
            "max_retries = 0",
        ]
        if selected_effort is not None:
            fields.append(f'reasoning_effort = "{selected_effort}"')
        return "\n".join(fields)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'[agent]\nmodel = "{model}"\ninclude_environment_context = false\n'
        "[skills]\nenabled = false\n"
        f'[provider]\nname = "{current}"\n'
        + provider(current, wire if current == "alpha" else "chat_completions", effort)
        + "\n[providers.alpha]\n"
        + provider("alpha", wire, effort if current == "alpha" else "low")
        + "\n[providers.beta]\n"
        + provider("beta", "chat_completions", "low")
        + "\n[models.catalog.large]\ncontext_window = 200000\n"
        'supported_reasoning_levels = [{ effort = "low" }, { effort = "high" }]\n'
        "[models.catalog.small]\ncontext_window = 20000\n"
        'supported_reasoning_levels = [{ effort = "low" }, { effort = "high" }]\n',
        encoding="utf-8",
    )


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CORKI_HOME", str(tmp_path / "home"))
    for name in ("CORKI_MODEL", "CORKI_API_BASE", "CORKI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        importlib.import_module("corki.cli.main"), "TerminalUI", lambda settings, path: settings
    )
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append((str(request.url), body, request.headers.get("authorization")))
        if request.url.path.endswith("/chat/completions"):
            packet = {
                "choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            }
        else:
            assert request.url.path.endswith("/responses"), request.url
            packet = {
                "type": "response.completed",
                "response": {
                    "id": f"fixture-{len(requests)}",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
                },
            }
        return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

    client = httpx.AsyncClient
    monkeypatch.setattr(
        http_client,
        "OwnedHTTPClient",
        lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
    )
    return tmp_path / "home" / "config.toml", requests


@pytest.mark.parametrize("wire", ["responses", "chat_completions"])
def test_cold_cli_resume_starts_default_with_resumed_model_and_effort(host, wire):
    path, requests = host

    async def scenario():
        config(path, effort="low", wire=wire)
        first = (await build_application_async())._runtime
        try:
            await first.update_thread_settings(
                collaboration_mode=CollaborationMode("plan", "large", "high", "Saved Plan rule")
            )
            assert isinstance([e async for e in first.stream("FIRST")][-1], TurnCompleted)
            history = await first._repository.load_items(first.thread_id)
        finally:
            await first.aclose()
        config(path, current="beta", model="small", effort="low", wire=wire)
        resumed = (await build_application_async(resume=str(first.thread_id)))._runtime
        try:
            selected = resumed.thread_settings
            assert selected.collaboration_mode == "default"
            assert selected.collaboration_instructions is None
            assert (selected.model, selected.provider, selected.reasoning_effort) == (
                "large",
                "alpha",
                "high",
            )
            assert isinstance([e async for e in resumed.stream("SECOND")][-1], TurnCompleted)
            restored_history = await resumed._repository.load_items(resumed.thread_id)
            assert all(i in restored_history for i in history)
            assert len(requests) == 2
            assert all(url.startswith("https://alpha.invalid/") for url, _, _ in requests)
        finally:
            await resumed.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("personality", [None, "none", "friendly", "pragmatic"])
def test_implicit_resume_preserves_personality_over_changed_host_default(host, personality):
    path, requests = host

    async def scenario():
        config(path)
        first = (await build_application_async())._runtime
        try:
            await first.update_thread_settings(personality=personality)
            assert isinstance([e async for e in first.stream("first")][-1], TurnCompleted)
            saved = first._repository.read_thread_model_settings(first.thread_id)
            assert saved.personality == personality
            prefix = await first._repository.load_items(first.thread_id)
        finally:
            await first.aclose()
        config(path, current="beta", model="small", effort="low")
        host_personality = "pragmatic" if personality == "friendly" else "friendly"
        path.write_text(
            path.read_text().replace(
                "[agent]\n", f'[agent]\npersonality = "{host_personality}"\n', 1
            ),
            encoding="utf-8",
        )
        cold = (await build_application_async(resume=str(first.thread_id)))._runtime
        try:
            assert cold.thread_settings.personality == personality
            assert isinstance([e async for e in cold.stream("second")][-1], TurnCompleted)
            assert cold._repository.read_thread_model_settings(cold.thread_id) == saved
            history = await cold._repository.load_items(cold.thread_id)
            assert history[: len(prefix)] == prefix
            assert len(requests) == 2
            assert requests[-1][0] == "https://alpha.invalid/v1/responses"
            assert requests[-1][1]["model"] == "large"
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("wire", ["responses", "chat_completions"])
@pytest.mark.parametrize("effort", ["high", None])
def test_implicit_resume_restores_group_before_transport_window_and_compaction(host, wire, effort):
    path, requests = host

    async def scenario():
        config(path, effort=effort, wire=wire)
        path.write_text(
            path.read_text().replace("rotated", "before").replace("concise", "detailed")
        )
        first = (await build_application_async())._runtime
        try:
            assert isinstance([e async for e in first.stream("ORIGINAL_INPUT")][-1], TurnCompleted)
            original = await first._repository.load_items(first.thread_id)
            assert await first._repository.load_thread_source(
                first.thread_id
            ) == SessionSource.from_startup_arg("cli")
        finally:
            await first.aclose()
        config(path, current="beta", model="small", effort="low", wire=wire)
        app = await build_application_async(resume=str(first.thread_id))
        cold = app._runtime
        try:
            assert (
                cold._settings.model,
                cold._settings.provider_name,
                cold._settings.reasoning_effort,
            ) == (
                "large",
                "alpha",
                effort,
            )
            assert app._ui == cold._settings
            assert cold._settings.main_context_limits.raw_tokens == 200000
            assert cold._graph._window_manager._model_name == "large"
            assert isinstance([e async for e in cold.stream("RESUMED_INPUT")][-1], TurnCompleted)
            events = [e async for e in cold.compact()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert any(isinstance(e, ContextCompacted) for e in events)
            history = await cold._repository.load_items(cold.thread_id)
            assert await cold._repository.load_thread_source(
                cold.thread_id
            ) == SessionSource.from_startup_arg("cli")
            assert all(item in history for item in original)
            assert [
                i.content for i in active_history(history) if isinstance(i, UserMessageItem)
            ] == [
                "ORIGINAL_INPUT",
                "RESUMED_INPUT",
            ]
            saved = cold._repository.read_thread_model_settings(cold.thread_id)
            assert (saved.model, saved.provider, saved.reasoning_effort) == (
                "large",
                "alpha",
                effort,
            )
            with sqlite3.connect(cold._repository.path) as db:
                record = db.execute("SELECT * FROM thread_model_settings").fetchone()
            assert not any("secret" in str(value) or ".invalid" in str(value) for value in record)
            for index, (url, body, auth) in enumerate(requests):
                assert url.startswith("https://alpha.invalid/")
                assert body["model"] == "large"
                assert auth == f"Bearer secret-alpha-{'rotated' if index else 'before'}"
                if wire == "responses":
                    assert body.get("reasoning", {}).get("effort") == effort
                    assert body["reasoning"]["summary"] == ("concise" if index else "detailed")
                else:
                    assert "reasoning_effort" not in body  # generic chat capability gate
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("override", ["model", "provider", "effort", "env_model", "env_base"])
def test_one_explicit_override_skips_entire_persisted_group(host, monkeypatch, override):
    path, requests = host

    async def scenario():
        config(path)
        first = (await build_application_async())._runtime
        try:
            assert isinstance([e async for e in first.stream("first")][-1], TurnCompleted)
        finally:
            await first.aclose()
        config(path, current="beta", model="small", effort="low")
        kwargs = {}
        expected = ("small", "beta", "low")
        if override == "model":
            kwargs["model"] = "large"
            expected = ("large", "beta", "low")
        elif override == "provider":
            kwargs["provider"] = "alpha"
            expected = ("small", "alpha", "low")
        elif override == "effort":
            kwargs["reasoning_effort"] = "high"
            expected = ("small", "beta", "high")
        elif override == "env_model":
            monkeypatch.setenv("CORKI_MODEL", "large")
            expected = ("large", "beta", "low")
        else:
            monkeypatch.setenv("CORKI_API_BASE", "https://explicit.invalid/v1")
        cold = (await build_application_async(resume=str(first.thread_id), **kwargs))._runtime
        try:
            assert (
                cold._settings.model,
                cold._settings.provider_name,
                cold._settings.reasoning_effort,
            ) == expected
            assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
            assert requests[-1][1]["model"] == expected[0]
            assert requests[-1][0].startswith(
                "https://explicit.invalid/"
                if override == "env_base"
                else f"https://{expected[1]}.invalid/"
            )
            saved = cold._repository.read_thread_model_settings(cold.thread_id)
            assert (saved.model, saved.provider, saved.reasoning_effort) == expected
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_missing_saved_provider_fails_before_client_construction(host, monkeypatch):
    path, requests = host

    async def scenario():
        config(path)
        first = (await build_application_async())._runtime
        try:
            assert isinstance([e async for e in first.stream("first")][-1], TurnCompleted)
        finally:
            await first.aclose()
        path.write_text(
            '[agent]\nmodel="small"\n[provider]\nname="beta"\n[skills]\nenabled=false\n'
        )
        monkeypatch.setattr(
            "corki.core.runtime._create_model", lambda *a: pytest.fail("created client")
        )
        with pytest.raises(ValueError, match="alpha"):
            (await build_application_async(resume=str(first.thread_id)))
        assert len(requests) == 1

    asyncio.run(scenario())


def test_legacy_database_without_model_metadata_uses_current_defaults(host):
    path, requests = host

    async def scenario():
        config(path, current="beta", model="small", effort="low")
        repository = SQLiteSessionRepository(path.parent / "sessions" / "corki.db")
        await repository.create_thread("legacy-thread", path.parent.parent)
        # An actual pre-migration schema: reopening must create the new table,
        # not invent a past model selection for an existing thread.
        with sqlite3.connect(repository.path) as db:
            db.execute("DROP TABLE thread_model_settings")
        cold = (await build_application_async(resume="legacy-thread"))._runtime
        try:
            assert isinstance([e async for e in cold.stream("legacy")][-1], TurnCompleted)
            assert requests[-1][1]["model"] == "small"
            saved = cold._repository.read_thread_model_settings(cold.thread_id)
            assert (saved.model, saved.provider, saved.reasoning_effort) == ("small", "beta", "low")
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_parser_accepts_explicit_resume_model_settings():
    parsed = build_parser().parse_args(
        [
            "resume",
            "thread",
            "--model",
            "large",
            "--provider",
            "alpha",
            "--reasoning-effort",
            "high",
        ]
    )
    assert (parsed.model, parsed.provider, parsed.reasoning_effort) == ("large", "alpha", "high")


def test_saved_profile_identity_preserves_label_without_enabling_private_capabilities(host):
    path, requests = host

    async def scenario():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '[agent]\nmodel="large"\n[skills]\nenabled=false\n'
            '[provider]\nid="deployment"\n'
            '[providers.deployment]\nname="deepseek"\n'
            'api_key="fixture-key"\n'
            'base_url="https://deployment.invalid/v1"\nreasoning_effort="high"\n'
        )
        first = (await build_application_async())._runtime
        try:
            assert isinstance([e async for e in first.stream("first")][-1], TurnCompleted)
            assert "reasoning_effort" not in requests[-1][1]
            assert first.thread_settings.reasoning_effort == "high"
            assert requests[-1][0] == "https://deployment.invalid/v1/chat/completions"
            assert requests[-1][2] == "Bearer fixture-key"
        finally:
            await first.aclose()
        path.write_text(
            path.read_text().replace('id="deployment"', 'name="beta"').replace('"high"', '"low"')
        )
        cold = (await build_application_async(resume=str(first.thread_id)))._runtime
        try:
            assert isinstance([e async for e in cold.stream("second")][-1], TurnCompleted)
            assert "reasoning_effort" not in requests[-1][1]
            assert cold.thread_settings.reasoning_effort == "high"
            assert requests[-1][0] == "https://deployment.invalid/v1/chat/completions"
            assert requests[-1][2] == "Bearer fixture-key"
            saved = cold._repository.read_thread_model_settings(cold.thread_id)
            assert saved.provider == "deployment"
            assert saved.reasoning_effort == "high"
            assert cold._settings.provider_name == "deepseek"
            assert cold._settings.provider_id == "deployment"
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_settings_write_failure_leaves_no_admitted_turn_and_retry_uses_same_defaults(
    host, monkeypatch
):
    path, requests = host

    async def scenario():
        config(path)
        runtime = (await build_application_async())._runtime
        write = runtime._repository._save_thread_model_settings

        def fail(*args):
            raise OSError("fixture settings commit failed")

        monkeypatch.setattr(runtime._repository, "_save_thread_model_settings", fail)
        try:
            with pytest.raises(OSError, match="fixture settings commit"):
                _ = [e async for e in runtime.stream("not admitted")]
            assert not requests
            assert runtime._compiled is None and runtime._checkpoint_context is None
            assert runtime._repository.read_thread_model_settings(runtime.thread_id) is None
            assert not await runtime._repository.load_items(runtime.thread_id)
            with sqlite3.connect(runtime._repository.path) as db:
                assert db.execute("SELECT count(*) FROM turns").fetchone()[0] == 0
            monkeypatch.setattr(runtime._repository, "_save_thread_model_settings", write)
            assert isinstance([e async for e in runtime.stream("retry")][-1], TurnCompleted)
            assert len(requests) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_key_rotation_alone_preserves_saved_model_provider_and_effort(host, monkeypatch):
    path, requests = host

    async def scenario():
        config(path)
        first = (await build_application_async())._runtime
        try:
            assert isinstance([e async for e in first.stream("first")][-1], TurnCompleted)
        finally:
            await first.aclose()
        config(path, current="beta", model="small", effort="low")
        monkeypatch.setenv("CORKI_API_KEY", "new-env-credential")
        cold = (await build_application_async(resume=str(first.thread_id)))._runtime
        try:
            assert isinstance([e async for e in cold.stream("second")][-1], TurnCompleted)
            assert requests[-1][0] == "https://alpha.invalid/v1/responses"
            assert requests[-1][1]["model"] == "large"
            assert requests[-1][1]["reasoning"]["effort"] == "high"
            assert requests[-1][2] == "Bearer new-env-credential"
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_failed_explicit_resume_cannot_overwrite_previous_group(host, monkeypatch):
    path, requests = host

    async def scenario():
        config(path)
        first = (await build_application_async())._runtime
        try:
            assert isinstance([e async for e in first.stream("first")][-1], TurnCompleted)
            original = await first._repository.load_items(first.thread_id)
            saved = first._repository.read_thread_model_settings(first.thread_id)
        finally:
            await first.aclose()
        config(path, current="beta", model="small", effort="low")
        cold = (await build_application_async(resume=str(first.thread_id), model="small"))._runtime

        def fail(*args):
            raise OSError("fixture settings commit failed")

        monkeypatch.setattr(cold._repository, "_save_thread_model_settings", fail)
        try:
            with pytest.raises(OSError, match="fixture settings commit"):
                _ = [e async for e in cold.stream("not admitted")]
            assert cold._repository.read_thread_model_settings(cold.thread_id) == saved
            assert await cold._repository.load_items(cold.thread_id) == original
            assert len(requests) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "close"])
def test_startup_settings_write_is_joined_before_cancel_or_close_returns(host, monkeypatch, action):
    path, requests = host

    async def scenario():
        config(path)
        runtime = (await build_application_async())._runtime
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        write = runtime._repository._save_thread_model_settings

        def held(*args):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)
            write(*args)

        monkeypatch.setattr(runtime._repository, "_save_thread_model_settings", held)

        async def consume():
            return [e async for e in runtime.stream("not admitted")]

        consumer = asyncio.create_task(consume())
        closer = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            startup = runtime._startup_task
            assert startup is not None
            if action == "cancel":
                consumer.cancel()
            else:
                closer = asyncio.create_task(runtime.aclose())
            async with asyncio.timeout(3):
                while not startup.cancelling():
                    await asyncio.sleep(0)
            assert not consumer.done()
            assert closer is None or not closer.done()
            assert not requests
            release.set()
            outcomes = await asyncio.gather(consumer, return_exceptions=True)
            assert isinstance(outcomes[0], asyncio.CancelledError)
            if closer is not None:
                await closer
            assert startup.done()
            assert not requests and runtime._compiled is None
            saved = runtime._repository.read_thread_model_settings(runtime.thread_id)
            assert (saved.model, saved.provider, saved.reasoning_effort) == (
                "large",
                "alpha",
                "high",
            )
            assert not await runtime._repository.load_items(runtime.thread_id)
        finally:
            release.set()
            await asyncio.gather(consumer, return_exceptions=True)
            if closer is not None:
                await closer
            await runtime.aclose()

    asyncio.run(scenario())
