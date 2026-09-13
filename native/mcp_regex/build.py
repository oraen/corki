"""Rebuild the portable matcher artifact with Rust 1.85.1 and the committed lock."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent
environment = os.environ.copy()
cache = Path(environment.get("CARGO_HOME", str(Path.home() / ".cargo"))).resolve()
environment["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(
    (
        f"--remap-path-prefix={root.parents[1]}=/corki",
        f"--remap-path-prefix={cache}=/cargo",
    )
)
with tempfile.TemporaryDirectory(prefix="corki-mcp-regex-build-") as target:
    subprocess.run(
        [
            os.environ.get("CORKI_REGEX_CARGO", "cargo"),
            "+1.85.1",
            "build",
            "--locked",
            "--release",
            "--target",
            "wasm32-unknown-unknown",
            "--manifest-path",
            str(root / "Cargo.toml"),
            "--target-dir",
            target,
        ],
        check=True,
        env=environment,
    )
    destination = root.parents[1] / "src" / "corki" / "_native" / "mcp_regex.wasm"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        Path(target) / "wasm32-unknown-unknown/release/corki_mcp_regex.wasm", destination
    )
    print(destination)
