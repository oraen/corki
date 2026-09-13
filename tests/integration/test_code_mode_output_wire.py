import asyncio
import json
import sqlite3

import httpx
import pytest

from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core import LangGraphRuntime
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("budget", [1000, 20000])
@pytest.mark.parametrize("failed", [False, True])
def test_cell_output_http_projection_and_cold_archive(tmp_path, api, budget, failed):
    async def scenario():
        requests = []
        source = '// @exec: {"max_output_tokens":20000}\ntext("X".repeat(50000));'
        if failed:
            source += 'throw new Error("boom");'

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            first = len(requests) == 1
            function = {"name": "exec", "arguments": json.dumps({"input": source})}
            if api == "responses":
                output = (
                    [{"type": "function_call", "call_id": "cell-call", **function}]
                    if first
                    else [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ]
                )
                packet = {
                    "type": "response.completed",
                    "response": {"id": f"r{len(requests)}", "output": output},
                }
            else:
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "cell-call",
                                "type": "function",
                                "function": function,
                            }
                        ]
                    }
                    if first
                    else {"content": "done"}
                )
                packet = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": "tool_calls" if first else "stop",
                        }
                    ]
                }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        settings = CorkiSettings(
            working_directory=tmp_path,
            model="fixture",
            skills_enabled=False,
            api_mode=api,
            tool_mode="code_mode",
            tool_search_mode="disabled",
            model_contexts=parse_model_contexts(
                {
                    "fixture": {
                        "context_window": 100000,
                        "truncation_policy": {"mode": "tokens", "limit": budget},
                    }
                }
            ),
        )
        database = tmp_path / "cell.db"

        def create(thread=None):
            adapter = OpenAIResponsesModel if api == "responses" else OpenAICompatibleModel
            return LangGraphRuntime.create(
                settings=settings,
                database_path=database,
                registry=ToolRegistry(),
                thread_id=thread,
                model=adapter(
                    api_key="fixture",
                    base_url="https://fixture.invalid/v1",
                    client=client,
                    capabilities=resolve_capabilities(
                        base_url="https://fixture.invalid/v1", api_mode=api
                    ),
                ),
            )

        runtime = create()
        thread = runtime.thread_id
        try:
            events = [e async for e in runtime.stream("execute")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            originals = [
                i
                for i in await runtime._repository.load_items(thread)
                if isinstance(i, ToolResultItem)
            ]
            assert len(originals) == 1 and originals[0].is_error is failed
            assert originals[0].content.count("X") == 50000
            if failed:
                assert "Script error:\n" in originals[0].content
            with sqlite3.connect(database) as db:
                (encoded,) = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name='exec'"
                ).fetchone()
                value = json.loads(encoded)
                assert value["content"].count("X") == 50000
                assert value.get("legacy_output_char_budget") is None
                assert not value.get("dispatch_error", False)
        finally:
            await runtime.aclose()
        cold = create(thread)
        try:
            assert isinstance([e async for e in cold.stream("continue")][-1], TurnCompleted)
            assert isinstance([e async for e in cold.compact()][-1], TurnCompleted)
            assert len(requests) == 4
            for request in requests[1:]:
                outputs = [
                    i
                    for i in request.get("input", request.get("messages", []))
                    if i.get("type") in {"function_call_output", "custom_tool_call_output"}
                    or i.get("role") == "tool"
                ]
                assert len(outputs) == 1
                output = outputs[0].get("output", outputs[0].get("content"))
                if isinstance(output, list):
                    output = "\n".join(p["text"] for p in output)
                if budget == 20000:
                    assert output.count("X") == 50000 and "truncated" not in output
                else:
                    assert "tokens truncated" in output and output.count("X") < 4800
                assert "Script failed" in output if failed else "Script completed" in output
            with sqlite3.connect(database) as db:
                assert (
                    db.execute(
                        "SELECT result_json FROM tool_executions WHERE tool_name='exec'"
                    ).fetchone()[0]
                    == encoded
                )
        finally:
            await cold.aclose()
            await client.aclose()

    asyncio.run(scenario())
