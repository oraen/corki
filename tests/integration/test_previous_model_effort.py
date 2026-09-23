import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import CompactionItem, UserMessageItem
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["v2", "legacy", "local"])
@pytest.mark.parametrize(
    "current,levels,default,expected",
    [
        ("ultra", ["low", "medium", "high"], "low", "medium"),
        ("high", ["low", "medium", "high"], "low", "high"),
        (None, ["low", "medium", "high"], "low", "medium"),
        ("ultra", ["low", "medium", "high", "xhigh"], "high", "medium"),
        ("ultra", [], "low", "low"),
        ("ultra", [], None, None),
        (None, [], None, None),
        ("custom", ["custom"], "low", "custom"),
        ("ultra", None, None, None),
        ("ultra", ["low", "max", "ultra"], "low", "max"),
        ("ultra", ["low", "high", "ultra"], "low", "high"),
        ("ultra", ["ultra"], None, "medium"),
        ("persistent", ["persistent"], None, "disabled"),
        ("ultra", [], "persistent", "disabled"),
        (None, [" "], None, " "),
    ],
)
def test_cold_previous_model_effort_is_resolved_without_changing_main(
    tmp_path,
    monkeypatch,
    mode,
    current,
    levels,
    default,
    expected,
    outcome="normal",
    tiers=None,
    lite=None,
):
    async def scenario():
        requests = []
        headers = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            body = json.loads(request.content)
            requests.append(body)
            headers.append(request.headers)
            compact = len(requests) == 2
            if compact and outcome == "failure":
                return httpx.Response(400, json={"error": {"code": "invalid_prompt"}})
            if compact and outcome == "cancel":
                raise asyncio.CancelledError
            output = {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "summary" if compact else "done"}],
            }
            events = []
            usage = 20000 if len(requests) == 1 else 10
            events.append(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r",
                        "output": [output],
                        "usage": {"input_tokens": usage, "output_tokens": 0, "total_tokens": usage},
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
        old = {"context_window": 100000}
        if levels is not None:
            old.update(
                supported_reasoning_levels=[{"effort": e} for e in levels],
                default_reasoning_level=default,
            )
        small = {"context_window": 10000}
        if lite is not None:
            old["use_responses_lite"], small["use_responses_lite"] = lite
        if tiers is not None:
            old["service_tiers"] = [{"id": "priority"}] if tiers[0] else []
            small["service_tiers"] = [{"id": "priority"}] if tiers[1] else []
        catalog = parse_model_contexts({"large": old, "small": small})

        async def create(model, thread=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    model=model,
                    model_contexts=catalog,
                    reasoning_effort=current,
                    service_tier="priority" if tiers is not None else None,
                    supports_service_tier=True if tiers is not None else None,
                    api_mode="responses",
                    provider_name="custom" if mode == "local" else "openai",
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                    skills_enabled=False,
                    remote_compaction_v2=mode != "legacy",
                    model_max_retries=0,
                    model_request_max_retries=0,
                ),
                database_path=tmp_path / "s.db",
                registry=ToolRegistry(),
                thread_id=thread,
            )

        first = await create("large")
        try:
            assert isinstance([e async for e in first.stream("OLD")][-1], TurnCompleted)
            thread = first.thread_id
        finally:
            await first.aclose()
        second = await create("small", thread)
        try:
            if outcome == "checkpoint":
                await second._ensure_ready()
                turn = new_turn_id()
                user = UserMessageItem("NEW", turn)
                await second._repository.save_turn(
                    TurnRecord(turn, thread, TurnStatus.RUNNING, "NEW")
                )

                class Sink:
                    async def emit(self, event):
                        pass

                await second._compiled.ainvoke(
                    _initial_state(thread, turn, second._settings, user),
                    config=second._graph_config(turn),
                    context=GraphRunContext(events=Sink()),
                    interrupt_before=["call_model"],
                )
                assert len(requests) == 2
                await second.aclose()
                second = await create("small", thread)
                assert isinstance([e async for e in second.resume_pending()][-1], TurnCompleted)
            elif outcome in ("failure", "cancel"):
                events = []
                if outcome == "cancel":
                    with pytest.raises(asyncio.CancelledError):
                        async for event in second.stream("NEW"):
                            events.append(event)
                else:
                    events = [e async for e in second.stream("NEW")]
                assert isinstance(events[-1], TurnFailed if outcome == "failure" else TurnCancelled)
                assert len(requests) == 2
                assert requests[1].get("reasoning", {}).get("effort") == expected
                assert second._model._reasoning_effort == current
                assert not any(
                    isinstance(item, CompactionItem)
                    for item in await second._repository.load_items(thread)
                )
                return
            else:
                assert isinstance([e async for e in second.stream("NEW")][-1], TurnCompleted)
            assert len(requests) == 3
            assert requests[1]["model"] == "large"
            assert "NEW" not in json.dumps(requests[1]["input"])
            assert requests[1].get("reasoning", {}).get("effort") == expected
            assert requests[2]["model"] == "small"
            if tiers is not None:
                assert requests[1].get("service_tier") == ("priority" if all(tiers) else None)
                assert requests[2].get("service_tier") == ("priority" if tiers[1] else None)
            current_wire = {"ultra": "medium", "persistent": "disabled"}.get(current, current)
            assert requests[2].get("reasoning", {}).get("effort") == current_wire
            assert isinstance([e async for e in second.stream("FOLLOWING")][-1], TurnCompleted)
            assert len(requests) == 4
            assert requests[3].get("reasoning", {}).get("effort") == current_wire
            markers = [
                item
                for item in await second._repository.load_items(thread)
                if isinstance(item, CompactionItem)
            ]
            assert len(markers) == 1 and markers[0].remote_payload_json is None
            assert "summary" in json.dumps(requests[2]["input"])
            if lite is not None:
                for body, header, _enabled in zip(
                    requests, headers, (lite[0], lite[0], lite[1], lite[1]), strict=True
                ):
                    assert header.get("x-openai-internal-codex-responses-lite") is None
                    assert body.get("reasoning", {}).get("context") is None
                    assert all(i.get("type") != "additional_tools" for i in body["input"])
                    assert "instructions" in body
        finally:
            await second.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["v2", "legacy", "local"])
