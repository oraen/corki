"""Original startup results and recovery readiness have distinct ownership."""

import asyncio

import pytest

from corki.mcp.client import MCPProtocolError
from corki.mcp.resource_binding import MCPResourceBinding
from corki.mcp.startup import MCPPreparation


@pytest.mark.parametrize("resource", [False, True])
@pytest.mark.parametrize("outcome", ["failed", "cancelled", "caller_cancelled"])
def test_original_startup_result_cannot_be_rewritten_by_recovery(resource, outcome):
    async def scenario():
        generation = MCPPreparation((), None, False, [], {}, {}, [], {}, {}, {})
        release = asyncio.Event()
        calls = []

        class Recovered:
            async def invoke(self, *args):
                calls.append(args)
                return "recovered"

        recovered = Recovered()

        async def initial():
            await release.wait()
            # Recovery may publish before an original startup waiter is resumed.
            generation.staged["codex_apps"] = recovered
            generation.initial_failures["codex_apps"] = "original startup failed"
            if outcome == "cancelled":
                raise asyncio.CancelledError
            return False

        startup = asyncio.create_task(initial())
        generation.tasks["codex_apps"] = startup
        binding = MCPResourceBinding(generation, {}, lambda _: None)

        async def lookup():
            if resource:
                return await binding.read_resource("codex_apps", "fixture:resource")
            return await generation.wait_for_client("codex_apps")

        waiter = asyncio.create_task(lookup())
        try:
            await asyncio.sleep(0)
            if outcome == "caller_cancelled":
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
                assert not startup.done(), "request cancellation cancelled shared startup"
                release.set()
            else:
                release.set()
                with pytest.raises(MCPProtocolError, match="startup.*(failed|cancelled)"):
                    await waiter
            await asyncio.gather(startup, return_exceptions=True)
            assert calls == []
            assert await lookup() == ("recovered" if resource else recovered)
            assert len(calls) == int(resource)
        finally:
            release.set()
            await asyncio.gather(startup, waiter, return_exceptions=True)
            binding.release()
            await binding.closed

    asyncio.run(scenario())
