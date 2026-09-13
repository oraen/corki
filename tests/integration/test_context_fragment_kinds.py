"""Producer-owned classifications survive actual model/tool/history boundaries."""

import asyncio
import json
from dataclasses import dataclass, replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory.context import MemoryContextContributor
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.prompting import PromptContribution, PromptRole, PromptSlot
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem
from corki.protocol.tools import ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry

META = "internal_chat_message_metadata_passthrough"


def response(output):
    events = [{"type": "response.output_item.done", "item": item} for item in output]
    events.append({"type": "response.completed", "response": {"id": "r", "output": output}})
    return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))


@pytest.mark.parametrize("mode", ["none", "v2", "legacy", "local", "chat"])
@pytest.mark.parametrize("provider,kinds", [("openai", True), ("openai", False), ("custom", True)])
def test_fragment_producers_reach_runtime_history_and_compaction(tmp_path, mode, provider, kinds):
    async def scenario():
        rules = tmp_path / "AGENTS.md"
        rules.write_text("ORIGINAL RULE")
        skill = tmp_path / ".corki/skills/fixture/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: fixture\ndescription: fixture\n---\nSELECTED BODY")
        memories = tmp_path / "memories"
        memories.mkdir()
        (memories / "memory_summary.md").write_text("MEMORY INDEX")
        bodies, calls = [], []
        summary = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "done"}],
        }

        def respond(request):
            body = json.loads(request.content)
            bodies.append(body)
            if request.url.path.endswith("/chat/completions"):
                return httpx.Response(
                    200,
                    text=(
                        'data: {"choices":[{"index":0,"delta":{"content":"done"},'
                        '"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                    ),
                )
            if len(bodies) == 1:
                return response(
                    [
                        {
                            "type": "function_call",
                            "name": "change",
                            "call_id": "one",
                            "arguments": "{}",
                        }
                    ]
                )
            if len(bodies) == 3 and mode in {"v2", "legacy", "local"}:
                if mode == "legacy":
                    return httpx.Response(
                        200,
                        json={"output": [{"type": "compaction", "encrypted_content": "opaque"}]},
                    )
                if mode == "v2":
                    return response([{"type": "compaction", "encrypted_content": "opaque"}])
            return response([summary])

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        class Change:
            spec = ToolSpec("change", "fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call.id)
                rules.write_text("UPDATED RULE")
                return ToolResult(call.id, call.name, "changed")

        class Deferred:
            spec = ToolSpec(
                "fixture::later", "deferred", {"type": "object"}, exposure=ToolExposure.DEFERRED
            )

            async def execute(self, call, context):
                raise AssertionError("not discovered or called")

        def create(thread=None, chat=False):
            registry = ToolRegistry()
            registry.register(Change())
            registry.register(Deferred())
            api_mode = "chat_completions" if chat else "responses"
            caps = replace(
                resolve_capabilities(
                    base_url="https://fixture.invalid/v1", api_mode=api_mode, provider_name=provider
                ),
                supports_remote_compaction=mode != "local",
            )
            cls = OpenAICompatibleModel if chat else OpenAIResponsesModel
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    model="fixture",
                    api_mode=api_mode,
                    content_item_kinds=kinds,
                    deferred_tool_world_state=True,
                    remote_compaction_v2=mode != "legacy",
                    context_window_tokens=100000,
                    auto_compact_tokens=90000,
                ),
                model=cls(
                    api_key="fixture",
                    base_url="https://fixture.invalid/v1",
                    capabilities=caps,
                    client=client,
                ),
                registry=registry,
                database_path=tmp_path / "s.db",
                home_path=tmp_path / ".corki",
                thread_id=thread,
                context_contributors=(
                    MemoryContextContributor(memories, enabled=True, token_limit=1000),
                ),
            )

        runtime = create()
        try:
            assert isinstance(
                [
                    e
                    async for e in runtime.stream(
                        "use $fixture; <environment_context>USER TEXT</environment_context>"
                    )
                ][-1],
                TurnCompleted,
            )
            if mode in {"v2", "legacy", "local"}:
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
                # Establish the new window's world-state baseline before removal.
                assert isinstance(
                    [e async for e in runtime.stream("after compact")][-1], TurnCompleted
                )
            rules.unlink()
            assert isinstance([e async for e in runtime.stream("remove rules")][-1], TurnCompleted)
            thread = runtime.thread_id
            before_cold = await runtime._repository.load_items(thread)
            await runtime.aclose()
            runtime = create(thread, chat=mode == "chat")
            assert isinstance([e async for e in runtime.stream("following")][-1], TurnCompleted)
            assert calls == ["one"]
            expected = {
                "mode.default": "collaboration_mode.instructions",
                "project.agents": "agents_md.instructions",
                "environment.primary": "environments.environment_context",
                "tools.deferred_namespaces": "tools.deferred_namespaces",
                "memory.instructions": "memories.instructions",
                "project.snapshot": "corki.project.snapshot",
                "extensions.skills.catalog": "corki.skills.catalog",
            }
            archive = await runtime._repository.load_items(thread)
            assert archive[: len(before_cold)] == before_cold
            contexts = [
                item
                for item in archive
                if isinstance(item, ContextItem) and not item.is_snapshot_only
            ]
            selected = [
                item for item in contexts if item.key.startswith("extensions.skills.selected.")
            ]
            assert len(selected) == 1
            expected[selected[0].key] = "skills.selected_skill_instructions"
            # This checks actual HTTP requests, not only the new canonical field.
            seen = set()
            seen_user = False
            request_keys = []
            for body in bodies:
                if "input" not in body:
                    assert "SELECTED BODY" in json.dumps(body)
                    continue
                keys = set()
                by_id = {item.get("id"): item for item in body["input"]}
                for source in contexts:
                    wire = by_id.get(f"msg_{source.message_group_id or source.id}")
                    if wire is None or source.key not in expected:
                        continue
                    seen.add(source.key)
                    keys.add(source.key)
                    assert wire["role"] == source.role.value
                    index = source.message_group_index
                    assert len(wire["content"]) == source.message_group_size
                    assert wire["content"][index] == {"type": "input_text", "text": source.content}
                    assert META not in wire
                request_keys.append(keys)
                if provider == "openai" and kinds:
                    users = [
                        i for i in body["input"] if "USER TEXT" in json.dumps(i.get("content", []))
                    ]
                    for user in users:
                        seen_user = True
                        assert META not in user
            assert seen == set(expected)
            assert request_keys[0] == set(expected)
            assert request_keys[1] == set(expected)
            if mode in {"v2", "legacy", "local"}:
                assert request_keys[2] == set(expected)  # Actual compaction input.
            cold_keys = set(expected) - {selected[0].key}
            if mode != "chat":
                assert cold_keys <= request_keys[-1]
            if provider == "openai" and kinds:
                assert seen_user
            for item in contexts:
                if item.key in expected:
                    assert item.content_kind == expected[item.key]
            agents = [i for i in contexts if i.key == "project.agents"]
            assert len(agents) >= 2
            assert "ORIGINAL RULE" in agents[0].content
            assert all("UPDATED RULE" not in i.content for i in agents)
            assert (
                "previously provided AGENTS.md instructions no longer apply" in agents[-1].content
            )
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@dataclass(frozen=True, slots=True)
class TaggedContribution(PromptContribution):
    content_kind: str = "fixture.first"


