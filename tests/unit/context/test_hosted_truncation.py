"""Codex policy golden cases and raw/projection invariants."""

import json

import pytest

from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.context.hosted_output import (
    project_hosted_items,
    truncate_output_body,
    truncate_output_text,
)
from corki.context.tokens import estimate_item_tokens
from corki.core.checkpoint import checkpoint_serializer
from corki.history_notes.archive import project_history
from corki.memory.transcript import render_transcript
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import HostedToolItem, item_from_payload, item_to_payload, new_step_id
from corki.protocol.truncation import TruncationPolicy


@pytest.mark.parametrize(
    "text,mode,limit,expected",
    [
        ("example output", "bytes", 1, "…13 chars truncated…t"),
        ("example output", "tokens", 1, "ex…3 tokens truncated…ut"),
        (
            "😀" * 10 + "\nsecond line with text\n",
            "bytes",
            20,
            "😀😀…21 chars truncated…with text\n",
        ),
        ("字abc", "tokens", 0, "…2 tokens truncated…"),
        ("字abc", "bytes", 0, "…4 chars truncated…"),
        ("", "tokens", 0, ""),
        ("字", "bytes", 3, "字"),
    ],
)
def test_source_utf8_and_marker_contract(text, mode, limit, expected):
    assert truncate_output_text(text, TruncationPolicy(mode, limit)) == expected


@pytest.mark.parametrize("mode,limit,marker", [("tokens", 2, "1 tokens"), ("bytes", 8, "2 chars")])
def test_ordered_output_budget_keeps_media_and_appends_omission_counts(mode, limit, marker):
    image = {"type": "input_image", "image_url": "fixture", "detail": "original"}
    encrypted = {"type": "encrypted_content", "encrypted_content": "OPAQUE" * 100}
    parts = [
        {"type": "input_text", "text": ""},
        {"type": "input_text", "text": "abcd"},
        image,
        {"type": "input_text", "text": "abcdef"},
        {"type": "input_text", "text": "tail"},
        {"type": "input_audio", "audio_url": "fixture"},
        encrypted,
    ]
    assert truncate_output_body(parts, TruncationPolicy(mode, limit)) == [
        {"type": "input_text", "text": "abcd"},
        image,
        {"type": "input_text", "text": f"ab…{marker} truncated…ef"},
        encrypted,
        {"type": "input_text", "text": "[omitted 1 text items ...]"},
        {"type": "input_text", "text": "[omitted 1 audio items ...]"},
    ]
    assert parts[-3]["text"] == "tail"


@pytest.mark.parametrize(
    "override,expected",
    [
        (None, "abcd…2 tokens truncated…mnop"),
        (1, "ab…3 tokens truncated…op"),
        (0, "…4 tokens truncated…"),
    ],
)
def test_host_override_and_visible_copy_do_not_mutate_archive(override, expected):
    raw = {
        "type": "function_call_output",
        "output": "abcdefghijklmnop",
        "_meta": {"fallback_token_limit_override": 100000},
    }
    original = HostedToolItem(
        json.dumps(raw), new_turn_id(), new_step_id(), fallback_token_limit_override=override
    )
    (projected,) = project_hosted_items((original,), TruncationPolicy("tokens", 1))
    assert json.loads(projected.visible_payload_json)["output"] == expected
    assert projected.id == original.id and projected.payload_json == original.payload_json
    assert original.model_payload_json is None
    assert "abcdefghijklmnop" in render_transcript((projected,), redact=lambda x: x)
    assert "abcdefghijklmnop" in list(project_history((projected,), new_thread_id()))[0]["text"]
    assert "model_payload_json" not in render_transcript((projected,), redact=lambda x: x)
    assert project_hosted_items((projected,), TruncationPolicy("bytes", 0)) == (projected,)
    codec = checkpoint_serializer()
    assert codec.loads_typed(codec.dumps_typed(projected)) == projected
    assert item_from_payload("hosted_tool", item_to_payload(projected)) == projected


def test_large_raw_fact_is_accepted_but_estimate_uses_visible_projection():
    raw = HostedToolItem(
        json.dumps({"type": "function_call_output", "output": "字" * 8000}, ensure_ascii=False),
        new_turn_id(),
        new_step_id(),
    )
    (projected,) = project_hosted_items((raw,), TruncationPolicy("bytes", 20))
    assert estimate_item_tokens(raw) > 10000
    assert estimate_item_tokens(projected) < 200
    assert "model_payload_json" not in item_to_payload(raw)
    assert "fallback_token_limit_override" not in item_to_payload(raw)


def test_transport_guard_remains_distinct_from_model_policy(monkeypatch):
    monkeypatch.setattr("corki.protocol.hosted.HOSTED_ITEM_MAX_BYTES", 40)
    with pytest.raises(ValueError, match="transport"):
        HostedToolItem(
            json.dumps({"type": "function_call_output", "output": "a" * 50}),
            new_turn_id(),
            new_step_id(),
        )


@pytest.mark.parametrize(
    "name,mode", [("gpt-6-astra", "tokens"), ("gpt-5.2", "bytes"), ("unknown", "bytes")]
)
def test_model_selection_and_token_override_use_reference_units(tmp_path, name, mode):
    settings = CorkiSettings(model=name, working_directory=tmp_path)
    assert settings.model_context_info(name).truncation_policy == TruncationPolicy(mode, 10000)
    overridden = CorkiSettings(model=name, working_directory=tmp_path, tool_output_token_limit=7)
    assert overridden.model_context_info(name).truncation_policy == TruncationPolicy(
        mode, 28 if mode == "bytes" else 7
    )
    (info,) = parse_model_contexts(
        {"fixture": {"truncation_policy": {"mode": "tokens", "limit": 0}}}
    )
    assert info.truncation_policy == TruncationPolicy("tokens", 0)


@pytest.mark.parametrize("value", [True, -1, "4", 2.5])
def test_invalid_policy_limits_fail_configuration(value, tmp_path):
    with pytest.raises(ValueError):
        TruncationPolicy("tokens", value)
    with pytest.raises(ValueError):
        CorkiSettings(working_directory=tmp_path, tool_output_token_limit=value)
