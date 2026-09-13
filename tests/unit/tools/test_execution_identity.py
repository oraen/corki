import asyncio
import os

import pytest

from corki import __version__
from corki.config import ShellEnvironmentPolicy
from corki.protocol.execution_identity import ExecutionIdentity
from corki.protocol.ids import new_session_id, new_thread_id
from corki.tools.builtin.process import ProcessManager
from corki.tools.builtin.shell_environment import unified_exec_environment


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_host_identity_bypasses_include_only_and_wins_over_set(platform):
    identity = ExecutionIdentity(new_thread_id(), new_session_id())
    result = unified_exec_environment(
        {},
        ShellEnvironmentPolicy(
            inherit="none",
            include_only=("NOTHING",),
            set={
                "CODEX_THREAD_ID": "fake",
                "CODEX_SESSION_ID": "fake",
                "CODEX_VERSION": "fake",
            },
        ),
        identity=identity,
        platform=platform,
    )
    assert {k: v for k, v in result.items() if k.startswith("CODEX_")} == {
        "CODEX_THREAD_ID": identity.thread_id,
        "CODEX_SESSION_ID": identity.session_id,
        "CODEX_VERSION": __version__,
        "CODEX_CI": "1",
    }


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_stale_host_metadata_removal_preserves_platform_case_rules(platform):
    identity = ExecutionIdentity(new_thread_id(), new_session_id())
    names = [
        "CODEX_PERMISSION_PROFILE",
        "codex_permission_profile",
        "CODEX_VERSION",
        "codex_version",
        "CODEX_APPLY_PATCH_PRESERVE_LINE_ENDINGS",
        "Codex_Apply_Patch_Preserve_Line_Endings",
    ]
    result = unified_exec_environment(
        {name: "fake-inherited" for name in names},
        ShellEnvironmentPolicy(set={name: "fake-configured" for name in names}),
        identity=identity,
        platform=platform,
    )
    assert [result.get(name) for name in names] == (
        [None, None, __version__, None, None, None]
        if platform == "win32"
        else [None, "fake-configured", __version__, "fake-configured", None, None]
    )


def test_standalone_manager_does_not_inherit_outer_runtime_identity():
    result = unified_exec_environment(
        {"CODEX_THREAD_ID": "outer", "codex_session_id": "outer"},
        ShellEnvironmentPolicy(),
    )
    assert "CODEX_THREAD_ID" not in result and "codex_session_id" not in result
    assert result["CODEX_VERSION"] == __version__


def test_manager_identity_is_immutable_and_typed():
    identity = ExecutionIdentity(new_thread_id(), new_session_id())
    manager = ProcessManager()
    with pytest.raises(TypeError):
        manager.bind_identity({"thread_id": "fake"})
    manager.bind_identity(identity)
    manager.bind_identity(identity)
    with pytest.raises(RuntimeError, match="cannot change"):
        manager.bind_identity(ExecutionIdentity(new_thread_id(), new_session_id()))


@pytest.mark.skipif(os.name == "nt", reason="POSIX command")
def test_completed_process_does_not_reopen_identity_binding(tmp_path):
    async def scenario():
        manager = ProcessManager()
        try:
            result = await manager.execute("true", cwd=tmp_path, yield_seconds=1, login=False)
            assert result.exit_code == 0
            with pytest.raises(RuntimeError, match="cannot change"):
                manager.bind_identity(ExecutionIdentity(new_thread_id(), new_session_id()))
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_close", [False, True])
def test_close_joins_identity_resolution_without_spawning(tmp_path, monkeypatch, cancel_close):
    async def scenario():
        manager = ProcessManager()
        entered, release = asyncio.Event(), asyncio.Event()
        spawned = []

        async def resolve():
            entered.set()
            await release.wait()
            manager.bind_identity(ExecutionIdentity(new_thread_id(), new_session_id()))

        async def spawn(*args, **kwargs):
            spawned.append(True)
            raise AssertionError("must not spawn after close")

        manager.set_identity_resolver(resolve)
        monkeypatch.setattr(manager, "_start_session", spawn)
        execution = asyncio.create_task(manager.execute("unused", cwd=tmp_path, yield_seconds=0))
        close = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            close = asyncio.create_task(manager.terminate_all())
            await asyncio.sleep(0)
            if cancel_close:
                close.cancel()
                await asyncio.sleep(0)
                close.cancel()
                await asyncio.sleep(0)
            assert not close.done() and not execution.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await execution
            if cancel_close:
                with pytest.raises(asyncio.CancelledError):
                    await close
            else:
                await close
            assert spawned == [] and not manager._starting and not manager._sessions
        finally:
            release.set()
            await asyncio.gather(execution, *((close,) if close else ()), return_exceptions=True)

    asyncio.run(scenario())


def test_cancel_while_resolving_identity_does_not_launch_command(tmp_path, monkeypatch):
    async def scenario():
        manager = ProcessManager()
        entered, release = asyncio.Event(), asyncio.Event()
        spawned = []

        async def resolve():
            entered.set()
            await release.wait()
            manager.bind_identity(ExecutionIdentity(new_thread_id(), new_session_id()))

        async def spawn(*args, **kwargs):
            spawned.append(True)
            raise AssertionError("command was not admitted before cancellation")

        manager.set_identity_resolver(resolve)
        monkeypatch.setattr(manager, "_start_session", spawn)
        execution = asyncio.create_task(manager.execute("unused", cwd=tmp_path, yield_seconds=0))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            execution.cancel()
            await asyncio.sleep(0)
            execution.cancel()
            await asyncio.sleep(0)
            assert not execution.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await execution
            assert spawned == [] and not manager._starting
        finally:
            release.set()
            await asyncio.gather(execution, return_exceptions=True)
            await manager.terminate_all()

    asyncio.run(scenario())
