"""Full memory policy and use gate in actual HTTP requests, not a detached renderer."""

import asyncio
import hashlib
import json
import re
import shlex
import sys
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core import LangGraphRuntime
from corki.memory.sqlite import SQLiteMemoryRepository
from corki.models import (
    ModelCompleted,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tool_names import compatible_tool_name
from corki.protocol.tools import ToolCall
from corki.storage import SQLiteSessionRepository

POLICY = (
    "If unsure, do a quick memory pass.",
    "ideally <= 4-6 search steps before main work.",
    "Consider both risk of drift and verification effort.",
    "Do not present unverified memory-derived facts as confirmed-current.",
    "Never include memory citations inside pull-request messages.",
    "Never cite blank lines; double-check ranges.",
    "<oai-mem-citation>",
    "<rollout_ids>",
    "Do not try to edit the memory files yourself",
)


def message(text):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


@pytest.mark.parametrize("mode", ["chat", "responses", "lite"])
@pytest.mark.parametrize("case", ["enabled", "unused", "no_feature", "shell_only", "missing"])
def test_read_policy_gate_reaches_wire_and_unknown_tool_observation(tmp_path, mode, case):
    async def scenario():
        bodies = []
        root = tmp_path / "memories"
        root.mkdir()
        if case != "missing":
            (root / "memory_summary.md").write_text(
                "INDEX: repository verification\n", encoding="utf-8"
            )
        (root / "MEMORY.md").write_text("durable verification fact\n", encoding="utf-8")
        enabled, use, dedicated = case != "no_feature", case != "unused", case != "shell_only"
        expected_tools = enabled and use and dedicated
        expected_prompt = enabled and use and case != "missing"
        mode_name = "chat_completions" if mode == "chat" else "responses"
        # This is deliberately distinct from database_path, which can also name
        # the host's checkpoint/config location when a repository is supplied.
        source_db = tmp_path / "actual source.db"
        repository = SQLiteSessionRepository(source_db)

        def handle(request):
            bodies.append(json.loads(request.content))
            first = len(bodies) == 1
            if mode == "chat":
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "read-call",
                                "type": "function",
                                "function": {
                                    "name": compatible_tool_name("memories::read"),
                                    "arguments": '{"path":"MEMORY.md"}',
                                },
                            }
                        ]
                    }
                    if first
                    else {"content": "done"}
                )
                packet = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": "tool_calls" if first else "stop",
                        }
                    ]
                }
                return httpx.Response(
                    200, text="data: " + json.dumps(packet) + "\n\ndata: [DONE]\n\n"
                )
            output = (
                [
                    {
                        "type": "function_call",
                        "call_id": "read-call",
                        "name": compatible_tool_name("memories::read"),
                        "arguments": '{"path":"MEMORY.md"}',
                    }
                ]
                if first
                else [message("done")]
            )
            packet = {"type": "response.completed", "response": {"id": "r", "output": output}}
            return httpx.Response(200, text="data: " + json.dumps(packet) + "\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        adapter = OpenAICompatibleModel if mode == "chat" else OpenAIResponsesModel
        model = adapter(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode=mode_name, provider_name="openai"
            ),
        )
        settings = CorkiSettings(
            tmp_path,
            model="fixture",
            api_mode=mode_name,
            skills_enabled=False,
            memories_enabled=enabled,
            memories_use=use,
            memories_generate=False,
            memories_background_enabled=False,
            memories_dedicated_tools=dedicated,
            model_max_retries=0,
            model_contexts=parse_model_contexts(
                {"fixture": {"context_window": 272000, "use_responses_lite": mode == "lite"}}
            ),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "host.db",
            repository=repository,
            memory_repository=SQLiteMemoryRepository(source_db),
            model=model,
            memory_root=root,
        )
        try:
            events = [e async for e in runtime.stream("recall the verification command")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(bodies) == 2
            tools = json.dumps(bodies[0].get("tools", bodies[0].get("input", [])))
            assert (compatible_tool_name("memories::read") in tools) == expected_tools
            history = await repository.load_items(runtime.thread_id)
            contexts = [
                i for i in history if isinstance(i, ContextItem) and i.key == "memory.instructions"
            ]
            assert bool(contexts) == expected_prompt
            if expected_prompt:
                content = contexts[0].content
                assert contexts[0].content_kind == "memories.instructions"
                assert contexts[0].role.value == "developer"
                for clause in POLICY:
                    assert clause in content
                restored_template = (
                    content.replace(str(root), "#{memory_root}")
                    .replace(json.dumps(str(source_db), ensure_ascii=False), "#{history_database}")
                    .replace("INDEX: repository verification", "#{memory_summary}")
                )
                assert hashlib.sha256(restored_template.encode()).hexdigest() == (
                    "39c556c17e472986544e7e59fd0bfc6176a7d784c100955711305a661cb7e7f5"
                )
                assert str(source_db) in content
                assert "conversation_items" in content and "payload_json" in content
                assert "mode=ro" in content and "thread_id" in content
                assert "These files are append-only `jsonl`" not in content
                assert content.count("========= MEMORY_SUMMARY BEGINS =========") == 1
                assert "INDEX: repository verification" in content
                wire = bodies[0]["messages" if mode == "chat" else "input"]
                fragment = next(i for i in wire if "Never cite blank lines" in json.dumps(i))
                assert fragment["role"] == ("system" if mode == "chat" else "developer")
            result = next(i for i in history if isinstance(i, ToolResultItem))
            assert result.is_error is not expected_tools
            if expected_tools:
                assert "durable verification fact" in result.content
            else:
                assert "durable verification fact" not in result.content
            assert json.dumps(result.content, ensure_ascii=False) in json.dumps(
                bodies[1], ensure_ascii=False
            )
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_cold_use_toggle_disables_tools_without_rewriting_current_window(tmp_path):
    async def scenario():
        class Model:
            requests = []

            async def stream(self, request):
                self.requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        root = tmp_path / "memories"
        root.mkdir()
        (root / "memory_summary.md").write_text("INDEX", encoding="utf-8")
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            memories_enabled=True,
            memories_generate=False,
            memories_background_enabled=False,
            memories_dedicated_tools=True,
        )
        database = tmp_path / "s.db"
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=settings, database_path=database, model=model, memory_root=root
        )
        try:
            events = [e async for e in runtime.stream("first")]
            assert isinstance(events[-1], TurnCompleted)
            history = await runtime._repository.load_items(runtime.thread_id)
            original = next(
                i for i in history if isinstance(i, ContextItem) and i.key == "memory.instructions"
            )
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold = await LangGraphRuntime.acreate(
            settings=replace(settings, memories_use=False),
            database_path=database,
            model=model,
            memory_root=root,
            thread_id=thread,
        )
        try:
            events = [e async for e in cold.stream("no memory")]
            assert isinstance(events[-1], TurnCompleted)
            assert not any(
                t.name.startswith(("memory_", "memories::")) for t in model.requests[-1].tools
            )
            stored = await cold._repository.load_items(thread)
            assert original in stored
            fragments = [
                i for i in stored if isinstance(i, ContextItem) and i.key == "memory.instructions"
            ]
            assert fragments == [original]
            assert isinstance([e async for e in cold.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in cold.stream("new window")][-1], TurnCompleted)
            assert not any(
                isinstance(i, ContextItem) and i.key == "memory.instructions"
                for i in model.requests[-1].items
            )
            assert not any(
                t.name.startswith(("memory_", "memories::")) for t in model.requests[-1].tools
            )
            assert (await cold._repository.load_items(thread))[: len(stored)] == stored
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("dedicated", [False, True])
def test_progressive_read_uses_real_shell_for_thread_scoped_raw_archive(tmp_path, dedicated):
    async def scenario():
        source_db = tmp_path / "source ' archive.db"
        repository = SQLiteSessionRepository(source_db)
        source = new_thread_id()
        await repository.create_thread(source, tmp_path)
        original = UserMessageItem("ARCHIVE_EXACT: run pytest tests/smoke.py -q", new_turn_id())
        await repository.append_items(source, (original,))
        root = tmp_path / "memories"
        root.mkdir()
        (root / "memory_summary.md").write_text(
            "INDEX: verification in MEMORY.md", encoding="utf-8"
        )
        memory = f"verification evidence: thread_id: {source}\n"
        (root / "MEMORY.md").write_text(memory, encoding="utf-8")

        class Model:
            requests = []

            async def stream(self, request):
                self.requests.append(request)
                index = len(self.requests)
                turn, step = request.items[-1].turn_id, new_step_id()
                policy = next(
                    i.content
                    for i in request.items
                    if isinstance(i, ContextItem) and i.key == "memory.instructions"
                )
                assert "mode=ro" in policy
                locator = re.search(r"SQLite archive[^\n]*\n\s*(\"[^\n]*\")", policy)
                assert locator is not None
                path = json.loads(locator[1])
                assert path == str(source_db)
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                if index == 1:
                    if dedicated:
                        name, args = "memories::search", {"queries": ["verification"]}
                    else:
                        name, args = (
                            "exec_command",
                            {
                                "cmd": shlex.join(
                                    ["rg", "-n", "verification", str(root / "MEMORY.md")]
                                ),
                                "login": False,
                            },
                        )
                elif dedicated and index == 2:
                    assert not results[-1].is_error and str(source) in results[-1].content
                    name, args = "memories::read", {"path": "MEMORY.md"}
                elif index == (3 if dedicated else 2):
                    assert not results[-1].is_error and str(source) in results[-1].content
                    # Real built-in shell, real readonly SQLite connection. This
                    # is the advertised compatibility path, not a fake history tool.
                    probe = (
                        "import sqlite3,json; from pathlib import Path; "
                        + f"db=sqlite3.connect(Path({path!r}).as_uri()+'?mode=ro',uri=True); "
                        + "rows=db.execute('SELECT kind,payload_json FROM conversation_items "
                        "WHERE thread_id=? AND sequence>=? ORDER BY sequence LIMIT ?',"
                        + repr((str(source), 0, 20))
                        + ").fetchall(); "
                        + "print('ARCHIVE='+json.dumps(rows)); db.close()"
                    )
                    name, args = (
                        "exec_command",
                        {
                            "cmd": shlex.join([sys.executable, "-I", "-S", "-c", probe]),
                            "login": False,
                            "yield_time_ms": 1000,
                        },
                    )
                else:
                    assert index == (4 if dedicated else 3)
                    assert not results[-1].is_error and "ARCHIVE_EXACT" in results[-1].content
                    block = (
                        "<oai-mem-citation><citation_entries>MEMORY.md:1-1|note=[source routing]"
                        f"</citation_entries><rollout_ids>{source}</rollout_ids></oai-mem-citation>"
                    )
                    yield ModelCompleted(
                        (AssistantMessageItem("verified from archive" + block, turn, step),)
                    )
                    return
                yield ModelCompleted(
                    (ToolCallItem(ToolCall(new_tool_call_id(), name, args), turn, step),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_dedicated_tools=dedicated,
            ),
            database_path=tmp_path / "host.db",
            repository=repository,
            memory_repository=SQLiteMemoryRepository(source_db),
            memory_root=root,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("verify the earlier command")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "verified from archive"
            assert await repository.load_items(source) == (original,)
            assert (root / "MEMORY.md").read_text() == memory
            answers = [
                i
                for i in await repository.load_items(runtime.thread_id)
                if isinstance(i, AssistantMessageItem)
            ]
            assert answers[-1].memory_citation.thread_ids == (source,)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_custom_repository_does_not_claim_the_configured_sqlite_locator(tmp_path):
    async def scenario():
        db = tmp_path / "private-backend.db"
        actual = SQLiteSessionRepository(db)

        class CustomRepository:
            def __getattr__(self, name):
                return getattr(actual, name)

        class Model:
            async def stream(self, request):
                policy = next(
                    i.content
                    for i in request.items
                    if isinstance(i, ContextItem) and i.key == "memory.instructions"
                )
                assert "    unavailable\n" in policy and str(db) not in policy
                assert "Do not guess its backing file" in policy
                yield ModelCompleted(
                    (AssistantMessageItem("unverified", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        root = tmp_path / "memories"
        root.mkdir()
        (root / "memory_summary.md").write_text("INDEX", encoding="utf-8")
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
            ),
            database_path=tmp_path / "host.db",
            repository=CustomRepository(),
            memory_repository=SQLiteMemoryRepository(db),
            memory_root=root,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("recall")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_summary_budget_and_window_refresh_preserve_policy_and_old_context(tmp_path):
    async def scenario():
        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        root = tmp_path / "memories"
        root.mkdir()
        summary = root / "memory_summary.md"
        summary.write_text("HEAD " + "padding " * 100 + " TAIL", encoding="utf-8")
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_summary_token_limit=4,
            ),
            database_path=tmp_path / "s.db",
            memory_root=root,
            model=Model(),
        )
        try:
            for prompt in ("first", "unchanged"):
                events = [e async for e in runtime.stream(prompt)]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            stored = await runtime._repository.load_items(runtime.thread_id)
            fragments = [
                i for i in stored if isinstance(i, ContextItem) and i.key == "memory.instructions"
            ]
            assert len(fragments) == 1
            original = fragments[0]
            assert (
                "tokens truncated" in original.content and "padding " * 100 not in original.content
            )
            assert "HEAD" in original.content and "TAIL" in original.content
            assert all(clause in original.content for clause in POLICY)
            summary.write_text("new summary", encoding="utf-8")
            events = [e async for e in runtime.stream("refresh")]
            assert isinstance(events[-1], TurnCompleted)
            unchanged = await runtime._repository.load_items(runtime.thread_id)
            assert [
                i
                for i in unchanged
                if isinstance(i, ContextItem) and i.key == "memory.instructions"
            ] == [original]
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("new window")][-1], TurnCompleted)
            summary.unlink()
            events = [e async for e in runtime.stream("missing")]
            assert isinstance(events[-1], TurnCompleted)
            stored = await runtime._repository.load_items(runtime.thread_id)
            fragments = [
                i for i in stored if isinstance(i, ContextItem) and i.key == "memory.instructions"
            ]
            assert len(fragments) == 2 and fragments[0] == original
            assert "new summary" in fragments[1].content
            assert all(clause in fragments[1].content for clause in POLICY)
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("empty window")][-1], TurnCompleted)
            after = await runtime._repository.load_items(runtime.thread_id)
            assert after[: len(stored)] == stored
            assert [
                i for i in after if isinstance(i, ContextItem) and i.key == "memory.instructions"
            ] == fragments
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
