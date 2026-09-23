"""Plugin generations publish atomically and retain admitted Python handlers."""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.plugins import PluginManager
from corki.plugins.manager import discover_manifests
from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall, ToolSpec
from corki.tools import ToolContext, ToolRegistry, ToolSource


def package(root, *, body="one", fail=False):
    manifest = root / ".codex-plugin/plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"name": root.name, "entrypoint": "plugin.py:register"}))
    code = root / "plugin.py"
    code.write_text(
        "from pathlib import Path\n"
        "def register(api):\n"
        "    with Path(__file__).with_name('registrations').open('a') as f: "
        "f.write('register\\n')\n"
        "    api.register_tool(name='echo', description='echo', parameters={'type':'object'}, "
        f"handler=lambda a,c:'{body}')\n"
        + ("    raise ValueError('fixture init failed')\n" if fail else "")
        + "async def aclose():\n"
        "    with Path(__file__).with_name('closures').open('a') as f: f.write('close\\n')\n"
    )
    return code


def test_invalid_memory_storage_is_rejected_before_executing_plugins(tmp_path):
    home = tmp_path / "home"
    root = home / "plugins/fixture"
    package(root)
    registry = ToolRegistry()

    # This host adapter is intentionally not SQLite; no adapter operation is
    # needed to establish that an explicit memory repository is required.
    class Repository:
        pass

    with pytest.raises(ValueError, match="requires an explicit memory_repository"):
        LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, memories_enabled=True
            ),
            database_path=tmp_path / "runtime.db",
            home_path=home,
            repository=Repository(),
            registry=registry,
        )
    assert not (root / "registrations").exists()
    assert registry.get("plugin__fixture__echo") is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_failed_construction_restores_host_registry_before_resource_cleanup(
    tmp_path, monkeypatch, asynchronous
):
    async def scenario():
        home = tmp_path / "home"
        package(home / "plugins/fixture")
        registry = ToolRegistry()
        owner = registry.create_owner(source=ToolSource.DYNAMIC)
        tool = SimpleNamespace(spec=ToolSpec("host", "host tool", {"type": "object"}))
        registry.replace_owned(owner, (tool,))
        before = registry.snapshot()
        entered, release = asyncio.Event(), asyncio.Event()
        close = PluginManager._close_modules

        async def held(manager):
            entered.set()
            await release.wait()
            await close(manager)

        monkeypatch.setattr(PluginManager, "_close_modules", held)

        class Model:
            async def aclose(self):
                pytest.fail("borrowed model must not close")

        class BrokenRuntime(LangGraphRuntime):
            def __init__(self, **kwargs):
                assert registry.get("plugin__fixture__echo") is not None
                raise ValueError("late construction failed")

        kwargs = dict(
            settings=CorkiSettings(tmp_path, skills_enabled=False),
            database_path=tmp_path / "runtime.db",
            home_path=home,
            registry=registry,
            model=Model(),
        )
        if not asynchronous:
            closers = []
            try:
                with pytest.raises(ValueError, match="late construction"):
                    BrokenRuntime.create(_construction=closers, **kwargs)
                assert registry.snapshot().specs() == before.specs()
                assert registry.get("host") is tool
            finally:
                # The legacy synchronous API still requires a separate resource
                # owner. Keep this regression fixture from leaking its modules.
                release.set()
                for cleanup in reversed(closers):
                    await cleanup()
            return

        pending = asyncio.create_task(BrokenRuntime.acreate(**kwargs))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert registry.snapshot().specs() == before.specs()
            assert registry.get("host") is tool
            later = SimpleNamespace(spec=ToolSpec("later", "later tool", {"type": "object"}))
            registry.append_external(owner, later)
            release.set()
            with pytest.raises(ValueError, match="late construction"):
                await pending
            assert registry.get("later") is later
            assert before.get("later") is None
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())


