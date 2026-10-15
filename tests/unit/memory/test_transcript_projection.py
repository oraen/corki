"""The extraction archive preserves facts and identities, not live context instructions."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.core.checkpoint import checkpoint_serializer
from corki.memory.pipeline import _render_transcript
from corki.protocol.ids import ToolCallId, new_item_id, new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ContextRole,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    item_from_payload,
    item_to_payload,
    new_step_id,
)
from corki.protocol.tools import AudioAttachment, ImageAttachment, TextContent, ToolCall, ToolSpec
from corki.storage import SQLiteSessionRepository


def test_structured_tool_archive_preserves_identity_errors_definitions_and_phase():
    turn, step = new_turn_id(), new_step_id()
    call = ToolCallItem(
        ToolCall(ToolCallId("lookup-1"), "tool_search", {"query": "fact"}), turn, step
    )
    result = ToolResultItem(
        call.call.id,
        "tool_search",
        "found fact",
        turn,
        discovered_tools=(ToolSpec("fact", "Read fact", {"type": "object"}),),
    )
    raw = ToolCallItem(
        ToolCall(
            ToolCallId("raw-1"),
            "exec",
            None,
            raw_arguments="text(await tools.fact({}))",
            input_kind="freeform",
        ),
        turn,
        step,
    )
    failed = ToolResultItem(
        raw.call.id,
        "exec",
        "failed",
        turn,
        is_error=True,
        input_kind="freeform",
        display_content="UI-ONLY",
    )
    answer = AssistantMessageItem("done", turn, step, phase="final_answer")
    rows = json.loads(_render_transcript((call, result, raw, failed, answer)))
    assert [row["type"] for row in rows] == [
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "assistant_message",
    ]
    assert rows[0]["call"]["id"] == rows[1]["call_id"] == "lookup-1"
    assert rows[1]["discovered_tools"][0]["name"] == "fact"
    assert rows[2]["call"]["raw_arguments"] == "text(await tools.fact({}))"
    assert rows[2]["call"]["input_kind"] == rows[3]["input_kind"] == "freeform"
    assert rows[3]["is_error"] and "UI-ONLY" not in json.dumps(rows)
    assert rows[4]["phase"] == "final_answer"


@pytest.mark.parametrize(
    "fragment",
    [
        "# AGENTS.md instructions for /tmp\n<INSTRUCTIONS>excluded</INSTRUCTIONS>",
        "  # agents.md INSTRUCTIONS\n<instructions>excluded</instructions>  ",
        "\n<SKILL>excluded</skill>\t",
    ],
)
def test_user_fragments_filtered_per_content_part_without_losing_media(fragment):
    turn = new_turn_id()
    image = ImageAttachment("data:image/png;base64,aGVsbG8=", "low")
    audio = AudioAttachment("data:audio/wav;base64,aGVsbG8=")
    user = UserMessageItem(
        "IGNORED FALLBACK excluded",
        turn,
        attachments=(image,),
        content_items=(TextContent(fragment), image, TextContent("keep request"), audio),
    )
    rows = json.loads(_render_transcript((UserMessageItem(fragment, turn), user)))
    assert len(rows) == 1
    assert [part["type"] for part in rows[0]["content_items"]] == ["image", "text", "audio"]
    assert rows[0]["content_items"][1]["text"] == "keep request"
    assert "excluded" not in json.dumps(rows) and "IGNORED FALLBACK" not in json.dumps(rows)


@pytest.mark.parametrize(
    "text",
    [
        "Please inspect <skill>actual user request</skill>",
        "<skill>not closed",
        "<skill>body</skill> follow-up",
        "<environment_context>cwd</environment_context>",
        "<subagent_notification>done</subagent_notification>",
        "<ſkill>unicode is not ASCII case-insensitive</skill>",
    ],
)
def test_only_complete_marked_user_fragments_are_excluded(text):
    (row,) = json.loads(_render_transcript((UserMessageItem(text, new_turn_id()),)))
    assert row["content"] == text


def test_context_reasoning_compaction_and_explicit_retained_copies_are_excluded():
    turn, step = new_turn_id(), new_step_id()
    original = UserMessageItem("original request", turn)
    retained = replace(original, retained_from_id=original.id)
    pending = UserMessageItem("genuinely new request", turn)
    items = (
        original,
        ContextItem("environment", ContextRole.USER, "world-state-only", turn),
        ReasoningItem("opaque-only", turn, step),
        CompactionItem("summary-only", original.id, turn, replacement_item_count=2),
        retained,
        pending,
    )
    rows = json.loads(_render_transcript(items))
    assert [row["content"] for row in rows] == ["original request", "genuinely new request"]
    assert "retained_from_id" not in item_to_payload(original)
    assert item_from_payload("user_message", item_to_payload(original)) == original
    assert item_from_payload("user_message", item_to_payload(retained)) == retained


def test_redaction_cannot_break_json_escaping_or_mutate_durable_items():
    original = UserMessageItem('password=abcdefgh\\"more\nkeep this', new_turn_id())
    before = item_to_payload(original)
    rows = json.loads(_render_transcript((original,)))
    assert "abcdefgh" not in rows[0]["content"]
    assert "keep this" in rows[0]["content"]
    assert item_to_payload(original) == before


def test_tool_output_keeps_ordered_media_and_ignores_display_and_state():
    turn = new_turn_id()
    result = ToolResultItem(
        ToolCallId("media"),
        "read",
        "stale fallback",
        turn,
        display_content="UI ONLY",
        attachments=(ImageAttachment("legacy duplicate"),),
        content_items=(
            TextContent("before"),
            ImageAttachment("data:image/png;base64,aGVsbG8=", "low"),
            AudioAttachment("data:audio/wav;base64,aGVsbG8="),
            TextContent("after"),
        ),
    )
    before = item_to_payload(result)
    (row,) = json.loads(_render_transcript((result,)))
    assert [part["type"] for part in row["content_items"]] == ["text", "image", "audio", "text"]
    assert not {"content", "attachments", "display_content", "state_update"}.intersection(row)
    assert item_to_payload(result) == before


def test_filtered_legacy_text_does_not_drop_its_image():
    user = UserMessageItem(
        "<skill>body</skill>", new_turn_id(), attachments=(ImageAttachment("data:a"),)
    )
    (row,) = json.loads(_render_transcript((user,)))
    assert row["content"] == "" and row["attachments"][0]["data_url"] == "data:a"


def test_user_provenance_round_trips_through_checkpoint_and_old_bytes_remain_readable():
    import ormsgpack
    from langgraph.checkpoint.serde.jsonplus import EXT_CONSTRUCTOR_KW_ARGS

    serializer = checkpoint_serializer()
    original = UserMessageItem("legacy request", new_turn_id())
    retained = replace(original, retained_from_id=original.id)
    assert serializer.loads_typed(serializer.dumps_typed(retained)) == retained
    # Actual legacy constructor bytes, not a new dataclass with a None field.
    payload = item_to_payload(original)
    assert "retained_from_id" not in payload
    encoded = ormsgpack.packb(
        ormsgpack.Ext(
            EXT_CONSTRUCTOR_KW_ARGS,
            ormsgpack.packb(("corki.protocol.items", "UserMessageItem", payload)),
        )
    )
    assert serializer.loads_typed(("msgpack", encoded)) == original


def test_legacy_user_payload_is_idempotent_after_reopen_with_new_retained_copies(tmp_path):
    async def scenario():
        path, thread = tmp_path / "sessions.db", new_thread_id()
        original = UserMessageItem("old request", new_turn_id())
        assert "retained_from_id" not in item_to_payload(original)
        repository = SQLiteSessionRepository(path)
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(thread, (original,))
        await repository.close()
        repository = SQLiteSessionRepository(path)
        try:
            (loaded,) = await repository.load_items(thread)
            assert loaded == original
            copy = replace(loaded, id=new_item_id(), retained_from_id=loaded.id)
            await repository.append_items(thread, (loaded, copy))
            await repository.append_items(thread, (loaded, copy))
            assert await repository.load_items(thread) == (original, copy)
            rows = json.loads(_render_transcript(await repository.load_items(thread)))
            assert len(rows) == 1 and rows[0]["id"] == original.id
        finally:
            await repository.close()

    asyncio.run(scenario())
