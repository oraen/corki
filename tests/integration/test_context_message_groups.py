"""Context message boundaries are recorded once, not inferred from later adjacency."""

import asyncio
import json
import re
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.prompting import PromptContribution, PromptRole, PromptSlot
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


def sse(items):
    events = [{"type": "response.output_item.done", "item": item} for item in items]
    events.append({"type": "response.completed", "response": {"id": "r", "output": items}})
    return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))


def fixture_groups(body, version):
    groups = []
    for item in body.get("input", body.get("messages", [])):
        content = item.get("content") or []
        texts = [content] if isinstance(content, str) else [p.get("text", "") for p in content]
        names = [name for text in texts for name in re.findall(r"FRAGMENT:(\w+):" + version, text)]
        if names:
            groups.append((item["role"], names))
    return groups


@pytest.mark.parametrize(
    "api,mode",
    [("responses", m) for m in ("none", "local", "local_overflow", "reset", "v2", "legacy")]
    + [("chat_completions", m) for m in ("none", "local", "reset")],
)
@pytest.mark.parametrize("full_permissions", [False, True])
def test_runtime_initial_delta_standalone_compaction_and_cold_groups(
    tmp_path, api, mode, full_permissions
):
    async def scenario():
        version, bodies, executions = ["one"], [], []
        skill = tmp_path / ".corki/skills/fixture/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: fixture\ndescription: fixture\n---\nSELECTED SKILL BODY")
        memory = tmp_path / ".corki/memories/memory_summary.md"
        memory.parent.mkdir(parents=True)
        memory.write_text("MEMORY_ORDER_PROOF:one")

        class Contributor:
            def contributions(self, **kwargs):
                return tuple(
                    PromptContribution(
                        "fixture." + name,
                        "memory/consolidation_inputs",
                        role,
                        PromptSlot.EXTENSIONS,
                        variables={"inputs": f"FRAGMENT:{name}:{version[0]}"},
                        separate_message=name == "ds",
                        content_kind="fixture." + name,
                    )
                    for name, role in (
                        ("d1", PromptRole.DEVELOPER),
                        ("d2", PromptRole.DEVELOPER),
                        ("u1", PromptRole.USER),
                        ("d3", PromptRole.DEVELOPER),
                        ("ds", PromptRole.DEVELOPER),
                        ("d4", PromptRole.DEVELOPER),
                    )
                )

        class Change:
            spec = ToolSpec("change", "change fixture context", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                version[0] = "two"
                memory.write_text("MEMORY_ORDER_PROOF:two")
                return ToolResult(call.id, call.name, "changed")

        def respond(request):
            body = json.loads(request.content)
            bodies.append(body)
            if len(bodies) == 3 and mode == "local_overflow":
                return httpx.Response(
                    200,
                    text='data: {"type":"response.failed","response":{"error":'
                    '{"code":"context_length_exceeded","message":"too big"}}}\n\n',
                )
            if len(bodies) == 3 and mode == "legacy":
                return httpx.Response(
                    200, json={"output": [{"type": "compaction", "encrypted_content": "opaque"}]}
                )
            if len(bodies) == 3 and mode == "v2":
                return sse([{"type": "compaction", "encrypted_content": "opaque"}])
            first = len(bodies) == 1
            if api == "responses":
                return sse(
                    [
                        {
                            "type": "function_call",
                            "call_id": "call",
                            "name": "change",
                            "arguments": "{}",
                        }
                    ]
                    if first
                    else [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ]
                )
            delta = (
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call",
                            "type": "function",
                            "function": {"name": "change", "arguments": "{}"},
                        }
                    ]
                }
                if first
                else {"content": "done"}
            )
            event = {
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": "tool_calls" if first else "stop"}
                ]
            }
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Change())
            cls = OpenAIResponsesModel if api == "responses" else OpenAICompatibleModel
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    api_mode=api,
                    model="fixture",
                    collaboration_mode="plan",
                    collaboration_instructions="MODE_ORDER_PROOF",
                    include_permissions_instructions=full_permissions,
                    memories_enabled=True,
                    memories_generate=False,
                    memories_background_enabled=False,
                    context_window_tokens=100000,
                    auto_compact_tokens=90000,
                    token_budget_enabled=mode == "reset",
                    remote_compaction_v2=mode != "legacy",
                ),
                registry=registry,
                database_path=tmp_path / "s.db",
                home_path=tmp_path / ".corki",
                thread_id=thread,
                context_contributors=(Contributor(),),
                model=cls(
                    api_key="fixture",
                    base_url="https://fixture.invalid/v1",
                    client=client,
                    capabilities=replace(
                        resolve_capabilities(
                            base_url="https://fixture.invalid/v1",
                            api_mode=api,
                            provider_name="openai",
                        ),
                        supports_remote_compaction=mode in {"v2", "legacy"},
                    ),
                ),
            )

        runtime = await create()
        try:
            assert isinstance([e async for e in runtime.stream("use $fixture")][-1], TurnCompleted)
            developer = "developer" if api == "responses" else "system"
            initial = [(developer, ["d1", "d2", "d3", "d4"]), (developer, ["ds"]), ("user", ["u1"])]
            delta = [
                (developer, ["d1", "d2"]),
                ("user", ["u1"]),
                (developer, ["d3"]),
                (developer, ["ds"]),
                (developer, ["d4"]),
            ]
            assert fixture_groups(bodies[0], "one") == initial
            initial_text = json.dumps(bodies[0])
            assert initial_text.index("MEMORY_ORDER_PROOF:one") < initial_text.index(
                "<skills_instructions>"
            )
            if full_permissions:
                assert (
                    initial_text.index("<skills_instructions>")
                    < initial_text.index("<permissions instructions>")
                    < initial_text.index("MODE_ORDER_PROOF")
                )
            else:
                assert initial_text.index("MODE_ORDER_PROOF") < initial_text.index(
                    "<skills_instructions>"
                )
            assert initial_text.index("FRAGMENT:d4:one") < initial_text.index("MODE_ORDER_PROOF")
            assert fixture_groups(bodies[1], "two") == delta
            key = "input" if api == "responses" else "messages"
            assert bodies[1][key][: len(bodies[0][key])] == bodies[0][key]
            selected = [i for i in bodies[0][key] if "SELECTED SKILL BODY" in json.dumps(i)]
            assert len(selected) == 1 and selected[0]["role"] == "user"
            assert "FRAGMENT:" not in json.dumps(selected[0])
            if mode != "none":
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
                if mode != "reset":
                    assert fixture_groups(bodies[2], "one") == initial
                    assert fixture_groups(bodies[2], "two") == delta
                    if mode == "local_overflow":
                        assert fixture_groups(bodies[3], "one") == initial[1:]
                        assert fixture_groups(bodies[3], "two") == delta
                assert isinstance(
                    [e async for e in runtime.stream("fresh window")][-1], TurnCompleted
                )
                assert fixture_groups(bodies[-1], "two") == initial
                rebuilt_text = json.dumps(bodies[-1])
                assert rebuilt_text.index("MEMORY_ORDER_PROOF:two") < rebuilt_text.index(
                    "<skills_instructions>"
                )
                assert rebuilt_text.index("FRAGMENT:d4:two") < rebuilt_text.index(
                    "MODE_ORDER_PROOF"
                )
                assert fixture_groups(bodies[-1], "one") == []
                assert "SELECTED SKILL BODY" not in json.dumps(bodies[-1])
            thread = runtime.thread_id
            before = await runtime._repository.load_items(thread)
            prior_body = bodies[-1]
            await runtime.aclose()
            runtime = await create(thread)
            assert isinstance(
                [e async for e in runtime.stream("cold unchanged")][-1], TurnCompleted
            )
            assert bodies[-1][key][: len(prior_body[key])] == prior_body[key]
            assert (await runtime._repository.load_items(thread))[: len(before)] == before
            assert executions == ["call"]
            for item in before:
                if isinstance(item, ContextItem) and not item.is_snapshot_only:
                    assert item.message_group_id is not None
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