def test_async_runtime_construction_joins_failed_plugin_cleanup(tmp_path, monkeypatch):
    async def scenario():
        home = tmp_path / "home"
        root = home / "plugins/fixture"
        code = package(root)
        code.write_text(
            "import asyncio\n"
            + code.read_text().replace(
                "async def aclose():", "    raise asyncio.CancelledError\nasync def aclose():"
            )
        )
        entered, release = asyncio.Event(), asyncio.Event()
        close = PluginManager._close_modules

        async def held(manager):
            entered.set()
            await release.wait()
            await close(manager)

        monkeypatch.setattr(PluginManager, "_close_modules", held)

        class BorrowedModel:
            async def aclose(self):
                pytest.fail("failed construction cannot close the host model")

        pending = asyncio.create_task(
            LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False),
                database_path=tmp_path / "runtime.db",
                home_path=home,
                model=BorrowedModel(),
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            for _ in range(2):
                pending.cancel()
                await asyncio.sleep(0)
                assert not pending.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert (root / "closures").read_text().splitlines() == ["close"]
            assert not any(
                t.get_name() == "corki-construction-rollback" for t in asyncio.all_tasks()
            )
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("close_fails", [None, "error", "cancel"])
def test_async_late_constructor_failure_closes_only_owned_models(
    tmp_path, monkeypatch, borrowed, close_fails
):
    async def scenario():
        from corki.core import runtime as runtime_module

        closed = []
        root = tmp_path / "plugins/fixture"
        package(root)

        class Model:
            async def aclose(self):
                closed.append("model")
                if close_fails == "cancel":
                    raise asyncio.CancelledError
                if close_fails == "error":
                    raise OSError("model close failed")

        model = Model()
        monkeypatch.setattr(runtime_module, "_create_model", lambda *args: model)
        error = ValueError("late constructor failure")

        class BrokenRuntime(LangGraphRuntime):
            def __init__(self, **kwargs):
                raise error

        cleanup_cancelled = close_fails == "cancel" and not borrowed
        with pytest.raises(asyncio.CancelledError if cleanup_cancelled else ValueError) as failure:
            await BrokenRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False),
                database_path=tmp_path / "runtime.db",
                home_path=tmp_path,
                model=model if borrowed else None,
            )
        assert (failure.value.__cause__ if cleanup_cancelled else failure.value) is error
        assert closed == ([] if borrowed else ["model"])
        assert (root / "closures").read_text().splitlines() == ["close"]
        assert getattr(error, "__notes__", []) == (
            [f"Construction cleanup failed: {'CancelledError' if cleanup_cancelled else 'OSError'}"]
            if close_fails and not borrowed
            else []
        )

    asyncio.run(scenario())


