import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.context import active_history
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, ContextItem, UserMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("append_failed", [False, True])
@pytest.mark.parametrize("include_catalog", [False, True])
def test_cold_prepare_recovery_preserves_injected_skill_even_when_file_changes(
    tmp_path, append_failed, include_catalog
):
    async def scenario():
        path = tmp_path / ".corki" / "skills" / "fixture" / "SKILL.md"
        path.parent.mkdir(parents=True)
        header = "---\nname: fixture\ndescription: fixture skill\n---\n\n"
        path.write_text(header + "ORIGINAL BODY", encoding="utf-8")
        settings = CorkiSettings(
            working_directory=tmp_path, skills_include_instructions=include_catalog
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        def create(thread_id=None):
            return LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "sessions.db",
                home_path=tmp_path / ".corki",
                registry=ToolRegistry(),
                model=Model(),
                thread_id=thread_id,
            )

        runtime = create()
        try:
            await runtime._ensure_ready()
            thread, turn = runtime.thread_id, new_turn_id()
            user = UserMessageItem("use $fixture", turn)
            await runtime._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
            )
            append = runtime._repository.append_items

            async def fail_after_commit(thread_id, items):
                await append(thread_id, items)
                if any(isinstance(i, ContextItem) and "ORIGINAL BODY" in i.content for i in items):
                    raise OSError("after input+skill commit")

            if append_failed:
                runtime._repository.append_items = fail_after_commit

            async def prepare():
                await runtime._compiled.ainvoke(
                    _initial_state(thread, turn, settings, user),
                    config=runtime._graph_config(turn),
                    context=GraphRunContext(events=Sink()),
                    interrupt_before=["call_model"],
                )

            if append_failed:
                with pytest.raises(OSError, match="after input"):
                    await prepare()
            else:
                await prepare()
            checkpoint = await runtime._compiled.aget_state(runtime._graph_config(turn))
            assert checkpoint.next == (
                ("prepare_model_context",) if append_failed else ("call_model",)
            )
            stored = await runtime._repository.load_items(thread)
            assert not requests
            await runtime.aclose()
            path.write_text(header + "CHANGED WHILE CLOSED", encoding="utf-8")
            runtime = create(thread)
            events = [e async for e in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 1
            visible_stored = active_history(stored)
            assert requests[0].items[: len(visible_stored)] == visible_stored
            catalog_states = [
                i
                for i in stored
                if isinstance(i, ContextItem) and i.key == "extensions.skills.catalog"
            ]
            assert len(catalog_states) == 1
            assert catalog_states[0].snapshot_state == (
                "skills.listed" if include_catalog else "skills.hidden"
            )
            if not include_catalog:
                assert catalog_states[0] not in requests[0].items
            selected = [
                i
                for i in requests[0].items
                if isinstance(i, ContextItem) and i.key.startswith("extensions.skills.selected.")
            ]
            assert len(selected) == 1 and "ORIGINAL BODY" in selected[0].content
            assert selected[0].source_input_id == user.id
            assert "CHANGED WHILE CLOSED" not in selected[0].content
            events = [e async for e in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert [
                i
                for i in requests[-1].items
                if isinstance(i, ContextItem) and i.key.startswith("extensions.skills.selected.")
            ] == selected
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("include_catalog", [False, True])
def test_selected_skill_wire_follows_user_and_omits_internal_input_identity(
    tmp_path, monkeypatch, api_mode, include_catalog
):
    async def scenario():
        path = tmp_path / ".corki" / "skills" / "fixture" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "---\nname: fixture\ndescription: fixture skill\n---\n\nWIRE SKILL BODY",
            encoding="utf-8",
        )
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            if api_mode == "responses":
                message = {
                    "type": "message",
                    "id": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                }
                data = {
                    "type": "response.completed",
                    "response": {"id": "response", "output": [message]},
                }
                text = "data: " + json.dumps(data) + "\n\n"
            else:
                data = {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}
                text = "data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(http_client, "OwnedHTTPClient", lambda **kwargs: client)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                api_mode=api_mode,
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
                skills_include_instructions=include_catalog,
            ),
            database_path=tmp_path / "sessions.db",
            home_path=tmp_path / ".corki",
            registry=ToolRegistry(),
        )
        try:
            for text in ("use $fixture", "continue"):
                events = [e async for e in runtime.stream(text)]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            key = "input" if api_mode == "responses" else "messages"
            initial, following = requests[0][key], requests[1][key]
            body_index = next(
                i for i, x in enumerate(initial) if "WIRE SKILL BODY" in json.dumps(x)
            )
            assert initial[body_index]["role"] == "user"
            assert "use $fixture" in json.dumps(initial[body_index - 1])
            assert following[: len(initial)] == initial
            wire = json.dumps(requests[-1])
            assert wire.count("WIRE SKILL BODY") == 1
            assert "source_input_id" not in wire and "snapshot_content" not in wire
            assert "snapshot_state" not in wire
            if not include_catalog:
                assert "<skills_instructions>" not in wire
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
