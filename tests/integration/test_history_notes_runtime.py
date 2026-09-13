import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, TokenBudgetConfig
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


class SequenceModel:
    def __init__(self, actions=()):
        self.actions, self.requests = list(actions), []

    async def stream(self, request):
        self.requests.append(request)
        turn = request.items[-1].turn_id
        if self.actions:
            action = self.actions.pop(0)
            name, arguments = action(request) if callable(action) else action
            item = ToolCallItem(ToolCall(new_tool_call_id(), name, arguments), turn, new_step_id())
        else:
            item = AssistantMessageItem("done", turn, new_step_id())
        yield ModelCompleted((item,))

    async def aclose(self):
        pass


@pytest.mark.parametrize("provider", ["openai", "independent"])
@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("tool_mode", ["direct", "code_mode_only"])
def test_official_configuration_still_uses_local_recovery(
    tmp_path, monkeypatch, tool_mode, provider, mode
):
    async def reject_http(*args, **kwargs):
        raise AssertionError("History/notes must not use account HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "send", reject_http)

    async def scenario():
        def read_found(request):
            result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
            found = json.loads(result.content)["items"][0]
            return "history::read_item", {
                "item_id": found["item_id"],
                "window_id": found["window_id"],
            }

        model = SequenceModel(
            [
                ("history::list_windows", {}),
                ("history::list_items", {"role": "user", "limit": 1, "recent_first": True}),
                read_found,
                ("history::search_contents", {"query": "MAIN_TASK", "role": "user"}),
                ("notes::write_file", {"path": "p", "text": "first\n"}),
                ("notes::append_to_file", {"path": "p", "text": "second\n"}),
                ("notes::list_files_by_prefix", {"prefix": "/root/notes"}),
                ("notes::search_contents", {"query": "second", "max_matches_per_file": 1}),
                ("notes::read_file", {"path": "p", "start_line": -1}),
            ]
        )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                token_budget_enabled=True,
                token_budget=TokenBudgetConfig(use_history_notes_extension=True),
                tool_mode=tool_mode,
                provider_name=provider,
                api_mode=mode,
                api_base="https://api.openai.com/v1",
                api_key="fixture",
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
        )
        try:
            assert isinstance([e async for e in runtime.stream("MAIN_TASK")][-1], TurnCompleted)
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert all(request.client_metadata is None for request in model.requests)
            assert len(results) == 9 and all(not r.is_error for r in results)
            assert (
                json.loads(results[0].content)["windows"][0]["window_id"]
                == f"{runtime.thread_id}:0"
            )
            assert json.loads(results[2].content)["text"] == "MAIN_TASK"
            assert json.loads(results[6].content)["files"][0]["path"] == "/root/notes/p"
            assert json.loads(results[7].content)["matches"][0]["line"] == 2
            assert json.loads(results[8].content)["text"] == "second\n"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
