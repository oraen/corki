"""Late constructor faults do not start lazy services or retain volatile storage."""

import asyncio
import json
import sqlite3

import pytest

from corki.config import CorkiSettings
from corki.config.hooks import ManagedHookPolicy
from corki.config.token_budget import TokenBudgetConfig
from corki.core import LangGraphRuntime
from corki.core import runtime as runtime_module
from corki.tools import ToolRegistry


@pytest.mark.parametrize("failure", [ValueError, asyncio.CancelledError])
@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("close_failure", [None, OSError, asyncio.CancelledError])
def test_sync_constructor_outside_loop_waits_for_owned_model_cleanup(
    tmp_path, monkeypatch, failure, borrowed, close_failure
):
    closed = []
    registry = ToolRegistry()
    error = failure("late synchronous construction failure")

    class Model:
        async def aclose(self):
            await asyncio.sleep(0)
            closed.append("model")
            if close_failure is not None:
                raise close_failure("injected close failure")

    model = Model()

    def fail_graph(**kwargs):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        raise error

    monkeypatch.setattr(runtime_module, "CorkiGraph", fail_graph)
    monkeypatch.setattr(runtime_module, "_create_model", lambda *args: model)
    terminate = runtime_module.ProcessManager.terminate_all

    async def terminate_all(manager):
        await terminate(manager)
        closed.append("processes")

    monkeypatch.setattr(runtime_module.ProcessManager, "terminate_all", terminate_all)
    cleanup_cancelled = not borrowed and close_failure is asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError if cleanup_cancelled else failure) as caught:
        LangGraphRuntime.create(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
            registry=registry,
            model=model if borrowed else None,
        )
    if cleanup_cancelled and failure is not asyncio.CancelledError:
        assert caught.value.__cause__ is error
    else:
        assert caught.value is error
    if not borrowed and close_failure is not None:
        assert error.__notes__ == [f"Construction cleanup failed: {close_failure.__name__}"]
    assert registry.specs() == ()
    assert closed == (["processes"] if borrowed else ["model", "processes"])


def test_sync_success_keeps_resources_until_real_turn_and_close(tmp_path, monkeypatch):
    from corki.models import ModelCompleted
    from corki.protocol.events import TurnCompleted
    from corki.protocol.items import AssistantMessageItem, new_step_id

    closed = []
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            yield ModelCompleted(
                (AssistantMessageItem("answer", request.items[-1].turn_id, new_step_id()),)
            )

        async def aclose(self):
            closed.append("model")

    def create_model(*args):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return Model()

    monkeypatch.setattr(runtime_module, "_create_model", create_model)
    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
        database_path=tmp_path / "state.db",
        home_path=tmp_path,
    )
    assert closed == []

    async def scenario():
        try:
            events = [event async for event in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == 1
            assert closed == []
        finally:
            await runtime.aclose()
        await runtime.aclose()

    asyncio.run(scenario())
    assert closed == ["model"]


@pytest.mark.parametrize("ephemeral", [False, True])
@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("handler", [{"type": "mcp_tool"}, {"type": "command"}])
def test_required_hook_failure_rolls_back_real_graph(
    tmp_path, monkeypatch, ephemeral, borrowed, handler
):
    async def scenario():
        baseline = asyncio.all_tasks()
        closed, captured = [], {}
        registry = ToolRegistry()
        graph_type = runtime_module.CorkiGraph

        class Model:
            async def aclose(self):
                await asyncio.sleep(0)
                closed.append("model")

        model = Model()

        def graph(**kwargs):
            captured.update(kwargs)
            return graph_type(**kwargs)

        terminate = runtime_module.ProcessManager.terminate_all

        async def terminate_all(manager):
            await terminate(manager)
            closed.append("processes")

        monkeypatch.setattr(runtime_module, "CorkiGraph", graph)
        monkeypatch.setattr(runtime_module, "_create_model", lambda *args: model)
        monkeypatch.setattr(runtime_module.ProcessManager, "terminate_all", terminate_all)
        with pytest.raises(ValueError, match="failed to load required managed hooks"):
            await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    managed_hook_policy=ManagedHookPolicy(
                        source=tmp_path / "requirements.toml",
                        hooks_json=json.dumps({"SessionEnd": [{"hooks": [handler]}]}),
                    ),
                ),
                model=model if borrowed else None,
                registry=registry,
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                ephemeral=ephemeral,
            )
        assert captured
        assert registry.specs() == ()
        assert closed == (["processes"] if borrowed else ["model", "processes"])
        assert asyncio.all_tasks() == baseline
        assert not (tmp_path / "checkpoints.db").exists()
        assert not (tmp_path / "state.db.thread-writer-locks").exists()
        if ephemeral:
            assert not (tmp_path / "state.db").exists()
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                captured["repository"]._connection.execute("SELECT 1")

    asyncio.run(scenario())


@pytest.mark.parametrize("ephemeral", [False, True])
@pytest.mark.parametrize("failure", [ValueError, asyncio.CancelledError])
def test_late_runtime_constructor_fault_keeps_internal_services_lazy(
    tmp_path, monkeypatch, ephemeral, failure
):
    async def scenario():
        baseline = asyncio.all_tasks()
        closed, leases, captured = [], [], {}
        registry = ToolRegistry()
        database = tmp_path / "state.db"
        error = failure("late graph composition failed")
        lease_type = runtime_module.ThreadWriterLease

        def lease(*args, **kwargs):
            value = lease_type(*args, **kwargs)
            leases.append(value)
            return value

        class Model:
            async def aclose(self):
                closed.append("model")

        def graph(**kwargs):
            captured.update(kwargs)
            assert asyncio.all_tasks() == baseline
            assert kwargs["code_mode"].cells == {}
            assert not kwargs["code_mode"].active.is_set()
            assert kwargs["window_manager"]._history_notes is not None
            assert all(not value.held for value in leases)
            raise error

        monkeypatch.setattr(runtime_module, "ThreadWriterLease", lease)
        monkeypatch.setattr(runtime_module, "CorkiGraph", graph)
        monkeypatch.setattr(runtime_module, "_create_model", lambda *args: Model())
        with pytest.raises(failure) as caught:
            await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    token_budget_enabled=True,
                    token_budget=TokenBudgetConfig(use_history_notes_extension=True),
                ),
                database_path=database,
                home_path=tmp_path,
                registry=registry,
                ephemeral=ephemeral,
            )
        assert caught.value is error
        assert closed == ["model"]
        assert registry.specs() == ()
        assert asyncio.all_tasks() == baseline
        assert not (tmp_path / "state.history-notes.db").exists()
        assert not (tmp_path / "checkpoints.db").exists()
        assert not (tmp_path / "state.db.thread-writer-locks").exists()
        if ephemeral:
            assert leases == []
            assert not database.exists()
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                captured["repository"]._connection.execute("SELECT 1")
        else:
            assert len(leases) == 1 and not leases[0].held

    asyncio.run(scenario())
