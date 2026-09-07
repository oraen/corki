import asyncio
from dataclasses import replace

import pytest

from corki.code_mode.service import CodeModeService
from corki.protocol.tools import ToolConcurrency, ToolExposure, ToolResult, ToolSpec


class Registry:
    def __init__(self, spec):
        self.definition = spec
        self.selected = asyncio.Event()

    def spec(self, name):
        self.selected.set()
        return self.definition if self.definition.name == name else None


@pytest.mark.parametrize("kind", ["json", "freeform"])
def test_old_cell_payload_kind_is_not_reinterpreted_by_current_schema(kind):
    async def scenario():
        old = ToolSpec("probe", "old", {}, input_kind=kind)
        new = replace(old, input_kind="freeform" if kind == "json" else "json")
        registry = Registry(new)
        service = CodeModeService(registry)
        calls = []

        async def dispatch(call, spec):
            calls.append((call, spec))
            return ToolResult(call.id, call.name, "observed")

        failure = asyncio.get_running_loop().create_future()
        service.activate("turn", dispatch, None, failure, registry=registry)
        try:
            assert await service.invoke(old, {} if kind == "json" else "raw") == "observed"
            assert calls[0][0].input_kind == kind
            assert calls[0][1] == new
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_retry_with_same_snapshot_retains_nested_execution_gate():
    async def scenario():
        spec = ToolSpec("probe", "exclusive", {})
        registry = Registry(spec)
        service = CodeModeService(registry)
        started, release = asyncio.Event(), asyncio.Event()
        calls, tasks = [], []

        async def dispatch(call, definition):
            calls.append(call)
            if len(calls) == 1:
                started.set()
                await release.wait()
            return ToolResult(call.id, call.name, "done")

        service.activate("turn", dispatch, None, registry=registry)
        try:
            tasks.append(asyncio.create_task(service.invoke(spec, {})))
            await asyncio.wait_for(started.wait(), 2)
            original = service.step_calls[0]
            service.activate("turn", dispatch, None, registry=registry)
            assert service.step_calls == [original] and service.barrier == (original,)
            registry.selected.clear()
            tasks.append(asyncio.create_task(service.invoke(spec, {})))
            await asyncio.wait_for(registry.selected.wait(), 2)
            assert len(service.step_calls) == 2 and len(calls) == 1
            release.set()
            assert await asyncio.gather(*tasks) == ["done", "done"]
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["invoke", "notify"])
def test_waiter_rechecks_admission_if_worker_pauses_after_wakeup(method):
    async def scenario():
        first_wait, requeued = asyncio.Event(), asyncio.Event()

        class AdmissionEvent(asyncio.Event):
            waits = 0

            async def wait(self):
                self.waits += 1
                (first_wait if self.waits == 1 else requeued).set()
                return await super().wait()

        spec = ToolSpec("probe", "fixture", {})
        registry = Registry(spec)
        service = CodeModeService(registry)
        service.active = AdmissionEvent()
        calls = []

        async def dispatch(call, definition):
            calls.append("invoke")
            return ToolResult(call.id, call.name, "done")

        async def notify(call_id, text):
            calls.append("notify")

        task = asyncio.create_task(
            service.invoke(spec, {}) if method == "invoke" else service.notify("cell", "notice")
        )
        try:
            await asyncio.wait_for(first_wait.wait(), 2)
            service.activate("turn", dispatch, notify, registry=registry)
            service.pause()  # synchronous clear before the awakened coroutine gets to run
            await asyncio.wait_for(requeued.wait(), 2)
            assert not calls and not task.done()
            service.activate("turn", dispatch, notify, registry=registry)
            await asyncio.wait_for(task, 2)
            assert calls == [method]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await service.aclose()

    asyncio.run(scenario())


def test_hidden_current_handler_is_exclusive_and_admitted_calls_keep_their_worker():
    async def scenario():
        old = ToolSpec("probe", "old", {}, concurrency=ToolConcurrency.PARALLEL)
        current = replace(old, description="current", exposure=ToolExposure.HIDDEN)
        registry = Registry(current)
        service = CodeModeService(registry)
        started, release = asyncio.Event(), asyncio.Event()
        calls, tasks = [], []

        async def dispatch(call, spec):
            calls.append((call, spec))
            if len(calls) == 1:
                started.set()
                await release.wait()
            return ToolResult(call.id, call.name, "current")

        async def wrong_dispatch(call, spec):
            pytest.fail("already admitted call was rebound to another worker")

        failure = asyncio.get_running_loop().create_future()
        service.activate("turn", dispatch, None, failure, registry=registry)
        try:
            tasks.append(asyncio.create_task(service.invoke(old, {})))
            await asyncio.wait_for(started.wait(), 2)
            assert service.barrier == (service.step_calls[0],), "Hidden disables parallelism"
            registry.selected.clear()
            tasks.append(asyncio.create_task(service.invoke(old, {})))
            await asyncio.wait_for(registry.selected.wait(), 2)
            assert len(service.step_calls) == 2
            assert len(calls) == 1, "second call must remain behind the exclusive gate"
            service.activate(
                "turn",
                wrong_dispatch,
                None,
                failure,
                registry=Registry(replace(current, description="too new")),
            )
            release.set()
            assert await asyncio.gather(*tasks) == ["current", "current"]
            assert all(spec == current for _, spec in calls)
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await service.aclose()

    asyncio.run(scenario())
