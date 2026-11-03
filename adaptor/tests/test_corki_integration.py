"""Optional client interoperability tests; Corki is not a gateway dependency."""

import asyncio
import json

import httpx
import pytest

pytest.importorskip("corki")
from corki.models import ModelCompleted, ModelRequest, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from test_gateway import running, sse


@pytest.mark.parametrize("content_type_present", [True, False])
def test_real_responses_adapter_can_consume_gateway_stream(tmp_path, content_type_present):
    def upstream(request):
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-6-astra"
        assert payload["reasoning"]["effort"] == "medium"
        assert payload["store"] is False
        response = sse()
        if not content_type_present:
            del response.headers["content-type"]
        return response

    with running(tmp_path, upstream) as (server, _, _):

        async def scenario():
            url = f"http://127.0.0.1:{server.server_port}/openai/v1"
            async with httpx.AsyncClient(trust_env=False) as client:
                model = OpenAIResponsesModel(
                    api_key="local-secret",
                    base_url=url,
                    client=client,
                    capabilities=resolve_capabilities(base_url=url, api_mode="responses"),
                )
                request = ModelRequest(
                    "gpt-6-astra",
                    "fixture",
                    (),
                    (UserMessageItem("hello", new_turn_id()),),
                    (),
                    reasoning_effort="medium",
                )
                events = [event async for event in model.stream(request)]
                assert any(isinstance(e, ModelCompleted) for e in events)

        asyncio.run(scenario())


def test_interrupted_upstream_is_not_reported_as_completed(tmp_path):
    from corki.models import ModelError

    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"type":"response.created","response":{"id":"r1"}}\n\n'
            raise httpx.ReadError("private upstream connection detail")

    def upstream(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Interrupted()
        )

    with running(tmp_path, upstream) as (server, _, _):

        async def scenario():
            url = f"http://127.0.0.1:{server.server_port}/openai/v1"
            async with httpx.AsyncClient(trust_env=False) as client:
                model = OpenAIResponsesModel(
                    api_key="local-secret",
                    base_url=url,
                    client=client,
                    max_retries=0,
                    capabilities=resolve_capabilities(base_url=url, api_mode="responses"),
                )
                request = ModelRequest(
                    "gpt-6-astra",
                    "fixture",
                    (),
                    (UserMessageItem("hello", new_turn_id()),),
                    (),
                    reasoning_effort="medium",
                )
                events = []
                with pytest.raises(ModelError):
                    async for event in model.stream(request):
                        events.append(event)
                assert not any(isinstance(e, ModelCompleted) for e in events)

        asyncio.run(scenario())


def test_chat_gateway_tool_roundtrip_and_usage(tmp_path):
    from corki.models import OpenAICompatibleModel
    from corki.protocol.items import ToolCallItem, ToolResultItem
    from corki.protocol.tools import ToolSpec

    requests = []

    def upstream(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "deepseek-flash"
        assert "reasoning" not in payload
        assert payload["store"] is False
        if len(requests) == 1:
            assert payload["tools"][0]["name"] == "probe"
            output = [
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "probe",
                    "arguments": '{"value":42}',
                }
            ]
        else:
            assert any(
                i.get("type") == "function_call_output" and i["output"] == "42"
                for i in payload["input"]
            )
            assert any(
                i.get("type") == "function_call" and i["call_id"] == "c1" for i in payload["input"]
            )
            output = [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=(
                "data: "
                + json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "r1",
                            "output": output,
                            "usage": {
                                "input_tokens": 100,
                                "output_tokens": 20,
                                "total_tokens": 120,
                                "input_tokens_details": {"cached_tokens": 30},
                            },
                        },
                    }
                )
                + "\n\n"
            ),
        )

    with running(tmp_path, upstream) as (server, _, _):

        async def scenario():
            url = f"http://127.0.0.1:{server.server_port}/openai"
            async with httpx.AsyncClient(trust_env=False) as client:
                adapter = OpenAICompatibleModel(
                    api_key="local-secret",
                    base_url=url,
                    client=client,
                    capabilities=resolve_capabilities(base_url=url, api_mode="chat_completions"),
                )
                turn = new_turn_id()
                request = ModelRequest(
                    "deepseek-flash",
                    "fixture",
                    (),
                    (UserMessageItem("run", turn),),
                    (
                        ToolSpec(
                            "probe",
                            "fixture",
                            {"type": "object", "properties": {"value": {"type": "integer"}}},
                        ),
                    ),
                )
                events = [e async for e in adapter.stream(request)]
                completed = next(e for e in events if isinstance(e, ModelCompleted))
                assert completed.usage.input_tokens == 100
                assert completed.usage.output_tokens == 20
                # Use canonical output items just as the ordinary harness does.
                calls = [item for item in completed.items if isinstance(item, ToolCallItem)]
                assert len(calls) == 1
                assert calls[0].call.id == "c1"
                from dataclasses import replace

                result = ToolResultItem("c1", "probe", "42", turn)
                followup = replace(request, items=(*request.items, *calls, result))
                second = [e async for e in adapter.stream(followup)]
                assert any(isinstance(e, ModelCompleted) for e in second)

        asyncio.run(scenario())
    assert len(requests) == 2


