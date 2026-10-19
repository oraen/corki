"""Live reply restoration must not lose text at the former 64K display cutoff."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from live import append_event


def test_long_reply_and_interleaved_output_restore_complete_text():
    events = []
    expected = ""
    for index in range(500):
        delta = f"片段{index}🦀" * 40
        expected += delta
        append_event(
            events, "AssistantTextDelta", {"turn_id": "turn", "item_id": "answer", "delta": delta}
        )
        append_event(
            events,
            "ToolOutputDelta",
            {"turn_id": "turn", "tool_call_id": "tool", "delta": "log" * 100},
        )
        append_event(
            events,
            "AssistantReasoningDelta",
            {
                "turn_id": "turn",
                "item_id": "reason",
                "channel": "summary",
                "delta": "summary" * 100,
            },
        )
    assert len(expected) > 64000
    assert len(events) == 3
    assert str(events[0]["data"]["delta"]) == expected
    assert len(events[1]["data"]["delta"]) == 64000
    assert len(events[2]["data"]["delta"]) == 64000


def test_anonymous_completions_and_turn_boundaries_do_not_merge():
    events = []
    append_event(events, "AssistantTextDelta", {"turn_id": "one", "delta": "first"})
    append_event(
        events, "AssistantMessageCompleted", {"turn_id": "one", "item_id": "saved", "text": "first"}
    )
    append_event(events, "AssistantTextDelta", {"turn_id": "one", "delta": "second"})
    append_event(events, "AssistantTextDelta", {"turn_id": "two", "delta": "third"})
    assert [str(e["data"]["delta"]) if "delta" in e["data"] else None for e in events] == [
        "first",
        None,
        "second",
        "third",
    ]


def test_spooled_projection_rolls_to_disk_serializes_exactly_and_releases_files():
    import gc
    import json

    from live import MEMORY_TEXT_BYTES, SpooledText
    from protocol import dumps

    original = "完整中文🦀" * 100000
    data = {"turn_id": "turn", "item_id": "answer", "delta": original}
    events = []
    append_event(events, "AssistantTextDelta", data)
    text = events[0]["data"]["delta"]
    assert isinstance(text, SpooledText)
    assert text.file._rolled
    assert text.file._max_size == MEMORY_TEXT_BYTES
    assert data["delta"] is original
    assert json.loads(dumps(events))[0]["data"]["delta"] == original
    append_event(events, "AssistantTextDelta", {**data, "delta": "追加"})
    assert str(text) == original + "追加"
    backing = text.file
    del text
    for index in range(200):
        append_event(events, "WarningEvent", {"turn_id": "turn", "message": str(index)})
    gc.collect()
    assert backing.closed
    assert len(events) == 200


def test_large_nested_tool_payload_uses_spool_without_altering_wire_content():
    import json

    from live import SpooledText
    from protocol import dumps

    value = "patch-body" * 10000
    data = {"turn_id": "turn", "nested": {"parts": [value]}}
    events = []
    append_event(events, "ToolCallStarted", data)
    assert isinstance(events[0]["data"]["nested"]["parts"][0], SpooledText)
    assert json.loads(dumps(events))[0]["data"] == data
