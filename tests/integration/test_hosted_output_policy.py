import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core import LangGraphRuntime
from corki.models import OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.protocol.items import HostedToolItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["bytes", "tokens"])
@pytest.mark.parametrize("kind", ["notification", "web", "search_output"])
def test_hosted_raw_archive_and_model_projection_have_distinct_budgets(tmp_path, mode, kind):
    async def scenario():
        content = "HEAD" + "字" * 7000 + "TAIL"
        source = (
            {
                "type": "function_call_output",
                "id": "notification",
                "output": content,
                "_meta": {"fallback_token_limit_override": 1000000},
            }
            if kind == "notification"
            else {
                "type": "web_search_call",
                "id": "web",
                "action": {"type": "search", "query": content},
            }
            if kind == "web"
            else {
                "type": "tool_search_output",
                "id": "search",
                "status": "completed",
                "execution": "server",
                "call_id": None,
                "tools": [{"description": content}],
            }
        )
        requests = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            body = json.loads(request.content)
            requests.append(body)
            assert not any(i.get("type") == source["type"] for i in body["input"])
            candidates = [
                json.loads(i["content"].split("\n", 1)[1])
                for i in body["input"]
                if isinstance(i.get("content"), str)
                and i["content"].startswith("External hosted-tool event")
            ]
            assert len(candidates) == 1
            event = candidates[0]
            if kind == "notification":
                assert "truncated" in event["output"] and len(event["output"]) < 100
                assert event["output"].startswith("HEAD") and event["output"].endswith("TAIL")
                assert ("tokens truncated" in event["output"]) == (mode == "tokens")
            else:
                assert event == source  # The archive's id is data inside the quoted JSON.
            output = [
                {
                    "type": "message",
                    "id": f"m-{len(requests)}",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "summary"}],
                }
            ]
            return httpx.Response(
                200,
                text="data: "
                + json.dumps(
                    {
                        "type": "response.completed",
                        "response": {"id": f"r-{len(requests)}", "output": output},
                    }
                )
                + "\n\n",
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = OpenAIResponsesModel(
            api_key="test",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode="responses"),
                supports_native_tool_search=True,
            ),
        )
        settings = CorkiSettings(
            model="fixture",
            working_directory=tmp_path,
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode="native",
            memories_enabled=True,
            memories_generate=False,
            memories_background_enabled=False,
            memories_disable_on_external_context=True,
            model_contexts=parse_model_contexts(
                {
                    "fixture": {
                        "context_window": 65536,
                        "truncation_policy": {"mode": mode, "limit": 10},
                    }
                }
            ),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=model,
            registry=ToolRegistry(),
            memory_root=tmp_path / "memories",
        )
        try:
            # Legacy facts are imported from storage, never from a live native event.
            await runtime._ensure_ready()
            archived = HostedToolItem(json.dumps(source), "old-turn", "old-step")
            await runtime._repository.append_items(runtime.thread_id, (archived,))
            assert isinstance([e async for e in runtime.stream("receive")][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("use event")][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert len(requests) == 3
            assert not requests[-1].get("tools")
            original = [
                i
                for i in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(i, HostedToolItem)
            ]
            assert len(original) == 1 and json.loads(original[0].payload_json) == source
            assert original[0].model_payload_json is None
            assert original == [archived]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("override", [None, 1])
def test_large_notification_cold_recovery_projects_before_context_budgeting(
    tmp_path, partial, override
):
    from corki.models import ModelCompleted
    from corki.protocol.ids import new_thread_id, new_turn_id
    from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
    from corki.sessions import TurnRecord, TurnStatus
    from corki.storage import SQLiteSessionRepository

    async def scenario():
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "external"))
        await repository.append_items(thread, (UserMessageItem("external", turn),))
        source = HostedToolItem(
            json.dumps(
                {"type": "function_call_output", "output": "字" * 12000}, ensure_ascii=False
            ),
            turn,
            new_step_id(),
            fallback_token_limit_override=override,
        )
        if partial:
            await repository.append_partial_item(thread, turn, 0, source)
        else:
            await repository.commit_model_step(thread, turn, 0, ModelCompleted((source,)))

        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                projected = next(i for i in request.items if isinstance(i, HostedToolItem))
                assert projected.id == source.id and projected.payload_json == source.payload_json
                body = json.loads(projected.visible_payload_json)["output"]
                assert len(body) < 100 and "truncated" in body
                assert ("tokens truncated" in body) == (override is not None)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                model="fixture",
                working_directory=tmp_path,
                skills_enabled=False,
                model_contexts=parse_model_contexts(
                    {
                        "fixture": {
                            "context_window": 12000,
                            "truncation_policy": {"mode": "bytes", "limit": 10},
                        }
                    }
                ),
            ),
            database_path=database,
            registry=ToolRegistry(),
            model=model,
            thread_id=thread,
        )
        try:
            assert isinstance([e async for e in runtime.resume_pending()][-1], TurnCompleted)
            assert model.calls == int(partial)
            if not partial:
                assert isinstance([e async for e in runtime.stream("follow up")][-1], TurnCompleted)
                assert model.calls == 1
            stored = await runtime._repository.load_items(thread)
            assert [i for i in stored if isinstance(i, HostedToolItem)] == [source]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
