import asyncio
import json

import httpx
import pytest

from corki.models import ModelRequest, OpenAICompatibleModel, resolve_capabilities
from corki.models.responses import OpenAIResponsesModel
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem, ContextRole, UserMessageItem


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_direct_adapter_call_excludes_snapshot_only_items(mode):
    async def scenario():
        turn = new_turn_id()
        silent = ContextItem(
            "extensions.skills.catalog",
            ContextRole.DEVELOPER,
            "",
            turn,
            snapshot_state="skills.hidden",
        )
        notice = ContextItem(
            "extensions.skills.catalog",
            ContextRole.DEVELOPER,
            "VISIBLE NOTICE",
            turn,
            snapshot_content="",
            snapshot_state="skills.hidden",
        )
        cls = OpenAIResponsesModel if mode == "responses" else OpenAICompatibleModel
        async with httpx.AsyncClient() as client:
            adapter = cls(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid/v1", api_mode=mode
                ),
                client=client,
            )
            payload = adapter._build_payload(
                ModelRequest(
                    "fixture",
                    "base",
                    (silent,),
                    (silent, notice, UserMessageItem("hello", turn)),
                    (),
                )
            )
        messages = payload["input" if mode == "responses" else "messages"]
        assert [m["content"] for m in messages] == (
            ["VISIBLE NOTICE", [{"type": "input_text", "text": "hello"}]]
            if mode == "responses"
            else ["base", "VISIBLE NOTICE", "hello"]
        )
        wire = json.dumps(payload)
        assert "snapshot_state" not in wire and "skills.hidden" not in wire

    asyncio.run(scenario())
