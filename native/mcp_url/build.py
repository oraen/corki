"""Rebuild the portable URL artifact with Rust 1.95.0 and the committed lock."""

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
with tempfile.TemporaryDirectory(prefix="corki-mcp-url-build-") as target:
    subprocess.run(
        [
            os.environ.get("CORKI_URL_CARGO", "cargo"),
            "+1.95.0",
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
    destination = root.parents[1] / "src/corki/_native/mcp_url.wasm"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(target) / "wasm32-unknown-unknown/release/corki_mcp_url.wasm", destination)
    cargo = shutil.which(os.environ.get("CORKI_URL_CARGO", "cargo"))
    if cargo is None:
        raise RuntimeError("URL parser build requires Cargo")
    rustc = Path(cargo).with_name("rustc" + Path(cargo).suffix)
    sysroot = subprocess.check_output(
        [str(rustc), "+1.95.0", "--print", "sysroot"], env=environment, text=True
    ).strip()
    shutil.copyfile(
        Path(sysroot) / "share/doc/rust/COPYRIGHT-library.html",
        destination.with_name("MCP_URL_RUST_COPYRIGHT.html"),
    )
    print(destination)
