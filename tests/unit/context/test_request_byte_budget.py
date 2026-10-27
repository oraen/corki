"""Byte-triggered model-switch compaction; no real model or user state."""
import asyncio
import json
from copy import copy

import pytest

from corki.context import ContextSnapshot, ContextWindowManager
from corki.models.base import ModelError
from corki.models.request_budget import RequestByteBudget
from corki.models.types import ModelCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, CompactionItem, UserMessageItem, new_step_id
from corki.protocol.tools import ToolSpec
from corki.storage import SQLiteSessionRepository


class BudgetModel:
    def __init__(self, summary="short summary"):
        self.checks = []
        self.requests = []
        self.summary = summary

    def measure(self, request):
        fixed = len(request.instructions.encode()) + len(json.dumps([
            tool.as_chat_completion_tool() for tool in request.tools
        ]).encode())
        total = fixed + sum(len(getattr(item, "content", getattr(item, "summary", "")).encode())
                            for item in request.items)
        return RequestByteBudget(total, fixed, 4000)

    async def request_byte_budget(self, request):
        self.checks.append(request)
        return self.measure(request)

    async def stream(self, request):
        assert not self.measure(request).exceeded, "Never send an oversized summary"
        self.requests.append(request)
        yield ModelCompleted((AssistantMessageItem(self.summary, request.items[-1].turn_id, new_step_id()),))


async def fixture(tmp_path, model):
    repository = SQLiteSessionRepository(tmp_path / "budget.db")
    thread = new_thread_id()
    await repository.create_thread(thread, tmp_path)
    old_turn = new_turn_id()
    original = (UserMessageItem("old request", old_turn),
                AssistantMessageItem("x" * 8000, old_turn, new_step_id()))
    await repository.append_items(thread, original)
    window = ContextWindowManager(repository=repository, model=model, model_name="target",
                                  context_window_tokens=100000, provider_id="provider-a")
    return repository, thread, original, window


def test_switch_checks_compacts_bounds_summary_and_skips_ordinary_steps(tmp_path):
    async def run():
        model = BudgetModel()
        repository, thread, original, window = await fixture(tmp_path, model)
        try:
            turn = new_turn_id()
            request = UserMessageItem("current request", turn)
            prepared = await window.prepare(thread_id=thread, turn_id=turn,
                snapshot=ContextSnapshot("base", (), tmp_path), tools=(), pending_items=(request,))
            assert prepared.compacted and len(model.requests) == 1
            assert any(isinstance(i, CompactionItem) for i in prepared.items)
            assert any(isinstance(i, UserMessageItem) and i.content == "current request" for i in prepared.items)
            assert (await repository.load_items(thread))[:2] == original
            assert len(model.checks) > 2  # initial, summary retries, compacted request
            count = len(model.checks)
            # Even growth beyond the byte limit is not checked on ordinary turns.
            await repository.append_items(thread, (AssistantMessageItem("y" * 8000, turn, new_step_id()),))
            await window.prepare(thread_id=thread, turn_id=new_turn_id(),
                snapshot=ContextSnapshot("base", (), tmp_path), tools=())
            assert len(model.checks) == count
            # Turn-owned copies share the same successful switch receipt.
            same = copy(window)
            await same.prepare(thread_id=thread, turn_id=new_turn_id(),
                snapshot=ContextSnapshot("base", (), tmp_path), tools=())
            assert len(model.checks) == count
            # Provider changes count even with the same model slug.
            same._provider_id = "provider-b"
            again = await same.prepare(thread_id=thread, turn_id=new_turn_id(),
                snapshot=ContextSnapshot("base", (), tmp_path), tools=())
            assert again.compacted and len(model.checks) > count
        finally:
            await repository.close()
    asyncio.run(run())


@pytest.mark.parametrize("large", ["instructions", "tools"])
def test_fixed_prefix_overflow_fails_without_summary_or_history_mutation(tmp_path, large):
    async def run():
        model = BudgetModel()
        repository, thread, original, window = await fixture(tmp_path, model)
        try:
            tools = (ToolSpec("echo", "x" * 5000, {"type": "object"}),) if large == "tools" else ()
            with pytest.raises(ModelError, match="系统提示词和工具定义") as failure:
                await window.prepare(thread_id=thread, turn_id=new_turn_id(),
                    snapshot=ContextSnapshot("x" * 5000 if large == "instructions" else "base", (), tmp_path),
                    tools=tools)
            assert failure.value.status_code == 413 and not failure.value.retryable
            assert not model.requests
            assert await repository.load_items(thread) == original
        finally:
            await repository.close()
    asyncio.run(run())


def test_oversized_summary_result_is_rechecked_before_committing(tmp_path):
    async def run():
        model = BudgetModel(summary="z" * 6000)
        repository, thread, original, window = await fixture(tmp_path, model)
        try:
            with pytest.raises(ModelError, match="压缩后的请求仍超过"):
                await window.prepare(thread_id=thread, turn_id=new_turn_id(),
                    snapshot=ContextSnapshot("base", (), tmp_path), tools=())
            assert len(model.requests) == 1
            assert await repository.load_items(thread) == original
        finally:
            await repository.close()
    asyncio.run(run())