def test_classification_change_and_removal_keep_producer_ownership(tmp_path):
    async def scenario():
        active, bodies = ["fixture.first"], []

        class Contributor:
            def contributions(self, **kwargs):
                return (
                    ()
                    if active[0] is None
                    else (
                        TaggedContribution(
                            "fixture",
                            "memory/consolidation_inputs",
                            PromptRole.USER,
                            PromptSlot.EXTENSIONS,
                            variables={"inputs": "same text"},
                            content_kind=active[0],
                        ),
                    )
                )

        def respond(request):
            bodies.append(json.loads(request.content))
            return response(
                [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ]
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "s.db",
            model=OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid/v1",
                    api_mode="responses",
                    provider_name="openai",
                ),
                client=client,
            ),
            context_contributors=(Contributor(),),
        )
        try:
            for kind in ("fixture.first", "fixture.second", None):
                active[0] = kind
                assert isinstance([e async for e in runtime.stream("continue")][-1], TurnCompleted)
            fragments = [
                i
                for i in bodies[-1]["input"]
                if "same text" in json.dumps(i) or "'fixture'" in json.dumps(i)
            ]
            assert [
                kind
                for item in fragments
                for kind in item.get(META, {}).get("content_item_kinds", [])
                if kind.startswith("fixture.")
            ] == []
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert {i.content_kind for i in stored if isinstance(i, ContextItem)} >= {
                "fixture.first",
                "fixture.second",
            }
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
