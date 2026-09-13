import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.protocol.tools import TextContent, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "mode,native", [("responses", True), ("responses", False), ("chat_completions", False)]
)
@pytest.mark.parametrize("failure", [None, "pre_commit", "post_commit"])
@pytest.mark.parametrize("provider_name", ["openai", "independent"])
def test_encrypted_observation_http_and_cold_replay(tmp_path, mode, native, failure, provider_name):
    from corki.protocol.events import TurnFailed
    from corki.protocol.items import ToolResultItem
    from corki.protocol.tools import EncryptedContent, ImageAttachment
    from corki.sessions.models import TurnStatus

    async def scenario():
        payloads, calls = [], []
        cipher = "opaque==" * 1000
        import base64
        import io

        from PIL import Image

        png = io.BytesIO()
        Image.new("RGB", (1, 1), "red").save(png, format="PNG")
        image = ImageAttachment(
            "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()
        )

        class Read:
            spec = ToolSpec(
                "recover", "Read opaque history", {"type": "object"}, output_char_budget=40
            )

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(
                    call.id,
                    call.name,
                    "safe preview",
                    content_items=(
                        TextContent("before"),
                        EncryptedContent(cipher),
                        TextContent("after" * 100),
                        image,
                    ),
                )

        def respond(request):
            assert request.url.host == "fixture.invalid"
            assert request.url.path == (
                "/v1/responses" if mode == "responses" else "/v1/chat/completions"
            )
            payloads.append(json.loads(request.content))
            first = len(payloads) == 1
            if mode == "responses":
                output = (
                    [
                        {
                            "type": "function_call",
                            "id": "item-call",
                            "call_id": "call-read",
                            "name": "recover",
                            "arguments": "{}",
                        }
                    ]
                    if first
                    else [
                        {
                            "type": "message",
                            "id": f"answer-{len(payloads)}",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ]
                )
                packet = {
                    "type": "response.completed",
                    "response": {"id": f"r-{len(payloads)}", "output": output},
                }
            else:
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-read",
                                "type": "function",
                                "function": {"name": "recover", "arguments": "{}"},
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

        def create(thread=None):
            registry = ToolRegistry()
            registry.register(Read())
            adapter = OpenAIResponsesModel if mode == "responses" else OpenAICompatibleModel
            model = adapter(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                client=client,
                capabilities=replace(
                    resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode=mode),
                    name=provider_name,
                    supports_encrypted_tool_output=native,
                ),
            )
            return LangGraphRuntime.create(
                settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
                database_path=tmp_path / "sessions.db",
                registry=registry,
                model=model,
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id
        original = runtime._repository.append_items
        save_turn = runtime._repository.save_turn
        injected = False

        async def append(target, items):
            nonlocal injected
            if failure and not injected and any(isinstance(item, ToolResultItem) for item in items):
                injected = True
                if failure == "post_commit":
                    await original(target, items)
                raise OSError("encrypted history commit fixture")
            await original(target, items)

        runtime._repository.append_items = append

        async def save(record):
            if failure and record.status is TurnStatus.FAILED:
                raise OSError("terminal commit fixture")
            await save_turn(record)

        runtime._repository.save_turn = save
        runtime._repository.retry_turn_terminal = save
        try:
            if failure:
                events = [event async for event in runtime.stream("recover prior work")]
                assert isinstance(events[-1], TurnFailed)
                assert events[-1].error_kind == "internal"
                assert "encrypted history commit fixture" in events[-1].error
            else:
                events = [event async for event in runtime.stream("recover prior work")]
                assert isinstance(events[-1], TurnCompleted)
            assert len(payloads) == (1 if failure else 2) and len(calls) == 1
        finally:
            if failure:
                # This is a crash boundary, not a graceful pending-write flush.
                runtime._pending_terminals.clear()
            await runtime.aclose()
        cold = create(thread)
        try:
            if failure:
                recovered = [event async for event in cold.resume_pending()]
                assert isinstance(recovered[-1], TurnCompleted)
            [event async for event in cold.stream("continue")]
            assert len(calls) == 1
            assert len(payloads) == 3
            for payload in payloads[1:]:
                assert cipher not in json.dumps(payload)
                if mode == "responses":
                    output = next(
                        item["output"]
                        for item in payload["input"]
                        if item.get("type") == "function_call_output"
                    )
                    assert output[0] == {"type": "input_text", "text": "before"}
                    assert output[1]["type"] == "input_text"
                    assert "Encrypted tool output unavailable" in output[1]["text"]
                    assert len(output[2]["text"]) < 500
                    assert output[3]["type"] == "input_image"
                    assert output[3]["image_url"].startswith("data:image/png;base64,")
                assert "Encrypted tool output unavailable" in str(payload)
            stored = await cold._repository.load_items(thread)
            parts = [part for item in stored for part in getattr(item, "content_items", ())]
            assert [
                part.encrypted_content for part in parts if isinstance(part, EncryptedContent)
            ] == [cipher]
        finally:
            await cold.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("code_mode", [False, True])
def test_archived_cipher_does_not_trigger_summary_and_js_projection_never_reads_it(
    tmp_path, code_mode
):
    from corki.models import ModelCompleted
    from corki.protocol.events import ContextCompacted
    from corki.protocol.ids import new_tool_call_id
    from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
    from corki.protocol.tools import EncryptedContent, ToolCall

    async def scenario():
        calls, requests = [], []
        cipher = "OPAQUE" * 100_000

        class Read:
            spec = ToolSpec("recover", "Read prior work", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(
                    call.id,
                    call.name,
                    "safe",
                    content_items=(
                        TextContent("before"),
                        EncryptedContent(cipher),
                        TextContent("after"),
                    ),
                )

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 1:
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="text(await tools.recover({}));",
                            input_kind="freeform",
                        )
                        if code_mode
                        else ToolCall(new_tool_call_id(), "recover", {})
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Read())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                token_budget_enabled=True,
                tool_mode="code_mode_only" if code_mode else "direct",
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=registry,
        )
        try:
            events = [event async for event in runtime.stream("ORIGINAL_USER")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(calls) == 1 and len(requests) == 2
            assert not any(isinstance(event, ContextCompacted) for event in events)
            if code_mode:
                results = [item for item in requests[-1].items if isinstance(item, ToolResultItem)]
                assert len(results) == 1 and "before\nafter" in results[0].content
                assert "OPAQUE" not in results[0].content and not results[0].is_error
                assert not any(isinstance(event, ContextCompacted) for event in events)
            else:
                assert "ORIGINAL_USER" in str(requests[-1].items)
                stored = await runtime._repository.load_items(runtime.thread_id)
                assert any(
                    isinstance(part, EncryptedContent) and part.encrypted_content == cipher
                    for item in stored
                    for part in getattr(item, "content_items", ())
                )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
