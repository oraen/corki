# Local file candidate helper

This standalone process only enumerates the current directory and scores local
paths. It does not import Codex services, credentials, account logic or tool-search
APIs. Corki owns process timeout, cancellation and reaping.

The dependency versions match the local Codex file-search lock: ignore 0.4.25 and
nucleo-matcher commit 4253de9faabb4e5c6d81d946a5e35a90f87347ee. The matcher uses
path scoring, case-insensitive queries and smart normalization. The walker allows
hidden paths, follows links, scopes gitignore to Git repositories, includes empty
directories and skips individual traversal errors. Only the best 100 candidates
are retained. An overall deadline fails explicitly instead of reporting a partial
walk as complete.

Build with Rust 1.95.0 (or a compatible newer toolchain). Install a fresh
development bundle with the build script (the destination must not already exist):

```sh
python native/file_search/build.py src/corki/_native/file_search
```

`--cargo` selects the Cargo executable; `RUSTC` selects the matching compiler when
they are not on PATH. `--offline` uses previously downloaded dependencies. The
installer builds locked sources, collects dependency and Rust library notices,
checks hashes and the binary platform, then publishes the complete directory.
It never replaces an existing bundle. Host artifacts are Git-ignored.

For a standalone executable without installation:

```sh
cargo build --locked --release --manifest-path native/file_search/Cargo.toml
```

The program reads one JSON request from stdin (`query`, optional `limit` between
1 and 100), searches its working directory, then writes a JSON candidate array.
An empty query does not scan. Input is limited to 16 KiB and 1,000 query characters;
paths containing control characters or invalid UTF-8 are not offered.

Set `CORKI_TEST_FILE_SEARCH` to the absolute built executable path to run
`tests/integration/test_file_search_native.py` and the native backend cases in
`tests/e2e/test_reference_completion_pty.py`. CLI wiring prefers a helper installed
at `src/corki/_native/file_search/corki-file-search` (`.exe` on Windows). Without
that artifact it uses the existing `rg` fallback (no empty-directory candidates,
different scoring). A standard wheel includes a verified installed bundle and
receives a native platform tag. Build the bundle before building a release wheel.
`CORKI_TEST_FILE_SEARCH_WHEEL` enables wheel extraction/default-entry tests.

Build-time platform validation currently supports macOS/Linux; only macOS arm64
execution and wheel tests have been run. Windows native packaging and Linux ABI
compatibility are not verified. Do not treat the local wheel as a published,
cross-platform release. The helper is one-shot per query, unlike Codex's reusable
index session, with an explicit three-second scan limit.
