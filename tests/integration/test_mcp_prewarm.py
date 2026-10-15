"""Background reconciliation publishes latest state without owning a model Step."""

import asyncio
import gc
import weakref
from dataclasses import replace

import pytest
from test_mcp_pending_reuse import PendingClient

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPRegistration
from corki.mcp.manager import MCPManager
from corki.mcp.prewarm import MCPPrewarm
from corki.mcp.runtime_environment import MCPRuntimeContext
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


class Model:
    async def stream(self, request):
        yield ModelCompleted(())

    async def aclose(self):
        pass


def test_runtime_prewarm_starts_without_step_and_replaces_pending_required(tmp_path, monkeypatch):
    async def scenario():
        clients = []
        created = asyncio.Queue()

        def factory(settings):
            client = PendingClient(settings, "initialize")
            clients.append(client)
            created.put_nowait(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=None,
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "prewarm.db",
        )
        waiting = None
        try:
            await runtime._ensure_ready()
            server = MCPServerSettings("warm", "http", url="https://one.test", required=True)
            runtime.request_mcp_reconcile((server,))
            first = await asyncio.wait_for(created.get(), 1)
            await asyncio.wait_for(first.entered.wait(), 1)
            # A Step already waiting for required readiness must not hold the
            # publication lock and prevent a later host configuration update.
            waiting = asyncio.create_task(runtime._refresh_tools())
            await asyncio.sleep(0)
            runtime.request_mcp_reconcile((replace(server, url="https://two.test"),))
            second = await asyncio.wait_for(created.get(), 1)
            await asyncio.wait_for(first.cancelled.wait(), 1)
            assert not waiting.done()
            second.release.set()
            await asyncio.wait_for(waiting, 1)
            assert first.closes == 1
            assert runtime._mcp_manager._clients_by_name["warm"].client is second
        finally:
            if waiting is not None:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            await runtime.aclose()
        assert all(client.closes == 1 for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["catalog", "context", "force"])
def test_host_entrypoints_schedule_background_publication(tmp_path, monkeypatch, entrypoint):
    async def scenario():
        clients = []
        published = asyncio.Event()

        def factory(settings):
            client = PendingClient(settings, "initialize")
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        server = MCPServerSettings("warm", "http", url="https://warm.test")
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=(server,),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "entrypoint.db",
        )
        try:
            await runtime._ensure_ready()
            await asyncio.wait_for(clients[0].entered.wait(), 1)
            manager = runtime._mcp_manager
            previous = manager._preparation
            original = manager._publish_generation

            def publish(generation, fallbacks):
                original(generation, fallbacks)
                published.set()

            monkeypatch.setattr(manager, "_publish_generation", publish)
            if entrypoint == "catalog":
                runtime.request_mcp_catalog(MCPCatalog((MCPRegistration(server),)))
            elif entrypoint == "context":
                runtime.request_mcp_runtime_context(MCPRuntimeContext())
            else:
                runtime.request_mcp_refresh()
            await asyncio.wait_for(published.wait(), 1)
            assert manager._preparation is not previous and not manager.refresh_pending
            assert len(clients) == (2 if entrypoint == "force" else 1)
            assert clients[0].closes == (1 if entrypoint == "force" else 0)
        finally:
            await runtime.aclose()
        assert all(client.closes == 1 for client in clients)

    asyncio.run(scenario())


def test_failed_runtime_setup_does_not_publish_prewarm_worker(tmp_path, monkeypatch):
    async def scenario():
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, execution_permissions=None
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "setup.db",
        )
        try:
            runtime.request_mcp_reconcile(())

            async def fail(checkpointer):
                raise ValueError("setup failure")

            with monkeypatch.context() as patch:
                patch.setattr("corki.core.runtime.setup_checkpoint", fail)
                with pytest.raises(ValueError, match="setup failure"):
                    await runtime._ensure_ready()
            assert runtime._mcp_prewarm._task is None and runtime._compiled is None
            await runtime._ensure_ready()
            assert runtime._mcp_prewarm._task is not None
        finally:
            await runtime.aclose()
        assert runtime._mcp_prewarm._task.done()

    asyncio.run(scenario())


