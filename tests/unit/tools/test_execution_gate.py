import asyncio

import pytest

from corki.tools.execution_gate import ExecutionGate


def test_waiting_writer_precedes_later_readers():
    async def scenario():
        gate = ExecutionGate()
        release = asyncio.Event()
        events = []

        async def call(name, parallel):
            async with gate.enter(parallel=parallel):
                events.append(name)
                if name in {"r1", "r2"}:
                    await release.wait()

        tasks = [
            asyncio.create_task(call(name, parallel))
            for name, parallel in (
                ("r1", True),
                ("r2", True),
                ("w", False),
                ("r3", True),
            )
        ]
        try:
            async with asyncio.timeout(1):
                while len(gate._waiting) != 2:
                    await asyncio.sleep(0)
                assert events == ["r1", "r2"]
                release.set()
                await asyncio.gather(*tasks)
            assert events == ["r1", "r2", "w", "r3"]
            assert not gate._waiting and not gate._writer and gate._readers == 0
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_granted", [False, True])
def test_cancelled_writer_does_not_let_reader_bypass_active_writer(cancel_granted):
    async def scenario():
        gate = ExecutionGate()
        entered, release = asyncio.Event(), asyncio.Event()
        events = []

        async def first():
            async with gate.enter(parallel=False):
                entered.set()
                await release.wait()

        async def next_call(name, parallel):
            async with gate.enter(parallel=parallel):
                events.append(name)

        owner = asyncio.create_task(first())
        await entered.wait()
        writer = asyncio.create_task(next_call("writer", False))
        reader = asyncio.create_task(next_call("reader", True))
        try:
            while len(gate._waiting) != 2:
                await asyncio.sleep(0)
            if cancel_granted:
                # Wake writer, then cancel it before it consumes its granted slot.
                release.set()
                await owner
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
            if not cancel_granted:
                assert not events and not reader.done()
                release.set()
            await asyncio.wait_for(asyncio.gather(owner, reader), 1)
            assert events == ["reader"]
            assert not gate._waiting and not gate._writer and gate._readers == 0
        finally:
            for task in (owner, writer, reader):
                task.cancel()
            await asyncio.gather(owner, writer, reader, return_exceptions=True)

    asyncio.run(scenario())
