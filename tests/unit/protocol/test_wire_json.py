import json
import math

import pytest

from corki.models.base import ModelError
from corki.models.response_wire import decode_response_event
from corki.models.responses import _to_response_input
from corki.protocol.compaction import compaction_payload
from corki.protocol.items import RemoteHistoryItem, item_from_payload, item_to_payload
from corki.protocol.response_items import response_item_payload
from corki.protocol.wire_json import loads_wire, materialize
from corki.protocol.wire_numbers import WireNumber, dumps_wire


def typed(raw):
    return response_item_payload(materialize(loads_wire(raw)))


@pytest.mark.parametrize(
    "raw",
    [
        '{"type":"compaction","encrypted_content":"x","id":null,"id":"id"}',
        '{"type":"agent_message","author":"a","author":"a","recipient":"b","content":[]}',
        '{"type":"configuration_update","reasoning":{"effort":"low","effort":"high"}}',
        '{"type":"function_call_output","output":"x","output":"y"}',
        '{"type":"web_search_call","action":{"type":"search","query":null,"query":"x"}}',
        '{"type":"local_shell_call","status":"completed","action":{"type":"exec","command":[],"timeout_ms":null,"timeout_ms":1}}',
        '{"type":"function_call_output","output":[{"type":"input_image","image_url":"x","detail":null,"detail":"auto"}]}',
        '{"type":"compaction","encrypted_content":"x","internal_chat_message_metadata_passthrough":{"content_item_kinds":3,"content_item_kinds":[]}}',
        '{"type":"local_shell_call","status":"completed","action":{"type":"exec","command":[],"env":{"X":2,"X":"valid-last"}}}',
    ],
)
def test_typed_known_duplicates_and_invalid_earlier_map_values_fail(raw):
    with pytest.raises(ValueError):
        typed(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            '{"type":"compaction","encrypted_content":"x","unknown":1,"unknown":2}',
            {"type": "compaction", "encrypted_content": "x"},
        ),
        (
            '{"type":"compaction","encrypted_content":"x","internal_chat_message_metadata_passthrough":{"cell_id":1,"cell_id":2}}',
            {
                "type": "compaction",
                "encrypted_content": "x",
                "internal_chat_message_metadata_passthrough": {},
            },
        ),
        (
            '{"type":"tool_search_call","execution":"client","arguments":{"x":1,"x":2}}',
            {
                "type": "tool_search_call",
                "execution": "client",
                "call_id": None,
                "arguments": {"x": 2},
            },
        ),
        ('{"type":"future","id":1,"id":2}', {"type": "other"}),
    ],
)
def test_ignored_fields_and_arbitrary_value_duplicates_remain_valid(raw, expected):
    assert typed(raw) == expected


@pytest.mark.parametrize(
    "suffix",
    [
        ',"unused":1e999',
        ',"unused":"\\ud800"',
        ',"unused":1,"unused":2',
        ',"unused":' + "[" * 200 + "0" + "]" * 200,
    ],
)
def test_sse_ignored_top_level_values_do_not_use_typed_scalar_or_depth_rules(suffix):
    assert decode_response_event('{"type":"future"' + suffix + "}") == {"type": "future"}


def test_sse_value_maps_validate_every_value_then_use_last_key():
    event = decode_response_event(
        '{"type":"response.output_item.done","item":{"type":"compaction","encrypted_content":"old","encrypted_content":"new"}}'
    )
    assert compaction_payload(json.dumps(event["item"]))["encrypted_content"] == "new"
    with pytest.raises(ModelError):
        decode_response_event('{"type":"future","metadata":{"x":"\\ud800","x":1}}')


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_constants_are_invalid_even_when_ignored(token):
    with pytest.raises(ModelError):
        decode_response_event('{"type":"future","unknown":' + token + "}")


@pytest.mark.parametrize(
    "token", ["true", "1.0", "-0", "9223372036854775808", "-9223372036854775809"]
)
def test_sse_indices_are_i64_not_coerced(token):
    with pytest.raises(ModelError):
        decode_response_event('{"type":"future","summary_index":' + token + "}")


@pytest.mark.parametrize(
    "token,kind",
    [
        ("-9223372036854775808", int),
        ("18446744073709551615", int),
        ("18446744073709551616", int),
        ("-9223372036854775809", int),
        ("-0", int),
        ("0e99999", WireNumber),
        ("1e-99999", WireNumber),
    ],
)
def test_value_integers_are_not_narrowed_to_f64(token, kind):
    value = materialize(loads_wire(token))
    assert type(value) is kind
    if kind is int:
        assert value == int(token) and math.isfinite(value)
    else:
        assert dumps_wire(value) == ("0e+99999" if token == "0e99999" else token)


@pytest.mark.parametrize("levels,valid", [(127, True), (128, False)])
def test_materialized_container_depth_limit(levels, valid):
    value = loads_wire("[" * levels + "0" + "]" * levels)
    if valid:
        materialize(value)
    else:
        with pytest.raises(ValueError, match="recursion"):
            materialize(value)


def test_valid_old_archive_bytes_are_not_rewritten():
    raw = '{ "type":"compaction_summary", "encrypted_content":"x", "unknown":1, "unknown":2 }'
    old = RemoteHistoryItem(raw, "old")
    restored = item_from_payload("remote_history", item_to_payload(old))
    assert restored.payload_json == raw
    with pytest.raises(ModelError, match="dedicated compaction history"):
        _to_response_input(restored)
    assert restored.payload_json == raw


@pytest.mark.parametrize(
    "value", [b"\xef\xbb\xbf{}", "{}".encode("utf-16"), "{}".encode("utf-32"), b'{"x":"\xff"}']
)
def test_wire_bytes_are_explicit_utf8(value):
    with pytest.raises(ValueError):
        loads_wire(value)
