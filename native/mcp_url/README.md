# MCP URL parser

This no-import WebAssembly bridge uses `url = 2.5.8`, with transitive versions and
checksums copied from pinned Codex's Cargo.lock. It provides HTTP URL normalization,
Agent Plugin endpoint validation, and redirect reference resolution. Configuration
keeps its original URL spelling; only Agent Plugin transport requests are normalized.
Ordinary MCP URL parsing is not changed by this bridge.

Build with Rust 1.95.0 and `wasm32-unknown-unknown`:

```sh
python native/mcp_url/build.py
```

`CORKI_URL_CARGO` can select an isolated rustup/Cargo proxy. The committed lock is
required; source and Cargo cache paths are remapped, and compilation uses a temporary
target directory. The build also copies the pinned Rust standard-library copyright
notice. Normal wheel installation includes the Wasm artifact and needs the existing
Wasmtime dependency, not Rust, Cargo, WASI, or network access.

The host caches compiled module code, not input URLs. Each call owns and closes its
Store, with no imports, 256 MiB memory and 100 million fuel units. These host ceilings
can reject exceptionally large native-valid input; a resource/engine failure disables
the affected MCP declaration and never falls back to an unvalidated parser. This is
computation isolation only, not a sandbox for MCP servers or tool effects.

`src/corki/_native/MCP_URL_LICENSES.txt` contains dependency license notices, including
build-only packages conservatively. MIT options are selected where available; ICU
components retain Unicode-3.0 notices. `MCP_URL_RUST_COPYRIGHT.html` reproduces the
standard library notice. Bridge source is Apache-2.0.