def test_reload_reuses_unchanged_code_and_keeps_old_step_handler(tmp_path):
    async def scenario():
        root = tmp_path / "fixture"
        code = package(root)
        registry = ToolRegistry()
        manager = PluginManager.discover_and_load(
            roots=(tmp_path,), disabled=frozenset(), registry=registry
        )
        registry.seal()
        old = registry.snapshot()
        name = "plugin__fixture__echo"

        async def read(snapshot):
            return (
                await snapshot.get(name).execute(
                    ToolCall(ToolCallId("x"), name, {}), ToolContext(cwd=tmp_path)
                )
            ).content

        try:
            plan = manager.prepare_reload(*discover_manifests((tmp_path,), frozenset({"fixture"})))
            assert registry.get(name) is not None
            plan.publish()
            assert registry.get(name) is None
            assert await read(old) == "one"
            manager.prepare_reload(*discover_manifests((tmp_path,), frozenset())).publish()
            assert registry.get(name) is old.get(name)
            assert (root / "registrations").read_text().splitlines() == ["register"]
            timestamp = code.stat()
            package(root, body="two")
            os.utime(code, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
            candidate = manager.prepare_reload(*discover_manifests((tmp_path,), frozenset()))
            assert await read(registry.snapshot()) == "one"
            candidate.publish()
            assert await read(registry.snapshot()) == "two"
            assert await read(old) == "one"
            assert len((root / "registrations").read_text().splitlines()) == 2
        finally:
            await manager.aclose()
            await manager.aclose()
        assert len((root / "closures").read_text().splitlines()) == 2

    asyncio.run(scenario())


def test_abort_closes_only_unpublished_modules_and_never_changes_registry(tmp_path):
    async def scenario():
        first = tmp_path / "first"
        package(first)
        registry = ToolRegistry()
        manager = PluginManager.discover_and_load(
            roots=(tmp_path,), disabled=frozenset(), registry=registry
        )
        registry.seal()
        previous = registry.snapshot()
        second = tmp_path / "second"
        package(second)
        plan = manager.prepare_reload(*discover_manifests((tmp_path,), frozenset()))
        assert registry.snapshot() is previous
        await plan.abort()
        await plan.abort()
        with pytest.raises(RuntimeError, match="publication"):
            plan.publish()
        assert not (first / "closures").exists()
        assert (second / "closures").read_text().splitlines() == ["close"]
        assert registry.get("plugin__second__echo") is None
        assert len(manager.plugins) == 1
        await manager.aclose()
        assert (first / "closures").read_text().splitlines() == ["close"]

    asyncio.run(scenario())


def test_failed_replacement_removes_candidate_but_preserves_admitted_handlers(tmp_path):
    async def scenario():
        root = tmp_path / "fixture"
        sibling = tmp_path / "healthy"
        package(root)
        package(sibling, body="healthy")
        registry = ToolRegistry()
        manager = PluginManager.discover_and_load(
            roots=(tmp_path,), disabled=frozenset(), registry=registry
        )
        registry.seal()
        admitted = registry.snapshot()
        name = "plugin__fixture__echo"
        healthy_name = "plugin__healthy__echo"
        try:
            package(root, body="partial replacement", fail=True)
            candidate = manager.prepare_reload(*discover_manifests((tmp_path,), frozenset()))
            assert registry.snapshot() is admitted
            candidate.publish()
            assert registry.get(name) is None
            assert registry.get(healthy_name) is admitted.get(healthy_name)
            assert any("fixture init failed" in warning for warning in manager.warnings)
            result = await admitted.get(name).execute(
                ToolCall(ToolCallId("admitted"), name, {}), ToolContext(cwd=tmp_path)
            )
            assert result.content == "one"
            package(root, body="repaired")
            manager.prepare_reload(*discover_manifests((tmp_path,), frozenset())).publish()
            result = await registry.get(name).execute(
                ToolCall(ToolCallId("repaired"), name, {}), ToolContext(cwd=tmp_path)
            )
            assert result.content == "repaired"
            assert not manager.warnings
            assert registry.get(healthy_name) is admitted.get(healthy_name)
            assert (sibling / "registrations").read_text().splitlines() == ["register"]
        finally:
            await manager.aclose()
        # The failed registration module is still owned for eventual cleanup.
        assert (root / "closures").read_text().splitlines() == ["close"] * 3
        assert (sibling / "closures").read_text().splitlines() == ["close"]

    asyncio.run(scenario())


def test_late_candidate_cannot_replace_newer_plugin_publication(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        manager = PluginManager.discover_and_load(
            roots=(tmp_path,), disabled=frozenset(), registry=registry
        )
        registry.seal()
        root = tmp_path / "fixture"
        package(root)
        old = manager.prepare_reload(*discover_manifests((tmp_path,), frozenset()))
        new = manager.prepare_reload(*discover_manifests((tmp_path,), frozenset()))
        new.publish()
        published = registry.snapshot()
        with pytest.raises(RuntimeError, match="publication"):
            old.publish()
        await old.abort()
        assert registry.snapshot() is published
        assert (root / "closures").read_text().splitlines() == ["close"]
        await manager.aclose()
        assert (root / "closures").read_text().splitlines() == ["close", "close"]

    asyncio.run(scenario())


def test_runtime_does_not_swallow_cancellation_from_unpublished_module_cleanup(
    tmp_path, monkeypatch
):
    async def scenario():
        home = tmp_path / "home"
        root = home / "plugins/fixture"
        code = package(root)

        class Model:
            async def aclose(self):
                pass

        registry = ToolRegistry()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "runtime.db",
            home_path=home,
            model=Model(),
            registry=registry,
        )
        original = registry.get("plugin__fixture__echo")
        code.write_text(
            "import asyncio\n" + code.read_text() + "    raise asyncio.CancelledError\n"
        )

        def fail(*args, **kwargs):
            raise ValueError("reject candidate")

        monkeypatch.setattr(runtime, "_prepare_mcp_configuration", fail)
        try:
            with pytest.raises(asyncio.CancelledError):
                await runtime._prepare_configuration_reload(
                    LocalConfigState((ConfigLayer(home / "config.toml", "user"),)), {}
                )
            assert registry.get("plugin__fixture__echo") is original
            assert runtime._pending_plugins is None
            assert (root / "closures").read_text().splitlines() == ["close"]
        finally:
            await runtime.aclose()
        assert (root / "closures").read_text().splitlines() == ["close", "close"]

    asyncio.run(scenario())


@pytest.mark.parametrize("broken", [True, False])
def test_late_registration_is_isolated_from_sealed_registry(tmp_path, broken):
    async def scenario():
        registry = ToolRegistry()
        manager = PluginManager.discover_and_load(
            roots=(tmp_path,), disabled=frozenset(), registry=registry
        )
        registry.seal()
        root = tmp_path / "late"
        package(root, fail=broken)
        manager.prepare_reload(*discover_manifests((tmp_path,), frozenset())).publish()
        assert (registry.get("plugin__late__echo") is None) is broken
        assert bool(manager.warnings) is broken
        await manager.aclose()
        assert (root / "closures").read_text().splitlines() == ["close"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_stage", ["register", "module"])
def test_runtime_owns_new_modules_when_registration_propagates_cancellation(
    tmp_path, failure_stage
):
    async def scenario():
        home = tmp_path / "home"
        root = home / "plugins/fixture"
        code = package(root)

        class Model:
            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "runtime.db",
            home_path=home,
            model=Model(),
        )
        previous = runtime._registry.snapshot()
        # A prior new sibling must be cleaned too when a later register aborts.
        sibling = home / "plugins/aaa_new"
        package(sibling)
        source = code.read_text()
        code.write_text(
            "import asyncio\n"
            + (
                source.replace(
                    "async def aclose():", "    raise asyncio.CancelledError\nasync def aclose():"
                )
                if failure_stage == "register"
                else source + "raise asyncio.CancelledError\n"
            )
        )
        try:
            with pytest.raises(asyncio.CancelledError):
                await runtime._prepare_configuration_reload(
                    LocalConfigState((ConfigLayer(home / "config.toml", "user"),)), {}
                )
            current = runtime._registry.snapshot()
            assert current.specs() == previous.specs()
            assert current.get("plugin__fixture__echo") is previous.get("plugin__fixture__echo")
            assert runtime._pending_plugins is None
            assert runtime._registry.get("plugin__aaa_new__echo") is None
        finally:
            await runtime.aclose()
            await runtime.aclose()
        assert (root / "closures").read_text().splitlines() == ["close", "close"]
        assert (sibling / "closures").read_text().splitlines() == ["close"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_stage", ["module", "missing_callable"])
def test_runtime_closes_failed_initial_entrypoint_and_healthy_sibling(tmp_path, failure_stage):
    async def scenario():
        home = tmp_path / "home"
        root = home / "plugins/fixture"
        code = package(root)
        code.write_text(
            code.read_text()
            + (
                "raise ValueError('module failed')\n"
                if failure_stage == "module"
                else "register = None\n"
            )
        )
        sibling = home / "plugins/healthy"
        package(sibling)

        class Model:
            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "runtime.db",
            home_path=home,
            model=Model(),
        )
        try:
            assert runtime._registry.get("plugin__fixture__echo") is None
            assert runtime._registry.get("plugin__healthy__echo") is not None
            assert any("fixture:" in warning for warning in runtime._plugin_manager.warnings)
            assert not (root / "registrations").exists()
        finally:
            await runtime.aclose()
            await runtime.aclose()
        assert (root / "closures").read_text().splitlines() == ["close"]
        assert (sibling / "closures").read_text().splitlines() == ["close"]

    asyncio.run(scenario())


def test_runtime_candidate_abort_joins_cleanup_after_repeated_cancel(tmp_path, monkeypatch):
    async def scenario():
        import corki.plugins.manager as plugin_module

        home = tmp_path / "home"
        root = home / "plugins/fixture"
        package(root)

        class Model:
            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "runtime.db",
            home_path=home,
            model=Model(),
        )
        original = runtime._registry.get("plugin__fixture__echo")
        entered, release = asyncio.Event(), asyncio.Event()
        closed = []
        load = plugin_module._load_entrypoint

        def held_loader(*args, **kwargs):
            module, register = load(*args, **kwargs)

            async def close():
                entered.set()
                await release.wait()
                closed.append(module.__name__)

            module.aclose = close
            return module, register

        def reject(*args, **kwargs):
            raise ValueError("reject candidate")

        package(root, body="new")
        monkeypatch.setattr(plugin_module, "_load_entrypoint", held_loader)
        monkeypatch.setattr(runtime, "_prepare_mcp_configuration", reject)
        pending = asyncio.create_task(
            runtime._prepare_configuration_reload(
                LocalConfigState((ConfigLayer(home / "config.toml", "user"),)), {}
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            for _ in range(2):
                pending.cancel()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert not pending.done()
                assert not closed
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert len(closed) == 1
            assert runtime._registry.get("plugin__fixture__echo") is original
            assert runtime._pending_plugins is None
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["manager", "candidate"])
def test_concurrent_close_waiters_join_same_module_cleanup(tmp_path, target):
    async def scenario():
        from types import ModuleType

        from corki.plugins.manager import PluginReload

        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        module = ModuleType("fixture_concurrent_close")

        async def close():
            calls.append("start")
            entered.set()
            await release.wait()
            calls.append("done")

        module.aclose = close
        manager = PluginManager((), (module,))
        if target == "candidate":
            candidate = PluginReload(PluginManager((), ()), manager, (module,), {}, lambda: None)
            operation = candidate.abort
        else:
            operation = manager.aclose
        first = asyncio.create_task(operation())
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            second = asyncio.create_task(operation())
            done, _ = await asyncio.wait({second}, timeout=0.01)
            assert not done
            assert calls == ["start"]
            release.set()
            await asyncio.gather(first, second)
            await operation()
            assert calls == ["start", "done"]
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)

    asyncio.run(scenario())
