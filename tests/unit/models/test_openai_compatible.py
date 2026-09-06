import asyncio
import json

import httpx

from corki.models import (
    ModelCompleted,
    ModelReasoningDelta,
    ModelRequest,
    ModelTextDelta,
    OpenAICompatibleModel,
    resolve_capabilities,
)
from corki.protocol.ids import new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ContextRole,
    ReasoningItem,
    ToolCallItem,
    UserMessageItem,
    new_step_id,
)


def test_openai_compatible_adapter_streams_text_and_assembles_tool_call() -> None:
    packets = [
        {"choices": [{"delta": {"reasoning_content": "checking"}}]},
        {"choices": [{"delta": {"content": "Hi "}}]},
        {
            "choices": [
                {
                    "delta": {
                        "content": "there",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {"name": "exec_", "arguments": '{"cmd":'},
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"name": "command", "arguments": '"pwd"}'},
                            }
                        ]
                    }
                }
            ]
        },
    ]
    body = "\n\n".join(f"data: {json.dumps(packet)}" for packet in packets)
    body += "\n\ndata: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, text=body)

    async def scenario() -> list[object]:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model = OpenAICompatibleModel(
            api_key="test",
            base_url="https://example.test/v1",
            capabilities=resolve_capabilities(
                base_url="https://example.test/v1", api_mode="chat_completions"
            ),
            client=client,
        )
        turn_id = new_turn_id()
        request = ModelRequest(
            model="test-model",
            instructions="system",
            context_items=(),
            items=(UserMessageItem("hello", turn_id),),
            tools=(),
        )
        events = [event async for event in model.stream(request)]
        await client.aclose()
        return events

    events = asyncio.run(scenario())

    assert [event.delta for event in events if isinstance(event, ModelTextDelta)] == [
        "Hi ",
        "there",
    ]
    assert [event.delta for event in events if isinstance(event, ModelReasoningDelta)] == [
        "checking"
    ]
    completed = next(event for event in events if isinstance(event, ModelCompleted))
    assistant = next(item for item in completed.items if isinstance(item, AssistantMessageItem))
    reasoning = next(item for item in completed.items if isinstance(item, ReasoningItem))
    tool_call = next(item for item in completed.items if isinstance(item, ToolCallItem))
    assert assistant.content == "Hi there"
    assert reasoning.content == "checking"
    assert tool_call.call.name == "exec_command"
    assert tool_call.call.arguments == {"cmd": "pwd"}


def test_adapter_maps_developer_role_and_sends_thinking_override() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n',
        )

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model = OpenAICompatibleModel(
            api_key="test",
            base_url="https://example.test",
            thinking_enabled=True,
            reasoning_effort="high",
            capabilities=resolve_capabilities(
                base_url="https://api.deepseek.com",
                api_mode="chat_completions",
                provider_name="deepseek",
            ),
            client=client,
        )
        turn_id = new_turn_id()
        step_id = new_step_id()
        request = ModelRequest(
            model="deepseek-v4-pro",
            instructions="base",
            context_items=(ContextItem("mode", ContextRole.DEVELOPER, "mode", turn_id),),
            items=(
                ReasoningItem("private reasoning", turn_id, step_id),
                AssistantMessageItem("previous", turn_id, step_id),
                UserMessageItem("hello", turn_id),
            ),
            tools=(),
        )
        _ = [event async for event in model.stream(request)]
        await client.aclose()

    asyncio.run(scenario())

    assert captured["thinking"] == {"type": "enabled"}
    assert captured["reasoning_effort"] == "high"
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert messages[1]["role"] == "system"
    assert messages[2]["reasoning_content"] == "private reasoning"


def test_adapter_maps_structured_output_to_provider_protocol() -> None:
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }

    openai = OpenAICompatibleModel(
        api_key="test",
        base_url="https://api.openai.com/v1",
        capabilities=resolve_capabilities(
            base_url="https://api.openai.com/v1", api_mode="chat_completions"
        ),
    )
    deepseek = OpenAICompatibleModel(
        api_key="test",
        base_url="https://api.deepseek.com",
        capabilities=resolve_capabilities(
            base_url="https://api.deepseek.com", api_mode="chat_completions"
        ),
    )
    structured = ModelRequest(
        "test-model",
        "system",
        (),
        (UserMessageItem("hello", new_turn_id()),),
        (),
        output_schema=schema,
        output_schema_name="answer_contract",
    )

    openai_payload = openai._build_payload(structured)
    deepseek_payload = deepseek._build_payload(structured)
    asyncio.run(openai.aclose())
    asyncio.run(deepseek.aclose())

    assert openai_payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "answer_contract",
            "strict": True,
            "schema": schema,
        },
    }
    assert deepseek_payload["response_format"] == {"type": "json_object"}
