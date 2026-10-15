"""Pinned zmij 1.0.19 oracle vectors, including notation and subnormal boundaries."""

import struct

import pytest

from corki.mcp.float_projection import project_f32
from corki.protocol.wire_numbers import WireNumber, dumps_wire, loads_number_values


@pytest.mark.parametrize(
    "bits,expected",
    [
        (0, "0.0"),
        (2147483648, "-0.0"),
        (1, "1e-45"),
        (2, "3e-45"),
        (3, "4e-45"),
        (8388607, "1.1754942e-38"),
        (8388608, "1.1754944e-38"),
        (2139095039, "3.4028235e+38"),
        (864026624, "5.9604645e-8"),
        (897988541, "0.000001"),
        (897988542, "0.0000010000001"),
        (897988543, "0.0000010000002"),
        (841731191, "1e-8"),
        (1232348160, "1000000.0"),
        (1399379109, "1000000000000.0"),
        (1399379110, "1000000060000.0"),
        (1399379111, "1000000100000.0"),
    ],
)
def test_public_binary32_uses_native_shortest_token(bits, expected):
    value = struct.unpack(">f", bits.to_bytes(4, "big"))[0]
    projected = project_f32(value)
    assert dumps_wire(projected) == expected
    assert dumps_wire(project_f32(projected)) == expected
    assert dumps_wire(project_f32(loads_number_values(expected))) == expected


@pytest.mark.parametrize(
    "value",
    [
        True,
        [],
        {},
        "1",
        float("nan"),
        float("inf"),
        float("-inf"),
        1e300,
        -(2**63) - 1,
        2**64,
        WireNumber("1e+9999"),
        WireNumber("0.50"),
        WireNumber("1e-08"),
    ],
)
def test_non_numeric_nonfinite_and_noncanonical_public_tokens_are_rejected(value):
    with pytest.raises(ValueError):
        project_f32(value)
