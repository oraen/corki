import asyncio
from dataclasses import FrozenInstanceError

import pytest

from corki.mcp import MCPServerMetadata
from corki.mcp.connection import MCPConnection


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_host_metadata_requires_explicit_boolean(value):
    with pytest.raises(ValueError, match="boolean"):
        MCPServerMetadata(value)


def test_connection_lease_covers_memory_mark_and_rejects_retired_calls():
    async def scenario():
        marked, release = asyncio.Event(), asyncio.Event()
        operations = []

        class Client:
            class settings:
                name = "fixture"

            async def call_tool(self, name, arguments):
                operations.append("call")
                return {"content": []}

            async def aclose(self):
                operations.append("close")

        connection = MCPConnection(Client(), lambda *args: None)
        with pytest.raises(FrozenInstanceError):
            connection.metadata.pollutes_memory = False

        async def mark():
            operations.append("mark")
            marked.set()
            await release.wait()

        task = asyncio.create_task(connection.call_tool("read", {}, on_external_context=mark))
        try:
            await asyncio.wait_for(marked.wait(), 2)
            connection.retire()
            assert operations == ["mark"] and not connection.closed
            with pytest.raises(Exception, match="superseded"):
                await connection.call_tool("read", {}, on_external_context=mark)
            assert operations == ["mark"]
            release.set()
            await asyncio.wait_for(task, 2)
            await connection.aclose()
            assert operations == ["mark", "call", "close"]
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await connection.aclose()

    asyncio.run(scenario())
