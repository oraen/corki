"""Offline differential check: pass the executable compiled from f32_oracle.rs.

Compile checksum-verified zmij 1.0.19 src/lib.rs with rustc --edition=2021
--crate-name zmij --crate-type rlib -O --out-dir <temporary-directory>, then
compile the sibling f32_oracle.rs with --extern zmij=<directory>/libzmij.rlib.
No Cargo resolution, Codex workspace mutation, or production native dependency.
"""

import argparse
import hashlib
import json
import random
import subprocess
from pathlib import Path

from corki.mcp.float_projection import _bits, _shortest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("oracle", type=Path)
    args = parser.parse_args()
    rng = random.Random(246)
    integers = [rng.randrange(-(2**63), 2**64) for _ in range(10000)]
    integers += [
        (1 << power) + (1 << (power - 24)) + delta
        for power in range(24, 64)
        for delta in (-1, 0, 1)
    ]
    bits = [rng.randrange(0x7F800000) | (rng.randrange(2) << 31) for _ in range(20000)]
    bits += [
        ((exponent << 23) + delta) | sign
        for exponent in range(1, 255)
        for delta in (-1, 0, 1)
        for sign in (0, 1 << 31)
    ]
    bits += [0, 1, 2, 3, 0x7FFFFF, 0x800000, 0x7F7FFFFF, 1 << 31]
    cases = [("i" if v < 0 else "u", v) for v in integers] + [("b", b) for b in bits]
    payload = "".join(f"{mode} {value}\n" for mode, value in cases)
    process = subprocess.run(
        [str(args.oracle.resolve())],
        input=payload,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    for (mode, value), answer in zip(cases, process.stdout.splitlines(), strict=True):
        expected_bits, expected_token = answer.split()
        actual = value if mode == "b" else _bits(value)
        assert (actual, _shortest(actual)) == (int(expected_bits), expected_token), (mode, value)
    print(
        json.dumps(
            {
                "cases": len(cases),
                "mismatches": 0,
                "input_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "oracle_output_sha256": hashlib.sha256(process.stdout.encode()).hexdigest(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
