import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import CompactionItem, UserMessageItem
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["v2", "legacy", "local"])
@pytest.mark.parametrize("fail,checkpoint", [(False, False), (False, True), (True, False)])
def test_cold_downshift_compacts_with_previous_model_before_current_input(
    tmp_path, monkeypatch, mode, fail, checkpoint
):
    async def scenario():
        from corki.config.layers import ConfigLayer, LocalConfigState
        from corki.core.stop_hooks import command_identity

        requests = []
        hook_payloads = []
        source = tmp_path / "config.toml"
        hook_document = ""
        for event, key in (("PreCompact", "pre_compact"), ("PostCompact", "post_compact")):
            fingerprint, _ = command_identity(
                {"type": "command", "command": "review"}, event_name=event
            )
            hook_document += (
                f'[[hooks.{event}]]\n[[hooks.{event}.hooks]]\ntype="command"\ncommand="review"\n'
                f"[hooks.state.{json.dumps(f'{source}:{key}:0:0')}]\n"
                f"trusted_hash={json.dumps(fingerprint)}\n"
            )

        async def run_hook(command, payload, **kwargs):
            hook_payloads.append(payload)
            return {"exit_code": 0, "stdout": "{}", "stderr": ""}

        monkeypatch.setattr("corki.core.compact_hooks.run_command", run_hook)

        def respond(request):
            body = json.loads(request.content)
            requests.append((str(request.url), body, request.headers.get("x-codex-turn-state")))
            compact = len(requests) == 2
            if compact and fail:
                return httpx.Response(
                    400,
                    json={"error": {"code": "invalid_prompt", "message": "old-model unavailable"}},
                )
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            assert not any(i.get("type") == "compaction_trigger" for i in body["input"])
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
                        "usage": {
                            "input_tokens": 20000 if len(requests) == 1 else 10,
                            "output_tokens": 0,
                            "total_tokens": 20000 if len(requests) == 1 else 10,
                        },
                    },
                }
            )
            return httpx.Response(
                200,
                text="".join("data: " + json.dumps(e) + "\n\n" for e in events),
                headers={"x-codex-turn-state": "old-request-route"},
            )

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )

        async def create(model, thread=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    model=model,
                    model_contexts=(
                        ModelContextInfo("large", 100000),
                        ModelContextInfo("small", 10000),
                    ),
                    api_mode="responses",
                    provider_name="custom" if mode == "local" else "openai",
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                    skills_enabled=False,
                    remote_compaction_v2=mode != "legacy",
                    model_max_retries=0,
                    model_request_max_retries=0,
                    configuration=LocalConfigState(
                        (ConfigLayer(source, "user", contents=hook_document),)
                    ),
                ),
                database_path=tmp_path / "s.db",
                registry=ToolRegistry(),
                thread_id=thread,
            )

        first = await create("large")
        try:
            assert isinstance([e async for e in first.stream("OLD_USER")][-1], TurnCompleted)
            thread = first.thread_id
        finally:
            await first.aclose()
        second = await create("small", thread)
        try:
            if checkpoint:
                await second._ensure_ready()
                turn = new_turn_id()
                user = UserMessageItem("NEW_USER", turn)
                await second._repository.save_turn(
                    TurnRecord(turn, thread, TurnStatus.RUNNING, "NEW_USER")
                )

                class Sink:
                    async def emit(self, event):
                        pass

                config = second._graph_config(turn)
                await second._compiled.ainvoke(
                    _initial_state(thread, turn, second._settings, user),
                    config=config,
                    context=GraphRunContext(events=Sink()),
                    interrupt_before=["call_model"],
                )
                assert len(requests) == 2
                assert (await second._compiled.aget_state(config)).next == ("call_model",)
                await second.aclose()
                second = await create("small", thread)
                events = [e async for e in second.resume_pending()]
            else:
                events = [e async for e in second.stream("NEW_USER")]
            assert isinstance(events[-1], TurnFailed if fail else TurnCompleted), events[-1]
            assert requests[1][1]["model"] == "large"
            assert [p["hook_event_name"] for p in hook_payloads] == (
                ["PreCompact"] if fail else ["PreCompact", "PostCompact"]
            )
            assert all(p["model"] == "large" and p["trigger"] == "auto" for p in hook_payloads)
            assert "NEW_USER" not in json.dumps(requests[1][1]["input"])
            assert requests[1][2] is None
            if fail:
                assert len(requests) == 2
                assert not any(
                    isinstance(i, CompactionItem)
                    for i in await second._repository.load_items(thread)
                )
            else:
                assert requests[2][1]["model"] == "small"
                assert "NEW_USER" in json.dumps(requests[2][1]["input"])
                if mode == "v2":
                    assert requests[2][2] is None
                    assert "client_metadata" not in requests[1][1]
                assert len(requests) == 3
        finally:
            await second.aclose()
        if not fail:
            third = await create("small", thread)
            try:
                assert isinstance([e async for e in third.stream("NEXT_USER")][-1], TurnCompleted)
                assert len(requests) == 4
            finally:
                await third.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "previous,current", [(None, "b"), ("a", None), ("a", "a"), ("a", "b"), ("", "b")]
)
@pytest.mark.parametrize("token_budget", [False, True])
def test_same_model_hash_change_on_cold_resume_with_low_usage(
    tmp_path, monkeypatch, previous, current, token_budget
):
    async def scenario():
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            events = []
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            assert not any(i.get("type") == "compaction_trigger" for i in body["input"])
            events.append(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "summary or reply"}],
                            }
                        ],
                        "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
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

        async def create(comp_hash, thread=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    model="same",
                    model_contexts=(ModelContextInfo("same", 100000, comp_hash=comp_hash),),
                    api_mode="responses",
                    provider_name="openai",
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                    skills_enabled=False,
                    token_budget_enabled=token_budget,
                    model_max_retries=0,
                    model_request_max_retries=0,
                ),
                database_path=tmp_path / "s.db",
                registry=ToolRegistry(),
                thread_id=thread,
            )

        first = await create(previous)
        try:
            assert isinstance([e async for e in first.stream("OLD")][-1], TurnCompleted)
            thread = first.thread_id
        finally:
            await first.aclose()
        changed = previous is not None and current is not None and previous != current
        second = await create(current, thread)
        try:
            assert isinstance([e async for e in second.stream("CURRENT")][-1], TurnCompleted)
            stored = await second._repository.load_items(thread)
            markers = [i for i in stored if isinstance(i, CompactionItem)]
            assert len(markers) == int(changed)
            assert len(requests) == (3 if changed else 2)
            if changed:
                assert markers[0].remote_payload_json is None
                assert "client_metadata" not in requests[1]
                assert "CURRENT" not in json.dumps(requests[1]["input"])
            assert "harness.previous_model" not in json.dumps(requests)
        finally:
            await second.aclose()
        third = await create(current, thread)
        try:
            before = len(requests)
            assert isinstance([e async for e in third.stream("NEXT")][-1], TurnCompleted)
            assert len(requests) == before + 1
        finally:
            await third.aclose()

    asyncio.run(scenario())
