import json

import pytest

from corki.models.base import ModelError
from corki.models.response_wire import decode_response_event
from corki.protocol.compaction import compaction_payload
from corki.protocol.response_items import response_item_payload
from corki.protocol.wire_json import loads_wire, materialize
from corki.protocol.wire_numbers import dumps_wire

NUMBER = "$serde_json::private::Number"
RAW = "$serde_json::private::RawValue"
META = "internal_chat_message_metadata_passthrough"


def generic(raw):
    return materialize(loads_wire(raw), preserve_pairs=False)


@pytest.mark.parametrize("number", [2**64, 2**128 - 1, -(2**63) - 1, -(2**127)])
@pytest.mark.parametrize("kind", ["response.output_item.done", "response.output_item.added"])
def test_value_to_content_checks_unknown_nested_fields_but_not_envelope_values(number, kind):
    item = {"type": "compaction", "encrypted_content": "x", "unknown": [{"nested": number}]}
    with pytest.raises(ModelError):
        decode_response_event(json.dumps({"type": kind, "item": item}))
    # No internally tagged ResponseItem buffering at these generic Value boundaries.
    for field in ("metadata", "response", "headers", "safety_buffering"):
        event = decode_response_event(json.dumps({"type": kind, field: item}))
        assert event[field] == item
    assert decode_response_event(json.dumps({"type": "future", "item": item}))["item"] == item


@pytest.mark.parametrize("number", [2**64 - 1, -(2**63), 2**128, -(2**127) - 1, 1.0, True])
def test_buffered_value_is_not_a_global_64_bit_numeric_limit(number):
    item = {"type": "compaction", "encrypted_content": "x", "unknown": number}
    assert (
        decode_response_event(json.dumps({"type": "response.output_item.done", "item": item}))[
            "item"
        ]
        == item
    )


def test_function_argument_strings_are_not_parsed_by_the_content_visitor():
    item = {
        "type": "function_call",
        "name": "tool",
        "call_id": "call",
        "arguments": '{"value":18446744073709551616}',
    }
    assert (
        decode_response_event(json.dumps({"type": "response.output_item.done", "item": item}))[
            "item"
        ]
        == item
    )


@pytest.mark.parametrize("key", [NUMBER, RAW])
@pytest.mark.parametrize(
    "value,canonical",
    [("1.2300", "1.2300"), ("1e999", "1e+999"), ("-0", "0"), (str(2**64), str(2**64))],
)
def test_generic_private_numbers_use_exact_number_values(key, value, canonical):
    assert dumps_wire(generic(json.dumps({key: value}))) == canonical


@pytest.mark.parametrize("key", [NUMBER, RAW])
@pytest.mark.parametrize("value", [1, None, {}, "NaN", "1 2", "1x", ""])
def test_invalid_private_value_is_not_an_ordinary_map(key, value):
    with pytest.raises(ValueError):
        generic(json.dumps({key: value}))


@pytest.mark.parametrize(
    "raw",
    [
        '{"$serde_json::private::Number":"1","extra":2}',
        '{"$serde_json::private::Number":"1","$serde_json::private::Number":"2"}',
        '{"$serde_json::private::RawValue":"1","extra":2}',
        '{"$serde_json::private::RawValue":"1","$serde_json::private::RawValue":"2"}',
        '{"$serde_json::private::Number":" 1 "}',
    ],
)
def test_private_classifiers_consume_exactly_one_entry_and_number_has_no_padding(raw):
    with pytest.raises(ValueError):
        generic(raw)


@pytest.mark.parametrize("key", [NUMBER, RAW])
def test_only_first_key_classifies_and_unknown_raw_typed_fields_stay_ignored(key):
    ordinary = {"first": 1, key: "not valid JSON or a number"}
    assert generic(json.dumps(ordinary)) == ordinary
    duplicated = '{"first":1,"' + key + '":"bad","first":2}'
    assert generic(duplicated) == {"first": 2, key: "bad"}
    raw = {"type": "compaction", "encrypted_content": "x", "unknown": {key: "bad"}}
    assert compaction_payload(json.dumps(raw)) == {"type": "compaction", "encrypted_content": "x"}


def test_raw_value_restarts_json_decoding_without_reinterpreting_ordinary_strings():
    nested = {"value": {RAW: ' {"n":1.2300,"s":"1e999"} '}}
    assert dumps_wire(generic(json.dumps(nested))) == '{"value":{"n":1.2300,"s":"1e999"}}'
    assert generic(json.dumps({RAW: '"ordinary string"'})) == "ordinary string"
    assert generic(json.dumps({RAW: "null"})) is None
    with pytest.raises(ValueError):
        generic(json.dumps({RAW: '"\\ud800"'}))


@pytest.mark.parametrize("value", ["1.2300", str(2**64)])
def test_typed_raw_number_accepts_number_map_but_only_sse_value_accepts_raw_map(value):
    base = {"type": "compaction", "encrypted_content": "x"}
    raw = {**base, META: {"create_time": {NUMBER: value}}}
    assert (
        dumps_wire(compaction_payload(json.dumps(raw)))
        == dumps_wire(base)[:-1] + ',"' + META + '":{"create_time":' + value + "}}"
    )
    raw[META]["create_time"] = {RAW: value}
    with pytest.raises(ValueError):
        compaction_payload(json.dumps(raw))
    event = decode_response_event(json.dumps({"type": "response.output_item.done", "item": raw}))
    assert META not in event["item"]
    assert response_item_payload(event["item"]) == base


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("tool_search_call", "arguments", {NUMBER: "1e999"}),
        ("tool_search_call", "arguments", {RAW: '{"query":"calendar"}'}),
        ("tool_search_output", "tools", [{RAW: '{"type":"function","name":"calendar"}'}]),
    ],
)
def test_raw_typed_value_fields_classify_after_content_buffering(kind, field, value):
    raw = {
        "type": kind,
        "execution": "client",
        "status": "completed",
        "role": "assistant",
        field: value,
    }
    result = response_item_payload(materialize(loads_wire(json.dumps(raw))))
    assert dumps_wire(result[field]) == dumps_wire(generic(json.dumps(value)))


def test_invalid_private_value_inside_discarded_known_item_is_still_a_typed_error():
    raw = {"type": "tool_search_call", "execution": "client", "arguments": {NUMBER: "invalid"}}
    with pytest.raises(ValueError):
        response_item_payload(materialize(loads_wire(json.dumps(raw))))