def test_idle_prewarm_stops_when_its_manager_owner_is_collected():
    async def scenario():
        manager = MCPManager((), ToolRegistry())
        reference = weakref.ref(manager)
        worker = MCPPrewarm(manager)
        worker.start()
        await asyncio.sleep(0)
        del manager
        gc.collect()
        try:
            assert reference() is None
            await asyncio.wait_for(asyncio.shield(worker._task), 0.1)
        finally:
            await worker.aclose()

    asyncio.run(scenario())


def test_invalidation_during_prewarm_publishes_only_latest_desired_state(tmp_path, monkeypatch):
    async def scenario():
        clients = []
        entered, release, published = asyncio.Event(), asyncio.Event(), asyncio.Event()

        def factory(settings):
            client = PendingClient(settings, "initialize")
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, execution_permissions=None
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "latest.db",
        )
        try:
            await runtime._ensure_ready()
            manager = runtime._mcp_manager
            original_wait, original_publish = manager._wait_preparation, manager._publish_generation
            publications = []

            async def wait(generation, mode, *args):
                if mode == "publish" and not entered.is_set():
                    entered.set()
                    await release.wait()
                return await original_wait(generation, mode, *args)

            def publish(generation, fallbacks):
                original_publish(generation, fallbacks)
                publications.append(generation.desired[0].url)
                published.set()

            monkeypatch.setattr(manager, "_wait_preparation", wait)
            monkeypatch.setattr(manager, "_publish_generation", publish)
            server = MCPServerSettings("warm", "http", url="https://warm.test/first")
            runtime.request_mcp_reconcile((server,))
            await asyncio.wait_for(entered.wait(), 1)
            for index in range(50):
                runtime.request_mcp_reconcile((replace(server, url=f"https://warm.test/{index}"),))
            release.set()
            await asyncio.wait_for(published.wait(), 1)
            assert publications == ["https://warm.test/49"]
            assert [client.settings.url for client in clients] == [
                "https://warm.test/first",
                "https://warm.test/49",
            ]
            assert clients[0].closes == 1 and not manager.refresh_pending
        finally:
            release.set()
            await runtime.aclose()
        assert all(client.closes == 1 for client in clients)

    asyncio.run(scenario())


def test_background_policy_update_preserves_sampling_snapshot_and_latest_call_admission(
    tmp_path, monkeypatch
):
    async def scenario():
        server = MCPServerSettings("warm", "http", url="https://warm.test", enabled_tools=("old",))
        client = PendingClient(server, "initialize")
        client.release.set()
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        requests = []
        published = asyncio.Event()

        class CallingModel(Model):
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                names = {spec.name for spec in request.tools}
                if len(requests) == 1:
                    assert "mcp__warm::old" in names and "mcp__warm::new" not in names
                    runtime.request_mcp_reconcile((replace(server, enabled_tools=("new",)),))
                    await asyncio.wait_for(published.wait(), 1)
                    assert {spec.name for spec in request.tools} == names
                    call = ToolCall(new_tool_call_id(), "mcp__warm::old", {})
                elif len(requests) == 2:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert result.is_error and client.calls == []
                    assert "mcp__warm::new" in names and "mcp__warm::old" not in names
                    call = ToolCall(new_tool_call_id(), "mcp__warm::new", {})
                else:
                    assert len(requests) == 3 and client.calls == ["new"]
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert not result.is_error and "new" in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                tool_search_mode="disabled",
                mcp_servers=(server,),
            ),
            model=CallingModel(),
            registry=ToolRegistry(),
            database_path=tmp_path / "sampling.db",
        )
        try:
            await runtime._ensure_ready()
            original = runtime._mcp_manager._publish_generation

            def publish(generation, fallbacks):
                original(generation, fallbacks)
                if generation.desired[0].enabled_tools == ("new",):
                    published.set()

            monkeypatch.setattr(runtime._mcp_manager, "_publish_generation", publish)
            events = [event async for event in runtime.stream("Use the allowed tool")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert client.starts == client.lists == 1
        finally:
            await runtime.aclose()
        assert client.closes == 1

    asyncio.run(scenario())


def test_cancelling_catalog_waiter_does_not_undo_a_published_prewarm(monkeypatch):
    async def scenario():
        server = MCPServerSettings("warm", "http", url="https://warm.test")
        clients = []

        def factory(settings):
            client = PendingClient(settings, "initialize")
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager((server,), ToolRegistry())
        waiting = None
        try:
            await manager.publish_pending()
            generation = manager._preparation
            await asyncio.wait_for(clients[0].entered.wait(), 1)
            waiting = asyncio.create_task(manager.refresh_if_dirty())
            await asyncio.sleep(0)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert manager._preparation is generation and not manager.refresh_pending
            assert clients[0].closes == 0 and not clients[0].cancelled.is_set()
            clients[0].release.set()
            await manager.prepare_server("warm")
            assert len(clients) == 1
        finally:
            if waiting is not None:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            await manager.aclose()
        assert clients[0].closes == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("initialize_first", [False, True])
def test_prewarm_coalesces_notifications_and_does_not_initialize_runtime(
    tmp_path, monkeypatch, initialize_first
):
    async def scenario():
        clients = []
        created = asyncio.Event()

        def factory(settings):
            client = PendingClient(settings, "initialize")
            clients.append(client)
            created.set()
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, execution_permissions=None
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "coalesce.db",
        )
        try:
            if initialize_first:
                await runtime._ensure_ready()
            for index in range(100):
                runtime.request_mcp_reconcile(
                    (MCPServerSettings("warm", "http", url=f"https://warm.test/{index}"),)
                )
            if not initialize_first:
                await asyncio.sleep(0)
                assert runtime._compiled is None and clients == []
                await runtime._ensure_ready()
            await asyncio.wait_for(created.wait(), 1)
            assert len(clients) == 1 and clients[0].settings.url.endswith("/99")
        finally:
            await runtime.aclose()
        assert clients[0].closes == 1
        assert runtime._mcp_prewarm._task.done()

    asyncio.run(scenario())


