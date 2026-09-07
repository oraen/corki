"""Durable interruption identity, checkpoint compatibility, and bounded model cost."""

import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.context.interruptions import interrupted_turn_item
from corki.context.tokens import estimate_item_tokens, estimate_text_tokens
from corki.core.checkpoint import checkpoint_serializer
from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import item_from_payload, item_kind, item_to_payload
from corki.protocol.messages import MessageRole
from corki.storage import SQLiteSessionRepository


def test_marker_roundtrips_and_deduplicates_without_losing_model_budget(tmp_path):
    async def scenario():
        thread, turn = ThreadId("thread"), TurnId("turn")
        item = interrupted_turn_item(turn)
        assert item_from_payload(item_kind(item), item_to_payload(item)) == item
        serializer = checkpoint_serializer()
        assert serializer.loads_typed(serializer.dumps_typed(item)) == item
        assert estimate_item_tokens(item) == 12 + estimate_text_tokens(item.content)
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        try:
            await repository.create_thread(thread, tmp_path)
            await repository.append_items(thread, (item,))
            await repository.append_items(thread, (replace(item, created_at="later"),))
            assert await repository.load_items(thread) == (item,)
            messages = await repository.load_messages(thread)
            assert len(messages) == 1 and messages[0].role is MessageRole.USER
            assert messages[0].content == item.content
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("enabled", [False, True])
def test_agents_interrupt_message_config_controls_runtime_settings(tmp_path, enabled):
    config = tmp_path / "config.toml"
    config.write_text(f"[agents]\ninterrupt_message = {str(enabled).lower()}\n")
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.agent_interrupt_message_enabled is enabled


@pytest.mark.parametrize("invalid", [None, 0, "false", []])
def test_interrupt_message_requires_boolean(tmp_path, invalid):
    with pytest.raises(ValueError, match="interrupt_message"):
        CorkiSettings(working_directory=tmp_path, agent_interrupt_message_enabled=invalid)
