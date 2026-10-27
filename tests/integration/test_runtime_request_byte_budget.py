"""Real Agent + Chat transport, with a synthetic local budget/model server."""
import asyncio
import json

import httpx

from corki import http_client
from corki.config import CorkiSettings
from corki.context.model_transition import model_snapshot
from corki.core import LangGraphRuntime
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.tools import ToolRegistry


def test_real_agent_switch_compaction_and_ordinary_turns(tmp_path, monkeypatch):
    async def run():
        calls, summaries = [], []
        limit = 50000

        def respond(request):
            payload = json.loads(request.content)
            calls.append(request.url.path)
            if request.url.path.endswith("/request-budget"):
                fixed = {**payload, "messages": [m for m in payload["messages"] if m["role"] == "system"]}
                return httpx.Response(200, json={"protocol_version": 1,
                    "request_bytes": len(request.content),
                    "fixed_bytes": len(json.dumps(fixed, ensure_ascii=False, separators=(",", ":")).encode()),
                    "limit_bytes": limit})
            assert len(request.content) <= limit
            compact = "CONTEXT CHECKPOINT COMPACTION" in str(payload["messages"][-1].get("content", ""))
            if compact:
                summaries.append(payload)
            data = {"id": "fixture-completion", "choices": [{"index": 0,
                "delta": {"content": "short summary" if compact else "done"}, "finish_reason": "stop"}]}
            return httpx.Response(200, text="data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n")

        client = httpx.AsyncClient
        monkeypatch.setattr(http_client, "OwnedHTTPClient", lambda *a, **kw:
            client(*a, **kw, transport=httpx.MockTransport(respond)))
        settings = CorkiSettings(working_directory=tmp_path, model="auto", provider_name="fixture",
            api_base="http://127.0.0.1:17001/qoder", api_key="fixture-only", api_mode="chat_completions",
            base_instructions="Follow the user request.", context_window_tokens=300000,
            skills_enabled=False, goals_enabled=False, memories_use=False, memories_generate=False)
        database = tmp_path / "sessions.db"
        runtime = await LangGraphRuntime.acreate(settings=settings, database_path=database,
            home_path=tmp_path, registry=ToolRegistry(), load_plugins=False)
        thread = runtime.thread_id
        try:
            await runtime._ensure_ready()
            old_turn = new_turn_id()
            original = (model_snapshot(ModelContextInfo("old", 300000), old_turn, provider="other"),
                UserMessageItem("old request", old_turn),
                AssistantMessageItem("x" * 120000, old_turn, new_step_id()))
            await runtime._repository.append_items(thread, original)
            events = [event async for event in runtime.stream("CURRENT INPUT")]
            assert isinstance(events[-1], TurnCompleted)
            assert any(isinstance(event, ContextCompacted) for event in events)
            assert len(summaries) == 1
            assert (await runtime._repository.load_items(thread))[:3] == original
            checks = calls.count("/qoder/request-budget")
            assert checks > 2
            events = [event async for event in runtime.stream("NEXT INPUT")]
            assert isinstance(events[-1], TurnCompleted)
            assert calls.count("/qoder/request-budget") == checks
        finally:
            await runtime.aclose()

        # A fresh runtime uses the durable accepted-model marker, not a new probe.
        restored = await LangGraphRuntime.acreate(settings=settings, database_path=database,
            home_path=tmp_path, thread_id=thread, registry=ToolRegistry(), load_plugins=False)
        try:
            events = [event async for event in restored.stream("RESTORED INPUT")]
            assert isinstance(events[-1], TurnCompleted)
            assert calls.count("/qoder/request-budget") == checks
        finally:
            await restored.aclose()
    asyncio.run(run())