def test_prewarm_failure_isolated_and_exact_step_retries(tmp_path, monkeypatch, caplog):
    async def scenario():
        clients = []

        def factory(settings):
            client = PendingClient(settings, "initialize")
            client.release.set()
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, execution_permissions=None
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "failure.db",
        )
        try:
            await runtime._ensure_ready()
            manager = runtime._mcp_manager
            original = manager._publish_generation
            attempts = []
            failed = asyncio.Event()

            def publish(generation, fallbacks):
                attempts.append(generation)
                if len(attempts) == 1:
                    failed.set()
                    raise ValueError("injected publication failure")
                return original(generation, fallbacks)

            monkeypatch.setattr(manager, "_publish_generation", publish)
            runtime.request_mcp_reconcile(
                (MCPServerSettings("warm", "http", url="https://warm.test"),)
            )
            await asyncio.wait_for(failed.wait(), 1)
            # Acquire the same gate after rollback; a failed worker never spins.
            async with manager._gate:
                assert manager.refresh_pending
            await asyncio.sleep(0)
            assert len(attempts) == 1 and not runtime._mcp_prewarm._task.done()
            await runtime._refresh_tools()
            assert len(attempts) == 2 and "mcp__warm::new" in manager.tool_names
            assert "MCP background reconciliation failed" in caplog.text
        finally:
            await runtime.aclose()
        assert all(client.closes == 1 for client in clients)

    asyncio.run(scenario())


def test_close_cancels_unpublished_prewarm_before_model_shutdown(tmp_path, monkeypatch):
    async def scenario():
        clients = []
        entered = asyncio.Event()

        def factory(settings):
            client = PendingClient(settings, "initialize")
            clients.append(client)
            return client

        class ClosingModel(Model):
            async def aclose(self):
                assert runtime._mcp_prewarm._task.done()
                assert clients[0].closes == 1

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, execution_permissions=None
            ),
            model=ClosingModel(),
            registry=ToolRegistry(),
            database_path=tmp_path / "close.db",
        )
        try:
            await runtime._ensure_ready()
            manager = runtime._mcp_manager
            original = manager._wait_preparation

            async def wait(generation, mode, *args):
                if mode == "publish":
                    entered.set()
                    await asyncio.Event().wait()
                return await original(generation, mode, *args)

            monkeypatch.setattr(manager, "_wait_preparation", wait)
            runtime.request_mcp_reconcile(
                (MCPServerSettings("warm", "http", url="https://warm.test"),)
            )
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(runtime.aclose(), 1)
            assert manager._preparation is None and manager.refresh_pending
            assert not any(
                task.get_name().startswith(("corki-mcp-", "mcp-"))
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
