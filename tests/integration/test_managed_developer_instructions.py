import asyncio
import json
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, settings

from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ContextRole,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.messages import Message, MessageRole
from corki.protocol.session_source import SessionSource, SessionSourceKind, SubAgentSource
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


def test_tool_step_compaction_and_cold_history_keep_policy_without_reexecution(tmp_path):
    async def scenario():
        executions = []

        class Tool:
            spec = ToolSpec("inspect", "Inspect fixture", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "COMMITTED OBSERVATION")

        class UsageModel(Model):
            async def stream(self, request):
                self.requests.append(request)
                index = len(self.requests)
                turn, step = request.items[-1].turn_id, new_step_id()
                item = (
                    ToolCallItem(ToolCall("inspect-once", "inspect", {}), turn, step)
                    if index == 1
                    else AssistantMessageItem("summary" if index == 2 else "done", turn, step)
                )
                yield ModelCompleted((item,), ModelUsage(180_000 if index == 1 else 100, 10))

        policy = compose_mcp_requirements(
            (MCPRequirementsLayer("host", 'additional_developer_instructions = "HOST POLICY"'),)
        )

        async def create(model, thread=None):
            registry = ToolRegistry()
            registry.register(Tool())
            return await LangGraphRuntime.acreate(
                settings=replace(settings(tmp_path), auto_compact_tokens=100_000),
                model=model,
                registry=registry,
                database_path=tmp_path / "policy.db",
                home_path=tmp_path / "home",
                mcp_requirements=policy,
                thread_id=thread,
            )

        def assert_policy(request):
            policies = [
                i
                for i in request.items
                if isinstance(i, ContextItem) and i.key == "managed_developer_instructions"
            ]
            assert len(policies) == 1
            assert policies[0].content == (
                "<managed_developer_instructions>\nHOST POLICY\n</managed_developer_instructions>"
            )
            assert policies[0].role is ContextRole.DEVELOPER and policies[0].separate_message

        model = UsageModel()
        runtime = await create(model)
        try:
            events = [e async for e in runtime.stream("KEEP CURRENT INPUT")]
            assert isinstance(events[-1], TurnCompleted)
            assert any(isinstance(e, ContextCompacted) for e in events)
            assert len(model.requests) == 3
            for request in model.requests:
                assert_policy(request)
            summary = model.requests[1]
            assert summary.tools == ()
            assert [i.call.id for i in summary.items if isinstance(i, ToolCallItem)] == [
                "inspect-once"
            ]
            assert [i.content for i in summary.items if isinstance(i, ToolResultItem)] == [
                "COMMITTED OBSERVATION"
            ]
            assert any(
                isinstance(i, UserMessageItem) and i.content == "KEEP CURRENT INPUT"
                for i in model.requests[-1].items
            )
            prefix = await runtime._repository.load_items(runtime.thread_id)
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold_model = Model()
        cold = await create(cold_model, thread)
        try:
            assert isinstance([e async for e in cold.stream("cold")][-1], TurnCompleted)
            assert_policy(cold_model.requests[-1])
            stored = await cold._repository.load_items(thread)
            assert stored[: len(prefix)] == prefix
            assert executions == ["inspect-once"]
            assert len([i for i in stored if isinstance(i, ToolCallItem)]) == 1
            assert len([i for i in stored if isinstance(i, ToolResultItem)]) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("guardian", [False, True])
def test_omitted_guardian_section_does_not_revoke_existing_policy(tmp_path, guardian):
    async def scenario():
        async def create(thread=None, *, initial=False):
            return await LangGraphRuntime.acreate(
                settings=settings(tmp_path),
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "policy.db",
                home_path=tmp_path / "home",
                thread_id=thread,
                session_source=(
                    SessionSource.internal("guardian")
                    if guardian and not initial
                    else SessionSource()
                ),
                mcp_requirements=compose_mcp_requirements(
                    (
                        MCPRequirementsLayer(
                            "host", 'additional_developer_instructions = "HOST POLICY"'
                        ),
                    )
                    if initial
                    else ()
                ),
            )

        first = await create(initial=True)
        try:
            assert isinstance([e async for e in first.stream("first")][-1], TurnCompleted)
            prefix = await first._repository.load_items(first.thread_id)
            thread = first.thread_id
        finally:
            await first.aclose()
        cold = await create(thread)
        try:
            assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
            stored = await cold._repository.load_items(thread)
            assert stored[: len(prefix)] == prefix
            updates = [
                i
                for i in stored[len(prefix) :]
                if isinstance(i, ContextItem) and i.key == "managed_developer_instructions"
            ]
            assert len(updates) == int(not guardian)
            if updates:
                assert "no longer apply" in updates[0].content
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["OLD POLICY", "NEW POLICY", "", None])
def test_legacy_policy_cold_reconcile_once(tmp_path, text):
    async def scenario():
        source = await LangGraphRuntime.acreate(
            settings=settings(tmp_path),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "policy.db",
            home_path=tmp_path / "home",
            mcp_requirements=compose_mcp_requirements(()),
        )
        try:
            await source._ensure_ready()
            thread = source.thread_id
            await source._repository.save_messages(
                thread,
                (
                    Message.create(
                        role=MessageRole.DEVELOPER,
                        content=(
                            "<managed_developer_instructions>\nOLD POLICY\n"
                            "</managed_developer_instructions>"
                        ),
                        turn_id=new_turn_id(),
                    ),
                ),
            )
            prefix = await source._repository.load_items(thread)
        finally:
            await source.aclose()
        policy = compose_mcp_requirements(
            ()
            if text is None
            else (
                MCPRequirementsLayer(
                    "host", "additional_developer_instructions = " + json.dumps(text)
                ),
            )
        )
        for _ in range(2):
            model = Model()
            cold = await LangGraphRuntime.acreate(
                settings=settings(tmp_path),
                model=model,
                registry=ToolRegistry(),
                database_path=tmp_path / "policy.db",
                home_path=tmp_path / "home",
                thread_id=thread,
                mcp_requirements=policy,
            )
            try:
                assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
                stored = await cold._repository.load_items(thread)
                assert stored[: len(prefix)] == prefix
                updates = [
                    i
                    for i in stored[len(prefix) :]
                    if isinstance(i, ContextItem) and i.key == "managed_developer_instructions"
                ]
                assert len(updates) == 1
                assert (
                    "replace all previously provided" if text else "no longer apply"
                ) in updates[0].content
                assert updates[0].content_kind == "managed_config.developer_instructions"
                assert not any(
                    isinstance(i, ContextItem) and i.key == "legacy.developer"
                    for i in stored[len(prefix) :]
                )
            finally:
                await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("manual", [False, True])
