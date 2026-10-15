import asyncio
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import StdioMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX owned group fixture")


async def until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.parametrize("mode", ["normal", "exit", "no-init"])
def test_close_cleans_stubborn_descendant_even_after_leader_exit_or_init_failure(tmp_path, mode):
    async def scenario():
        pid_file = tmp_path / "descendant.pid"
        script = Path(__file__).parents[1] / "fixtures/process_group_mcp_server.py"
        client = StdioMCPClient(
            MCPServerSettings(
                "tree",
                "stdio",
                command=sys.executable,
                args=(str(script), str(pid_file), mode),
                timeout_seconds=0.2 if mode == "no-init" else 3,
            )
        )
        child = process = None
        try:
            start = asyncio.create_task(client.start())
            await until(pid_file.exists)
            child = int(pid_file.read_text())
            process = client._process
            group_id = os.getpgid(child)
            if mode == "no-init":
                with pytest.raises(TimeoutError):
                    await start
            else:
                await start
                await client.list_tools()
                if mode == "exit":
                    await until(lambda: process.returncode is not None)
            assert group_id == process.pid, "MCP child did not receive an owned group"
            await asyncio.wait_for(client.aclose(), 5)
            await until(lambda: not alive(child))
            assert process.returncode is not None
            assert client._reader_task.done() and client._stderr_task.done()
            await client.aclose()
        finally:
            # RED-path cleanup targets only this fixture's exact child/direct process.
            if child is not None:
                with suppress(ProcessLookupError):
                    os.kill(child, signal.SIGKILL)
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await client.aclose()
            if process is not None:
                await process.wait()
                process._transport.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel_start", "close", "cancel_close"])
def test_spawn_handoff_and_repeated_cancellation_keep_cleanup_owned(tmp_path, monkeypatch, action):
    async def scenario():
        original = asyncio.create_subprocess_exec
        created, release = asyncio.Event(), asyncio.Event()
        processes = []

        async def spawn(*args, **kwargs):
            assert kwargs["process_group"] == 0
            process = await original(*args, **kwargs)
            processes.append(process)
            created.set()
            await release.wait()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        client = StdioMCPClient(
            MCPServerSettings(
                "handoff",
                "stdio",
                command=sys.executable,
                args=("-c", "import time; time.sleep(30)"),
            )
        )
        start = asyncio.create_task(client.start())
        close = None
        try:
            await asyncio.wait_for(created.wait(), 3)
            assert client._process is None
            if action == "cancel_start":
                start.cancel()
                await asyncio.sleep(0)
                start.cancel()
            else:
                close = asyncio.create_task(client.aclose())
                await asyncio.sleep(0)
                if action == "cancel_close":
                    close.cancel()
                    await asyncio.sleep(0)
                    close.cancel()
            await asyncio.sleep(0)
            assert not (close or start).done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(start, 5)
            if close is not None:
                if action == "cancel_close":
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(close, 5)
                else:
                    await asyncio.wait_for(close, 5)
            owner = client._close_task
            await client.aclose()
            assert client._close_task is owner and owner.done()
            assert client._spawn_task.done() and client.is_closed
            assert len(processes) == 1 and processes[0].returncode is not None
            assert client._reader_task.done() and client._stderr_task.done()
        finally:
            release.set()
            start.cancel()
            await asyncio.gather(start, return_exceptions=True)
            if close is not None:
                await asyncio.gather(close, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())


def test_shutdown_fails_pending_request_once_without_replaying(tmp_path):
    async def scenario():
        pid_file = tmp_path / "descendant.pid"
        script = Path(__file__).parents[1] / "fixtures/process_group_mcp_server.py"
        client = StdioMCPClient(
            MCPServerSettings(
                "pending",
                "stdio",
                command=sys.executable,
                args=(str(script), str(pid_file), "no-init"),
                timeout_seconds=30,
            )
        )
        start = asyncio.create_task(client.start())
        try:
            await until(pid_file.exists)
            process = client._process
            await client.aclose()
            from corki.mcp.client import MCPProtocolError

            with pytest.raises(MCPProtocolError, match="closed its output"):
                await asyncio.wait_for(start, 3)
            assert client._next_id == 1 and not client._pending
            assert process.returncode is not None
            await until(lambda: not alive(int(pid_file.read_text())))
        finally:
            await client.aclose()
            await asyncio.gather(start, return_exceptions=True)

    asyncio.run(scenario())


