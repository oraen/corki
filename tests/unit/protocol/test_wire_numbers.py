import json
from decimal import Decimal

import pytest

from corki.context.hosted_output import project_hosted_items
from corki.models.response_wire import decode_response_event
from corki.protocol.items import HostedToolItem
from corki.protocol.truncation import TruncationPolicy
from corki.protocol.wire_json import loads_wire, materialize
from corki.protocol.wire_numbers import WireNumber, dumps_wire


@pytest.mark.parametrize(
    "source,canonical",
    [
        ("-0", "0"),
        ("-0.0", "-0.0"),
        ("1", "1"),
        ("1.25", "1.25"),
        ("1.2300", "1.2300"),
        ("1.234567890123456789", "1.234567890123456789"),
        ("1E20", "1e+20"),
        ("1e007", "1e+007"),
        ("1e-07", "1e-07"),
        ("1e-9999", "1e-9999"),
        ("-1E9999", "-1e+9999"),
        ("0e99999", "0e+99999"),
        ("9" * 5000, "9" * 5000),
        ("0." + "0" * 5000 + "1", "0." + "0" * 5000 + "1"),
        ("1e" + "9" * 5000, "1e+" + "9" * 5000),
    ],
    ids=lambda value: value if len(value) < 40 else f"long-token-{len(value)}",
)
def test_exact_tokens_roundtrip_without_float_or_integer_range_limits(source, canonical):
    value = materialize(loads_wire(source))
    assert dumps_wire(value) == canonical
    assert dumps_wire(materialize(loads_wire(dumps_wire(value)))) == canonical


@pytest.mark.parametrize(
    "value",
    [
        "",
        "NaN",
        "Infinity",
        "-Infinity",
        "+1",
        "01",
        "1.",
        ".1",
        "1e",
        "1\n",
        '1,"injected":true',
        "１",
        None,
    ],
)
def test_raw_number_constructor_cannot_inject_json_or_extensions(value):
    with pytest.raises(ValueError, match="invalid JSON number"):
        WireNumber(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_encoder_rejects_nonfinite_python_values(value):
    with pytest.raises(ValueError):
        dumps_wire({"nested": [value]})


def test_encoder_escapes_strings_and_preserves_shared_but_not_recursive_containers():
    shared = {'字"\n': [WireNumber("1e999"), "1e999", "$serde_json::private::Number"]}
    value = {"b": shared, "a": shared, "empty": (None, True, False, 1, 1.25)}
    encoded = dumps_wire(value, sort_keys=True)
    parsed = json.loads(encoded, parse_float=Decimal)
    assert (
        parsed["a"]
        == parsed["b"]
        == {'字"\n': [Decimal("1e999"), "1e999", "$serde_json::private::Number"]}
    )
    assert encoded.startswith('{"a":')
    assert "字" in encoded and "字" not in dumps_wire(value, ensure_ascii=True)
    shared["loop"] = value
    with pytest.raises(ValueError, match="circular"):
        dumps_wire(value)
    with pytest.raises(TypeError, match="key"):
        dumps_wire({1: "not a JSON object key"})
    with pytest.raises(TypeError):
        dumps_wire({"unsupported": object()})


@pytest.mark.parametrize("token", ["1.234567890123456789", "1e+999"])
def test_hosted_archive_projection_and_durable_decode_keep_numbers(token):
    raw = (
        '{"type":"response.output_item.done","item":{"type":"function_call_output","id":"note","output":"'
        + "x" * 200
        + '","internal_chat_message_metadata_passthrough":{"create_time":'
        + token
        + "}}}"
    )
    event = decode_response_event(raw)
    assert "internal_chat_message_metadata_passthrough" not in event["item"]
    archived = materialize(loads_wire(raw))
    item = HostedToolItem(dumps_wire(archived["item"]), "turn", "step")
    projected = project_hosted_items((item,), TruncationPolicy("bytes", 10))[0]
    assert projected.payload_json == item.payload_json and projected.model_payload_json is not None
    cold = HostedToolItem(
        projected.payload_json, "turn", "step", model_payload_json=projected.model_payload_json
    )
    for payload in (cold.payload_json, cold.model_payload_json):
        assert json.loads(payload, parse_float=str)[
            "internal_chat_message_metadata_passthrough"
        ] == {"create_time": token}
    assert "truncated" in json.loads(cold.model_payload_json)["output"]


def test_native_search_arguments_keep_numeric_literals_not_marker_objects():
    event = decode_response_event(
        '{"type":"response.output_item.done","item":{"type":"tool_search_call","call_id":"search","execution":"client","arguments":{"limit":1.0000000000000000000001,"q":"1e999"}}}'
    )
    identity = event["item"]["call_id"]
    arguments = dumps_wire(event["item"]["arguments"])
    assert identity == "search"
    assert arguments == '{"limit":1.0000000000000000000001,"q":"1e999"}'