def test_compaction_rebuilds_host_policy_with_typed_markers(tmp_path, manual):
    class UsageModel(Model):
        async def stream(self, request):
            async for event in super().stream(request):
                yield (
                    replace(event, usage=ModelUsage(180_000, 10))
                    if isinstance(event, ModelCompleted)
                    else event
                )

    async def scenario():
        policy = compose_mcp_requirements(
            (MCPRequirementsLayer("host", 'additional_developer_instructions = "HOST POLICY"'),)
        )
        model = UsageModel()
        runtime = await LangGraphRuntime.acreate(
            settings=replace(settings(tmp_path), auto_compact_tokens=100_000),
            model=model,
            registry=ToolRegistry(),
            database_path=tmp_path / "policy.db",
            home_path=tmp_path / "home",
            mcp_requirements=policy,
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            prefix = await runtime._repository.load_items(runtime.thread_id)
            events = [e async for e in runtime.compact()] if manual else []
            events.extend([e async for e in runtime.stream("KEEP CURRENT INPUT")])
            assert isinstance(events[-1], TurnCompleted)
            assert any(isinstance(e, ContextCompacted) for e in events)
            updates = [
                i
                for i in model.requests[-1].items
                if isinstance(i, ContextItem)
                and i.content_kind == "managed_config.developer_instructions"
            ]
            assert len(updates) == 1
            assert updates[0].content == (
                "<managed_developer_instructions>\nHOST POLICY\n</managed_developer_instructions>"
            )
            assert updates[0].role is ContextRole.DEVELOPER and updates[0].separate_message
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert stored[: len(prefix)] == prefix
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "source,expected",
    [
        (SessionSource(SessionSourceKind.INTERNAL, "guardian"), False),
        (SessionSource(SessionSourceKind.SUBAGENT, SubAgentSource("other", "guardian")), False),
        (SessionSource(SessionSourceKind.CUSTOM, "guardian"), True),
        (SessionSource(SessionSourceKind.SUBAGENT, SubAgentSource("review")), True),
    ],
)
def test_only_basic_guardian_sources_omit_host_policy(tmp_path, source, expected):
    async def scenario():
        policy = compose_mcp_requirements(
            (MCPRequirementsLayer("host", 'additional_developer_instructions = "HOST POLICY"'),)
        )
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=settings(tmp_path),
            model=model,
            registry=ToolRegistry(),
            database_path=tmp_path / "policy.db",
            home_path=tmp_path / "home",
            mcp_requirements=policy,
            session_source=source,
        )
        try:
            assert isinstance([e async for e in runtime.stream("next")][-1], TurnCompleted)
            assert (
                any(
                    isinstance(i, ContextItem)
                    and i.content_kind == "managed_config.developer_instructions"
                    for i in model.requests[-1].items
                )
                is expected
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_host_policy_cold_replace_remove_and_deduplicate(tmp_path):
    async def scenario():
        thread = None
        prefix = ()
        for text in ("FIRST POLICY", "SECOND POLICY", ""):
            policy = compose_mcp_requirements(
                (
                    MCPRequirementsLayer(
                        "host", "additional_developer_instructions = " + json.dumps(text)
                    ),
                )
            )
            model = Model()
            runtime = await LangGraphRuntime.acreate(
                settings=settings(tmp_path),
                model=model,
                registry=ToolRegistry(),
                database_path=tmp_path / "policy.db",
                thread_id=thread,
                home_path=tmp_path / "home",
                mcp_requirements=policy,
            )
            try:
                assert isinstance([e async for e in runtime.stream("next")][-1], TurnCompleted)
                stored = await runtime._repository.load_items(runtime.thread_id)
                assert stored[: len(prefix)] == prefix
                updates = [
                    i
                    for i in stored[len(prefix) :]
                    if isinstance(i, ContextItem)
                    and i.content_kind == "managed_config.developer_instructions"
                ]
                assert len(updates) == 1
                update = updates[0]
                assert update.role is ContextRole.DEVELOPER and update.separate_message
                assert "<managed_developer_instructions>" in update.content
                assert text in update.content
                if prefix:
                    assert (
                        "replace all previously provided" if text else "no longer apply"
                    ) in update.content
                assert isinstance([e async for e in runtime.stream("unchanged")][-1], TurnCompleted)
                final = await runtime._repository.load_items(runtime.thread_id)
                assert not any(
                    isinstance(i, ContextItem) and i.content_kind == update.content_kind
                    for i in final[len(stored) :]
                )
                prefix, thread = final, runtime.thread_id
            finally:
                await runtime.aclose()

    asyncio.run(scenario())
