"""Cold paged CLI projection keeps compaction copies and tool pairing separate."""

import asyncio
from dataclasses import replace
from io import StringIO

import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.ids import new_item_id, new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.sessions import TurnRecord, TurnStatus


@pytest.mark.parametrize("page_size", [1, 2, 3, 5])
def test_cold_cli_compaction_and_tool_pairs_across_pages(tmp_path, monkeypatch, page_size):
    async def scenario():
        class NoSampling:
            async def stream(self, request):
                raise AssertionError("display must not sample or execute tools")
                yield

            async def aclose(self):
                pass

        settings = CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False)
        database = tmp_path / "history.db"
        warm = LangGraphRuntime.create(
            settings=settings, database_path=database, model=NoSampling(), home_path=tmp_path
        )
        await warm._ensure_ready()
        thread, turn = warm.thread_id, new_turn_id()
        user = UserMessageItem("ORIGINAL_REQUEST", turn)
        call = ToolCallItem(ToolCall(new_tool_call_id(), "fixture_tool", {}), turn, new_step_id())
        result = ToolResultItem(call.call.id, call.call.name, "ORIGINAL_RESULT", turn)
        answer = AssistantMessageItem("ORIGINAL_ANSWER", turn, new_step_id())
        items = (
            user,
            call,
            AssistantMessageItem("BETWEEN_CALL_AND_RESULT", turn, new_step_id()),
            result,
            answer,
            CompactionItem("HIDDEN_SUMMARY", answer.id, turn, replacement_item_count=4),
            replace(user, id=new_item_id(), retained_from_id=user.id),
            replace(call, id=new_item_id()),
            replace(result, id=new_item_id(), content="HIDDEN_RESULT_COPY"),
            replace(answer, id=new_item_id(), content="HIDDEN_ANSWER_COPY"),
            AssistantMessageItem("AFTER_COMPACTION", turn, new_step_id()),
        )
        await warm._repository.save_turn(TurnRecord(turn, thread, TurnStatus.COMPLETED, ""))
        await warm._repository.append_items(thread, items)
        await warm.aclose()
        runtime = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            model=NoSampling(),
            home_path=tmp_path,
            thread_id=thread,
        )
        original = runtime.load_display_items_page

        async def small_page(**kwargs):
            return await original(**{**kwargs, "limit": page_size})

        monkeypatch.setattr(runtime, "load_display_items_page", small_page)

        class UI(TerminalUI):
            async def read_message(self):
                pager = self._history_view.loader
                assert pager.has_older
                saw_result_without_call = False
                while True:
                    shown = self._transcript.render(80)
                    assert "HIDDEN_" not in shown
                    assert "completion not confirmed" not in shown
                    kinds = {type(item) for item in pager.items}
                    saw_result_without_call |= ToolResultItem in kinds and ToolCallItem not in kinds
                    if not pager.has_older:
                        break
                    pager.request_older()
                    await pager.task
                if page_size == 1:
                    assert saw_result_without_call
                for marker in ("ORIGINAL_REQUEST", "ORIGINAL_RESULT", "ORIGINAL_ANSWER"):
                    assert shown.count(marker) == 1
                assert shown.index("ORIGINAL_REQUEST") < shown.index("ORIGINAL_RESULT")
                assert shown.index("ORIGINAL_RESULT") < shown.index("ORIGINAL_ANSWER")
                assert await runtime._repository.load_items(thread) == items
                raise EOFError

        ui = UI(settings, tmp_path / "input-history", console=Console(file=StringIO(), width=80))
        assert (
            await CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui).run() == 0
        )

    asyncio.run(scenario())