def test_real_runtime_grouped_instructions_reach_gateway(tmp_path):
    from corki.config import CorkiSettings
    from corki.core import LangGraphRuntime
    from corki.models import OpenAICompatibleModel
    from corki.protocol.events import TurnCompleted
    from corki.tools import ToolRegistry

    requests = []

    def upstream(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "deepseek-flash"
        assert any("在吗" in str(item) for item in payload["input"])
        return sse()

    with running(tmp_path, upstream) as (server, _, _):

        async def scenario():
            url = f"http://127.0.0.1:{server.server_port}/openai"
            settings = CorkiSettings(
                working_directory=tmp_path,
                model="deepseek-flash",
                api_mode="chat_completions",
                api_base=url,
                skills_enabled=False,
                memories_enabled=False,
                include_environment_context=True,
            )
            async with httpx.AsyncClient(trust_env=False) as client:
                adapter = OpenAICompatibleModel(
                    api_key="local-secret",
                    base_url=url,
                    client=client,
                    capabilities=resolve_capabilities(base_url=url, api_mode="chat_completions"),
                    max_retries=0,
                    request_max_retries=0,
                )
                runtime = await LangGraphRuntime.acreate(
                    settings=settings,
                    model=adapter,
                    registry=ToolRegistry(),
                    database_path=tmp_path / "runtime.db",
                    home_path=tmp_path / "home",
                )
                try:
                    events = [event async for event in runtime.stream("在吗")]
                    assert isinstance(events[-1], TurnCompleted)
                finally:
                    await runtime.aclose()

        asyncio.run(scenario())
    assert len(requests) == 1


@pytest.mark.parametrize("model,effort", [("gpt-6-astra", "medium"), ("gpt-5.6-sol", "high")])
def test_configured_model_and_effort_reach_upstream(tmp_path, model, effort):
    from corki.config import CorkiSettings
    from corki.config.migration import convert_toml

    requests = []

    def upstream(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == model
        assert payload["reasoning"]["effort"] == effort
        return sse()

    with running(tmp_path, upstream) as (server, _, _):
        config = tmp_path / "settings.json"
        config.write_text(
            convert_toml(f'''[agent]
model = "{model}"
[provider]
base_url = "http://127.0.0.1:{server.server_port}/openai"
api_key = "local-secret"
api_mode = "responses"
reasoning_effort = "{effort}"
''')
        )
        settings = CorkiSettings.for_directory(tmp_path, config_file=config)

        async def scenario():
            async with httpx.AsyncClient(trust_env=False) as client:
                adapter = OpenAIResponsesModel(
                    api_key=settings.api_key,
                    base_url=settings.api_base,
                    client=client,
                    capabilities=resolve_capabilities(
                        base_url=settings.api_base, api_mode=settings.api_mode
                    ),
                    reasoning_effort=settings.reasoning_effort,
                )
                request = ModelRequest(
                    settings.model,
                    "fixture",
                    (),
                    (UserMessageItem("hello", new_turn_id()),),
                    (),
                    reasoning_effort=settings.reasoning_effort,
                    model_info=settings.model_context_info(settings.model),
                )
                events = [e async for e in adapter.stream(request)]
                assert any(isinstance(e, ModelCompleted) for e in events)

        asyncio.run(scenario())
    assert len(requests) == 1


def test_certificate_failure_is_not_retried_by_responses_adapter(tmp_path):
    import ssl

    from corki.models import ModelError

    calls = []

    def upstream(request):
        calls.append(request)
        try:
            raise ssl.SSLCertVerificationError("private TLS diagnostic")
        except ssl.SSLCertVerificationError as exc:
            raise httpx.ConnectError("private upstream diagnostic") from exc

    with running(tmp_path, upstream) as (server, _, _):

        async def scenario():
            url = f"http://127.0.0.1:{server.server_port}/openai"
            async with httpx.AsyncClient(trust_env=False) as client:
                adapter = OpenAIResponsesModel(
                    api_key="local-secret",
                    base_url=url,
                    client=client,
                    capabilities=resolve_capabilities(base_url=url, api_mode="responses"),
                )
                request = ModelRequest(
                    "gpt-6-astra", "fixture", (), (UserMessageItem("hello", new_turn_id()),), ()
                )
                with pytest.raises(ModelError) as caught:
                    async for _ in adapter.stream(request):
                        pass
                assert not caught.value.retryable
                assert "certificate verification failed" in str(caught.value)
                assert "private" not in str(caught.value)

        asyncio.run(scenario())
    assert len(calls) == 1
