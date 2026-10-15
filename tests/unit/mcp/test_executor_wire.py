"""Executor-specific boundaries must not inherit the more permissive MCP decoder."""

import base64
import json

import pytest

from corki.mcp.executor_wire import (
    MAX_DELTA_BYTES,
    BodyDelta,
    ExecutorProtocolError,
    HttpEnvelope,
    decode_bytes,
    decode_packet,
    request_id,
)
from corki.protocol.wire_numbers import WireNumber, dumps_wire


@pytest.mark.parametrize(
    "raw",
    [
        '{"id":1,"id":2}',
        '{"result":{"x":1,"x":2}}',
        "[]",
        '{"x":NaN}',
        '{"x":"\\ud800"}',
        b'{"x":"\xff"}',
        '{"x":' + "[" * 130 + "0" + "]" * 130 + "}",
    ],
)
def test_invalid_executor_json_rejected(raw):
    with pytest.raises(ExecutorProtocolError):
        decode_packet(raw)


def test_value_preflight_counts_values_not_keys_or_braces_inside_strings(monkeypatch):
    monkeypatch.setattr("corki.mcp.executor_wire.MAX_RPC_VALUES", 4)
    value = {"braces": '[{},\\"', "array": [None]}
    assert decode_packet(json.dumps(value)) == value
    with pytest.raises(ExecutorProtocolError, match="value limit"):
        decode_packet(json.dumps({**value, "extra": True}))


@pytest.mark.parametrize("value", [True, False, None, 1.0, 1 << 63, -(1 << 63) - 1])
def test_rpc_identity_is_strict(value):
    with pytest.raises(ExecutorProtocolError):
        request_id(value)


def test_string_and_integer_identities_do_not_alias():
    assert request_id("1") == "1" and request_id(1) == 1


def test_arbitrary_precision_values_survive_executor_decode():
    value = decode_packet(
        '{"method":"numbers","params":{"decimal":1.234567890123456789,"exponent":1e999,"largeInteger":18446744073709551616}}'
    )
    assert value["params"] == {
        "decimal": WireNumber("1.234567890123456789"),
        "exponent": WireNumber("1e999"),
        "largeInteger": 18446744073709551616,
    }
    assert decode_packet(dumps_wire(value)) == value


def test_raw_value_reuses_wrapper_node_and_shares_outer_budget(monkeypatch):
    encoded = '{"method":"raw","params":{"$serde_json::private::RawValue":"[0,1]"}}'
    monkeypatch.setattr("corki.mcp.executor_wire.MAX_RPC_VALUES", 5)
    assert decode_packet(encoded) == {"method": "raw", "params": [0, 1]}
    monkeypatch.setattr("corki.mcp.executor_wire.MAX_RPC_VALUES", 4)
    with pytest.raises(ExecutorProtocolError, match="value limit"):
        decode_packet(encoded)


def test_private_number_map_uses_native_number_visitor():
    assert decode_packet('{"x":{"$serde_json::private::Number":"1e999"}}') == {
        "x": WireNumber("1e999")
    }


@pytest.mark.parametrize("raw", ['{"id":1.0}', '{"id":1e0}'])
def test_typed_id_does_not_accept_floating_syntax(raw):
    with pytest.raises(ExecutorProtocolError):
        request_id(decode_packet(raw)["id"])


def test_negative_zero_follows_arbitrary_precision_value_visitor_not_raw_typed_rule():
    assert request_id(decode_packet('{"id":-0}')["id"]) == 0


def test_large_number_token_does_not_depend_on_python_integer_digit_limit():
    token = "9" * 5000
    value = decode_packet('{"future":' + token + "}")
    assert value == {"future": WireNumber(token)}
    assert decode_packet(dumps_wire(value)) == value


@pytest.mark.parametrize("encoded", ["@@", "Zg", "Zh==", "Zg===", " Zg==", "Zg==\n"])
def test_base64_requires_native_canonical_encoding(encoded):
    with pytest.raises(ExecutorProtocolError):
        decode_bytes(encoded)


def test_delta_decoded_limit_is_enforced():
    packet = {
        "requestId": "http-1",
        "seq": 1,
        "deltaBase64": base64.b64encode(b"a" * (MAX_DELTA_BYTES + 1)).decode(),
    }
    with pytest.raises(ExecutorProtocolError):
        BodyDelta.parse(packet)
    packet["deltaBase64"] = base64.b64encode(b"a" * MAX_DELTA_BYTES).decode()
    assert len(BodyDelta.parse(packet).data) == MAX_DELTA_BYTES


@pytest.mark.parametrize(
    "field,value",
    [("seq", True), ("seq", -1), ("seq", 1 << 64), ("requestId", 1), ("done", 1), ("error", False)],
)
def test_delta_typed_fields(field, value):
    packet = {"requestId": "http-1", "seq": 1, "deltaBase64": ""}
    packet[field] = value
    with pytest.raises(ExecutorProtocolError):
        BodyDelta.parse(packet)


@pytest.mark.parametrize(
    "name,value",
    [("bad:name", "ok"), ("x-test", "bad\nvalue"), ("x-test", "bad\x7fvalue"), ("", "value")],
)
def test_http_envelope_rejects_invalid_header_bytes(name, value):
    with pytest.raises(ExecutorProtocolError):
        HttpEnvelope.parse(
            {"status": 200, "headers": [{"name": name, "value": value}], "bodyBase64": ""}
        )


def test_http_envelope_preserves_order_duplicates_and_utf8():
    result = HttpEnvelope.parse(
        {
            "status": 200,
            "headers": [{"name": "x-test", "value": "你好"}, {"name": "x-test", "value": "second"}],
            "bodyBase64": "",
        }
    )
    assert result.headers == ((b"x-test", "你好".encode()), (b"x-test", b"second"))