@pytest.mark.parametrize("outcome", ["failure", "cancel", "checkpoint"])
def test_previous_model_effort_failure_cancel_and_cold_checkpoint(
    tmp_path, monkeypatch, mode, outcome
):
    test_cold_previous_model_effort_is_resolved_without_changing_main(
        tmp_path, monkeypatch, mode, "ultra", ["low", "medium", "high"], "low", "medium", outcome
    )


@pytest.mark.parametrize("mode", ["v2", "legacy", "local"])
@pytest.mark.parametrize("old,new", [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize("outcome", ["normal", "checkpoint"])
def test_previous_model_service_tier_uses_both_captured_and_old_model_support(
    tmp_path, monkeypatch, mode, old, new, outcome
):
    test_cold_previous_model_effort_is_resolved_without_changing_main(
        tmp_path,
        monkeypatch,
        mode,
        "ultra",
        ["low", "medium", "high"],
        "low",
        "medium",
        outcome,
        (old, new),
    )


@pytest.mark.parametrize("mode", ["v2", "legacy", "local"])
@pytest.mark.parametrize("old,new", [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize("outcome", ["normal", "checkpoint"])
def test_previous_model_lite_projection_is_model_owned_on_cold_restore(
    tmp_path, monkeypatch, mode, old, new, outcome
):
    test_cold_previous_model_effort_is_resolved_without_changing_main(
        tmp_path,
        monkeypatch,
        mode,
        "ultra",
        ["low", "medium", "high"],
        "low",
        "medium",
        outcome,
        lite=(old, new),
    )