def test_last_client_reference_drop_terminates_the_owned_process_tree(tmp_path):
    async def scenario():
        import gc
        import weakref

        script = Path(__file__).parents[1] / "fixtures/process_group_mcp_server.py"
        pid_file = tmp_path / "descendant.pid"
        client = StdioMCPClient(
            MCPServerSettings(
                "drop",
                "stdio",
                command=sys.executable,
                args=(str(script), str(pid_file), "normal"),
            )
        )
        await client.start()
        process = client._process
        reader, stderr = client._reader_task, client._stderr_task
        child = int(pid_file.read_text())
        reference = weakref.ref(client)
        try:
            del client
            await asyncio.sleep(0)
            gc.collect()
            assert reference() is None, "background reader task retained the whole client"
            await until(lambda: not alive(child))
            await asyncio.wait_for(process.wait(), 3)
            await asyncio.gather(reader, stderr)
            assert process.returncode is not None
        finally:
            with suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            if reference() is not None:
                await reference().aclose()
            await process.wait()
            process._transport.close()
            for task in (reader, stderr):
                task.cancel()
            await asyncio.gather(reader, stderr, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["native", "compatible"])
def test_runtime_discovery_refresh_shutdown_and_cold_history_own_distinct_process_trees(
    tmp_path, monkeypatch, mode
):
    async def scenario():
        clients, processes, children = [], [], []
        script = Path(__file__).parents[1] / "fixtures/process_group_mcp_server.py"

        class Client(StdioMCPClient):
            async def start(self):
                await super().start()
                processes.append(self._process)
                children.append(int(Path(self.settings.args[1]).read_text()))

        def factory(settings):
            from dataclasses import replace

            client = Client(
                replace(
                    settings, args=(str(script), str(tmp_path / f"pid-{len(clients)}"), "normal")
                )
            )
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode=mode,
            mcp_servers=(MCPServerSettings("tree", "stdio", command=sys.executable),),
        )

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif self.count in (2, 3):
                    if self.count == 3:
                        result = next(
                            i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                        )
                        assert int(result.content.split("\nOutput:\n", 1)[1]) == children[0]
                        runtime.request_mcp_refresh()
                    call = ToolCall(new_tool_call_id(), "mcp__tree::lookup", {})
                else:
                    assert self.count == 4 and len(children) == 2
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert int(result.content.split("\nOutput:\n", 1)[1]) == children[1]
                    assert len(set(children)) == len({p.pid for p in processes}) == 2
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [e async for e in runtime.stream("process tree needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            raw = await runtime._repository.load_items(runtime._thread_id)
        finally:
            await runtime.aclose()
        await until(lambda: all(not alive(pid) for pid in children))
        assert all(p.returncode is not None for p in processes)

        class ColdModel:
            async def stream(self, request):
                results = [
                    i
                    for i in request.items
                    if isinstance(i, ToolResultItem) and i.tool_name == "mcp__tree::lookup"
                ]
                assert len(results) == 2
                yield ModelCompleted(
                    (AssistantMessageItem("cold", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        cold = await LangGraphRuntime.acreate(
            settings=settings,
            model=ColdModel(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            thread_id=runtime._thread_id,
        )
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            after = await cold._repository.load_items(cold._thread_id)
            assert after[: len(raw)] == raw
            # Cold history may sample from the shared directory during startup;
            # join its actual transport before asserting the completed RPC count.
            await cold._mcp_manager.prepare_server("tree")
            assert clients[-1]._next_id == 2  # initialize/list only; no tool replay.
        finally:
            await cold.aclose()
        await until(lambda: all(not alive(pid) for pid in children))
        assert len(clients) == 3 and all(c.is_closed for c in clients)
        assert all(p.returncode is not None for p in processes)
        assert all(c._reader_task.done() and c._stderr_task.done() for c in clients)

    asyncio.run(scenario())
