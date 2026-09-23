import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize(
    "effort,expected",
    [(None, "low"), ("high", "high"), ("ultra", "xhigh"), ("persistent", "disabled")],
)
def test_runtime_uses_pinned_defaults_and_wire_effort_across_turns(
    tmp_path, monkeypatch, mode, effort, expected, injected=False
):
    async def scenario():
        bodies = []

        def respond(request):
            path = "responses" if mode == "responses" else "chat/completions"
            assert str(request.url) == f"https://fixture.invalid/v1/{path}"
            bodies.append(json.loads(request.content))
            if mode == "responses":
                event = {"type": "response.completed", "response": {"id": "r", "output": []}}
                body = "data: " + json.dumps(event) + "\n\n"
            else:
                event = {
                    "choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}]
                }
                body = "data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=body)

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        settings = CorkiSettings(
            tmp_path,
            model="gpt-6-astra",
            api_mode=mode,
            provider_name="openai",
            api_key="fixture",
            api_base="https://fixture.invalid/v1",
            reasoning_effort=effort,
            skills_enabled=False,
        )
        supplied = None
        if injected:
            adapter = OpenAIResponsesModel if mode == "responses" else OpenAICompatibleModel
            supplied = adapter(
                api_key="fixture",
                base_url=settings.api_base,
                reasoning_effort="ultra",
                capabilities=replace(
                    resolve_capabilities(
                        base_url=settings.api_base, api_mode=mode, provider_name="openai"
                    ),
                    supports_reasoning_effort=True,
                ),
            )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "s.db",
            registry=ToolRegistry(),
            model=supplied,
        )
        try:
            for prompt in ("first", "second"):
                assert isinstance([e async for e in runtime.stream(prompt)][-1], TurnCompleted)
            assert len(bodies) == 2
            for body in bodies:
                if mode == "responses":
                    assert body.get("reasoning") == {"effort": expected}
                    assert body.get("include") == ["reasoning.encrypted_content"]
                elif injected:
                    assert body.get("reasoning_effort") == expected
                else:
                    assert "reasoning_effort" not in body
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_runtime_pinned_default_does_not_inherit_an_injected_adapter_setting(
    tmp_path, monkeypatch, mode
):
    test_runtime_uses_pinned_defaults_and_wire_effort_across_turns(
        tmp_path, monkeypatch, mode, None, "low", injected=True
    )


@pytest.mark.parametrize("mode", ["v2", "legacy", "local"])
@pytest.mark.parametrize("selected", [None, "concise", "none"])
@pytest.mark.parametrize("default,supported", [("detailed", True), ("none", True), ("auto", False)])
def test_model_summary_default_override_and_gate_reach_manual_compaction(
    tmp_path, monkeypatch, mode, selected, default, supported
):
    async def scenario():
        bodies = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            body = json.loads(request.content)
            bodies.append(body)
            compact = len(bodies) == 2
            events = []
            events.append(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "summary" if compact else "done",
                                    }
                                ],
                            }
                        ],
                    },
                }
            )
            return httpx.Response(
                200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
            )

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        config = tmp_path / "config.toml"
        config.write_text(
            '[agent]\nmodel="custom-model"\n[skills]\nenabled=false\n[provider]\napi_mode="responses"\napi_key="fixture"\nbase_url="https://fixture.invalid/v1"\n'
            + ('name="custom"\n' if mode == "local" else 'name="openai"\n')
            + (f'reasoning_summary="{selected}"\n' if selected is not None else "")
            + "[models.catalog.custom-model]\ncontext_window=100000\n"
            + f'default_reasoning_summary="{default}"\n'
            + f"supports_reasoning_summary_parameter={str(supported).lower()}\n"
        )
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            remote_compaction_v2=mode != "legacy",
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings, database_path=tmp_path / "s.db", registry=ToolRegistry()
        )
        try:
            assert isinstance([e async for e in runtime.stream("OLD")][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("NEW")][-1], TurnCompleted)
            assert len(bodies) == 3
            summary = selected if selected is not None else default
            expected = {"summary": summary} if supported and summary != "none" else {}
            assert [b.get("reasoning") for b in bodies] == [expected] * 3
            for body in bodies:
                assert body["include"] == ["reasoning.encrypted_content"]
                assert all(item.get("type") != "compaction" for item in body["input"])
            assert "summary" in json.dumps(bodies[-1]["input"])
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
