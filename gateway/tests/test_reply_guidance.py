"""Per-account guidance uses the full runtime, stays outside history and changes next run."""

import asyncio
import json

import pytest
from corki_gateway.agent import Agent
from corki_gateway.reply_guidance import request_context
from corki_gateway.service import Host

from corki.core import runtime as runtime_module
from corki.models import ModelCompleted, ModelError, ModelErrorKind, resolve_capabilities
from corki.models.openai_compatible import OpenAICompatibleModel
from corki.models.responses import OpenAIResponsesModel
from corki.protocol.ids import new_thread_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall


def test_guidance_real_runtime_tool_continuation_change_clear_and_wire_roles(tmp_path, monkeypatch):
    async def scenario():
        (tmp_path / "settings.json").write_text(
            json.dumps(
                {
                    "default": {"provider": "fixture", "model": "fixture-model"},
                    "providers": {
                        "fixture": {
                            "base_url": "https://example.invalid",
                            "api_key": "fixture",
                            "models": {"fixture-model": {}},
                        }
                    },
                    "skills": {"enabled": False},
                }
            )
        )
        models = []
        started, release = asyncio.Event(), asyncio.Event()

        class Model:
            def __init__(self):
                self.requests = []

            async def stream(self, request):
                self.requests.append(request)
                turn = next(
                    i.turn_id for i in reversed(request.items) if isinstance(i, UserMessageItem)
                )
                if len(models) == 1 and len(self.requests) == 1:
                    started.set()
                    await release.wait()
                    raise ModelError(
                        "Fixture reconnect", kind=ModelErrorKind.CONNECTION, retryable=True
                    )
                elif len(models) == 1 and len(self.requests) == 2:
                    # A nonexistent fixture tool still exercises tool-result continuation,
                    # without executing any external command or filesystem side effect.
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall("fixture-call", "fixture_missing_tool", {}),
                                turn,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    yield ModelCompleted(
                        (AssistantMessageItem("Fixture answer", turn, new_step_id()),),
                        end_turn=True,
                    )

            async def aclose(self):
                pass

        def create_model(*_):
            model = Model()
            models.append(model)
            return model

        monkeypatch.setattr(runtime_module, "_create_model", create_model)
        h = Host(tmp_path, online=False)
        channel = h.store.save_connection(
            {
                "channel": "dingtalk",
                "name": "Fixture",
                "values": {
                    "reply_guidance": "  用业务语言回答\n可按需阅读 Skill  ",
                    "session_validation": "false",
                    "client_id": "fixture-app",
                    "client_secret": "fixture-secret",
                },
            }
        )
        h.store.enable(channel, True)
        connector = object()
        h.connectors[channel] = connector
        agent = Agent(h)
        thread = new_thread_id()
        workdir = str(tmp_path / "work")
        (tmp_path / "work").mkdir()
        before = h.store.connection(channel)["conversation_id"]
        try:
            task = asyncio.create_task(agent.run(thread, "Fixture task", workdir, channel))
            await asyncio.wait_for(started.wait(), 20)
            await h.change(
                {
                    "action": "save-connection",
                    "id": channel,
                    "name": "Fixture",
                    "values": {"reply_guidance": "New business rule"},
                }
            )
            assert h.connectors[channel] is connector
            assert h.store.connection(channel)["conversation_id"] == before
            release.set()
            assert await asyncio.wait_for(task, 20) == "Fixture answer"
            assert len(models[0].requests) == 3
            for req in models[0].requests:
                assert len(req.context_items) == 1
                assert "用业务语言回答\n可按需阅读 Skill" in req.context_items[0].content
                assert "New business rule" not in req.context_items[0].content
                assert not any(
                    isinstance(i, ContextItem) and i.key == "gateway.reply_guidance"
                    for i in req.items
                )
            assert any(isinstance(i, ToolResultItem) for i in models[0].requests[2].items)
            assert await agent.run(thread, "Second question", workdir, channel) == "Fixture answer"
            assert "New business rule" in models[-1].requests[0].context_items[0].content
            await h.change(
                {
                    "action": "save-connection",
                    "id": channel,
                    "name": "Fixture",
                    "values": {"reply_guidance": "  \n\t"},
                }
            )
            assert await agent.run(thread, "Third question", workdir, channel) == "Fixture answer"
            assert models[-1].requests[0].context_items == ()
            items = await agent.repository.load_items(thread)
            assert all(
                not isinstance(i, ContextItem) or i.key != "gateway.reply_guidance" for i in items
            )
            # Verify the actual serializers, not just our provider-neutral role.
            req = models[0].requests[0]
            chat = OpenAICompatibleModel(
                api_key="fixture",
                base_url="https://example.invalid",
                capabilities=resolve_capabilities(
                    base_url="https://example.invalid", api_mode="chat_completions"
                ),
            )
            responses = OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://example.invalid",
                capabilities=resolve_capabilities(
                    base_url="https://example.invalid", api_mode="responses"
                ),
            )
            try:
                chat_payload = chat._build_payload(req)
                response_payload = responses._build_payload(req)
                assert (
                    len(
                        [
                            m
                            for m in chat_payload["messages"]
                            if m["role"] == "system"
                            and "<channel_reply_guidance>" in str(m["content"])
                        ]
                    )
                    == 1
                )
                assert (
                    len(
                        [
                            m
                            for m in response_payload["input"]
                            if m.get("role") == "developer"
                            and "<channel_reply_guidance>" in str(m["content"])
                        ]
                    )
                    == 1
                )
            finally:
                await chat.aclose()
                await responses.aclose()
        finally:
            release.set()
            h.connectors.pop(channel, None)
            await h.close()
            await agent.repository.close()

    asyncio.run(scenario())


def test_empty_is_absent_and_account_field_validation(tmp_path):
    assert request_context("") == request_context(" \n\t") == ()
    h = Host(tmp_path, online=False)
    with pytest.raises(ValueError, match="8192"):
        h.store.save_connection(
            {"channel": "wechat", "name": "Fixture", "values": {"reply_guidance": "x" * 8193}}
        )
