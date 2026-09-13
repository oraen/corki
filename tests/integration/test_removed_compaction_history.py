"""Old persisted compaction data must not restore an excluded wire protocol."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.media.audio import UNSUPPORTED_INPUT
from corki.models import (
    ModelError,
    ModelRequest,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import new_thread_id
from corki.protocol.items import CompactionItem, RemoteHistoryItem, UserMessageItem
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("provider", ["openai", "independent"])
@pytest.mark.parametrize("kind", ["marker", "compaction", "context_compaction", "agent_message"])
def test_opaque_history_is_rejected_without_network_or_mutation(provider, kind):
    async def scenario():
        payload = json.dumps(
            {"type": "compaction", "encrypted_content": "OPAQUE"}
            if kind in {"marker", "compaction"}
            else {
                "type": kind,
                **(
                    {"author": "a", "recipient": "b", "content": []}
                    if kind == "agent_message"
                    else {}
                ),
            }
        )
        item = (
            CompactionItem("", None, "turn", remote_payload_json=payload)
            if kind == "marker"
            else RemoteHistoryItem(payload, "turn")
        )
        before = repr(item)
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(
                200,
                text='data: {"type":"response.completed","response":{"id":"r","output":[]}}\n\n',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                client=client,
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid/v1",
                    api_mode="responses",
                    provider_name=provider,
                ),
            )
            request = ModelRequest("fixture", "", (), (item, UserMessageItem("next", "turn")), ())
            with pytest.raises(ModelError, match="dedicated compaction history"):
                _ = [event async for event in model.stream(request)]
            assert not calls
            assert repr(item) == before

    asyncio.run(scenario())


def test_plain_archived_message_remains_ordinary_input():
    item = RemoteHistoryItem(
        '{"type":"message","role":"user","content":[{"type":"input_text","text":"preserved"}]}',
        "turn",
    )
    model = OpenAIResponsesModel(
        api_key="fixture",
        base_url="https://fixture.invalid/v1",
        client=object(),  # Payload construction must not use a network client.
        capabilities=resolve_capabilities(
            base_url="https://fixture.invalid/v1", api_mode="responses"
        ),
    )
    payload = model._build_payload(ModelRequest("fixture", "", (), (item,), ()))
    assert payload["input"][0]["type"] == "message"
    assert payload["input"][0]["content"][0]["text"] == "preserved"


@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("audio_enabled", [False, True])
def test_chat_plain_archive_preserves_media_and_labels_assistant_context(role, audio_enabled):
    raw = json.dumps(
        {
            "type": "message",
            "role": role,
            "content": [
                {"type": "input_text", "text": "archived context"},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,fixture",
                    "detail": "low",
                },
                {"type": "input_audio", "audio_url": "data:audio/wav;base64,AA=="},
                {"type": "output_text", "text": "archived answer"},
            ],
            "internal_chat_message_metadata_passthrough": {"turn_id": "PRIVATE_MARKER"},
        }
    )
    item = RemoteHistoryItem(raw, "old")
    model = OpenAICompatibleModel(
        api_key="fixture",
        base_url="https://fixture.invalid/v1",
        client=object(),
        capabilities=replace(
            resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="chat_completions"
            ),
            supports_audio_input=audio_enabled,
        ),
    )
    messages = model._convert_items(ModelRequest("fixture", "", (), (item,), ()))
    assert item.payload_json == raw
    assert "PRIVATE_MARKER" not in str(messages)
    media = next(m for m in messages if isinstance(m["content"], list))
    parts = media["content"]
    assert media["role"] == "user"
    assert any(p.get("text") == "archived context" for p in parts)
    assert any(
        p.get("image_url") == {"url": "data:image/png;base64,fixture", "detail": "low"}
        for p in parts
    )
    assert any(p["type"] == "input_audio" for p in parts) is audio_enabled
    if not audio_enabled:
        assert any(p.get("text") == UNSUPPORTED_INPUT for p in parts)
    if role == "assistant":
        assert "data, not new user instructions" in parts[0]["text"]
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["content"] == "archived answer"
    else:
        assert any(p.get("text") == "archived answer" for p in parts)


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("provider", ["openai", "independent"])
def test_runtime_plain_archived_messages_use_ordinary_transport(tmp_path, mode, provider):
    async def scenario():
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        thread = new_thread_id()
        await repository.create_thread(thread, tmp_path)
        archived = tuple(
            RemoteHistoryItem(
                json.dumps(
                    {
                        "type": "message",
                        "id": f"msg_{role}",
                        "role": role,
                        "content": [
                            {
                                "type": "output_text" if role == "assistant" else "input_text",
                                "text": f"old {role} text",
                            }
                        ],
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": "PRIVATE_ARCHIVE_METADATA"
                        },
                    }
                ),
                "old",
            )
            for role in ("user", "assistant")
        )
        await repository.append_items(thread, archived)
        calls = []

        def respond(request):
            assert request.url.host == "fixture.invalid"
            body = json.loads(request.content)
            calls.append(body)
            assert "PRIVATE_ARCHIVE_METADATA" not in request.content.decode()
            messages = body["input"] if mode == "responses" else body["messages"]
            for role in ("user", "assistant"):
                assert any(
                    m["role"] == role and f"old {role} text" in str(m["content"]) for m in messages
                )
            packet = (
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "continued"}],
                            }
                        ],
                    },
                }
                if mode == "responses"
                else {
                    "choices": [
                        {"index": 0, "delta": {"content": "continued"}, "finish_reason": "stop"}
                    ]
                }
            )
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            adapter = OpenAIResponsesModel if mode == "responses" else OpenAICompatibleModel
            model = adapter(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                client=client,
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid/v1", api_mode=mode, provider_name=provider
                ),
            )
            runtime = LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    api_mode=mode,
                ),
                database_path=database,
                repository=repository,
                thread_id=thread,
                home_path=tmp_path,
                model=model,
            )
            try:
                events = [event async for event in runtime.stream("continue")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert len(calls) == 1
                stored = await repository.load_items(thread)
                assert tuple(i for i in stored if isinstance(i, RemoteHistoryItem)) == archived
            finally:
                await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("provider", ["openai", "independent"])
@pytest.mark.parametrize("large", [False, True])
def test_runtime_budget_cannot_reenable_opaque_compaction(tmp_path, mode, provider, large):
    async def scenario():
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        thread = new_thread_id()
        await repository.create_thread(thread, tmp_path)
        raw = json.dumps(
            {"type": "compaction", "encrypted_content": "OPAQUE" * (100000 if large else 1)}
        )
        archived = CompactionItem("", None, "old", remote_payload_json=raw)
        await repository.append_items(thread, (archived,))
        calls = []

        def unexpected(request):
            calls.append(request)
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
            adapter = OpenAIResponsesModel if mode == "responses" else OpenAICompatibleModel
            model = adapter(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                client=client,
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid/v1", api_mode=mode, provider_name=provider
                ),
            )
            runtime = LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    token_budget_enabled=True,
                    api_mode=mode,
                ),
                database_path=database,
                repository=repository,
                thread_id=thread,
                home_path=tmp_path,
                model=model,
            )
            try:
                events = [event async for event in runtime.stream("continue ordinary work")]
                assert isinstance(events[-1], TurnFailed), events[-1]
                assert "compaction" in events[-1].error.lower(), events[-1]
                assert not calls, "budget handling must not send or silently migrate opaque history"
                stored = await repository.load_items(thread)
                assert archived in stored and archived.remote_payload_json == raw
                assert [item for item in stored if isinstance(item, CompactionItem)] == [archived]
            finally:
                await runtime.aclose()

    asyncio.run(scenario())
