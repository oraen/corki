# MCP matcher engine

The module also exports `hook_validate` and `hook_matches` for lifecycle hooks,
using pinned `regex = 1.12.3`, matching the reference Hook engine. These exports
perform unanchored Unicode regex search; MCP continues using the independent
regex-lite full-value exports below. The Python Hook caller handles wildcard and
ASCII literal alternatives before invoking regex. No protocol or service client
is included. Additional dependency licenses are in `_native/HOOK_REGEX_LICENSES.txt`.

This no-import WebAssembly module executes the exact `regex-lite = 0.1.8` dependency
used by pinned Codex, with its default parser/compiler limits and `\A(?:...)\z`
full-value wrapper. The Python host does not translate regex syntax. It validates
both the original pattern and the wrapped form before publishing policy.

Build with Rust 1.85.1 plus `wasm32-unknown-unknown`:

```sh
python native/mcp_regex/build.py
```

`CORKI_REGEX_CARGO` may name an isolated cargo/rustup executable. The committed lock
pins the upstream crate checksum. The script uses a temporary target directory and
copies only the generated module into `src/corki/_native/mcp_regex.wasm`. Normal wheel
installation ships that artifact and needs only the pinned Wasmtime Python dependency,
not Cargo, Rust, WASI or network access. The sdist includes this wrapper and lock.

The host caches immutable compiled module code, but creates and closes a fresh Store
for each operation. Guest linear memory is bounded to 256 MiB and execution to
100 million Wasm fuel units per operation. Those host ceilings can reject work that
native Codex would eventually finish; they never authorize a denied identity. Config
validation reports an engine/limit failure; an execution-time failure denies matching.
No guest imports or host file/network/process capabilities are provided. This is only
regex computation isolation, not a sandbox for tools or MCP servers.

The bundled crate uses its MIT license option, reproduced in
`src/corki/_native/REGEX_LITE_LICENSE.txt`. Wrapper code is Apache-2.0.
