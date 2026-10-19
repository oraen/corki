# Corki Desktop

Independent Electron desktop host. Application and protocol adapters live here;
Corki's runtime remains the authority for tools, model calls, permissions and
canonical conversation history. See [ARCHITECTURE.md](ARCHITECTURE.md) and the specification mapping in
[ACCEPTANCE.md](ACCEPTANCE.md).

## Development

Install the repository's Python dependencies in its development virtual
environment, then run in this directory:

```sh
npm ci --registry=https://registry.npmjs.org
npm start
```

If the package manager disables lifecycle scripts, allow Electron's installer
and esbuild's installer in your package manager policy before building.

The development host detects the repository virtual environment. Override with
`CORKI_PYTHON` pointing to an interpreter with Corki installed. No Manager server
or conversion gateway is started by this application.

## Checks

```sh
npm run typecheck
npm run build
npm test
```

The Node protocol tests use the repository's `.venv/bin/python`. From the repository
root, run `.venv/bin/python -m pytest app/tests/test_bridge.py app/tests/test_protocol.py app/tests/test_content.py app/tests/test_live.py`.
Tests use disposable homes, never real user configuration or conversations.

After building, run `node tests/electron-smoke.mjs` here for the native macOS
end-to-end test with a local fake model. To test the packaged executable:

```sh
CORKI_TEST_PACKAGED=out/Corki-darwin-arm64/Corki.app/Contents/MacOS/Corki node tests/electron-smoke.mjs
```

`node tests/electron-interactions.mjs` exercises real shell/patch approvals,
sandbox-denied patch retry, and local stdio MCP form and URL confirmations. `node tests/electron-lifecycle.mjs` checks single-instance behavior,
full restart/draft recovery and window bounds. Set `CORKI_TEST_RECOVERY=1` for the
native smoke's renderer/backend crash cases. Set `CORKI_TEST_PYTHON` to test a
separately installed interpreter instead of the development environment.

`node tests/electron-attachments.mjs` covers actual attachment import, original
image delivery, draft/history recovery and Unicode text copying.
`node tests/electron-security.mjs` checks IPC boundaries and listener disposal.
`node tests/electron-layout.mjs` checks desktop sizes, 125% zoom, standard edit
shortcuts, sidebar persistence and the New Chat shortcut.
`node tests/electron-models.mjs` checks per-turn model selection, config removal,
session restoration and unchanged global defaults.
`node tests/electron-threads.mjs` verifies directory selection, actual execution
cwd, rename and stop-before-delete without deleting workspace files.
`node tests/electron-edge.mjs` checks invalid configuration, recovery, empty
model replies and explicit model failures using a local fixture server.
`node tests/electron-exit.mjs` checks quit confirmation and owned execution-child
cleanup without affecting an unrelated process. Browser-focused checks are
`node tests/scroll.mjs`, `node tests/interaction-ui.mjs` and
`node tests/markdown-ui.mjs`.

After packaging, `node tests/package-check.mjs` verifies the payload allowlist,
icon, startup without shell activation and that bridge imports do not modify the
application bundle.

`node tests/ui-smoke.mjs` performs a focused UI/selection check using an installed
Google Chrome on macOS. Test profiles and screenshots stay in system temporary
directories, outside the repository.

## Packaging

`npm run package` creates an unsigned application for the current host platform
under `out/`. Packaging uses an allowlist staging directory; only the built UI,
Electron entry points, local resources and the Python bridge are included.
The first version uses a separately installed Corki Python environment. It does
not silently include the developer's virtual environment. On first packaged
launch, use “选择 Python 环境” to select its interpreter; the absolute interpreter
path is saved in the user's desktop preferences, not in the application bundle.
Finder launches therefore do not require shell activation or repository cwd.
A macOS example for installing a locally built Corki wheel separately:

```sh
# From the repository root:
uv build --wheel --out-dir /tmp/corki-wheels
python3 -m venv ~/.corki/app/python
# Substitute the wheel filename produced above:
~/.corki/app/python/bin/python -m pip install /tmp/corki-wheels/corki-VERSION-PLATFORM.whl
```

Select `~/.corki/app/python/bin/python` in the application's interpreter chooser.
This installs the core independently; the Electron package contains no Python
virtual environment or user configuration. This flow has been tested with a
non-editable wheel in a disposable environment outside the checkout.
Code signing, publishing and auto-update are not part of this local build.

## Data and lifecycle

- Existing configuration: `~/.corki/settings.json` (read only from this host).
- Existing Agent history: `~/.corki/sessions/corki.db`.
- Desktop-only drafts, titles, submission receipts and interrupted reply text: `~/.corki/app/desktop.db`.
- Referenced upload blobs: `~/.corki/app/attachments/`. Unreferenced blobs older
  than one day are collected; core history retains the original message content.
- Window/Python selection: `~/.corki/app/window.json`.
- Electron profile: `~/.corki/app/electron/`.
- `CORKI_HOME` isolates all of the above for tests.

Archive removes a conversation from Recents while retaining canonical history.
Delete permanently removes its canonical rows, transcript, per-turn checkpoints,
goal state, thread notes and desktop drafts/title/receipts. Running tasks must
stop first; another process's writer lease blocks both operations. A minimal
deleted-thread ID tombstone prevents stale desktop submissions from recreating
the conversation. Confirmation can be suppressed for 24 hours across restarts.
Workspace files, independent fork copies and shared consolidated memories are
not deleted. Unreferenced upload staging blobs follow the one-day cleanup above.
SQLite reuses freed pages; deletion does not promise immediate database file
shrinkage or forensic erasure. Ancillary cleanup precedes canonical deletion,
so failed/interrupted cleanup can be retried; it is not a cross-file crash-atomic
transaction.
Stopped replies retain their received text and an explicit stopped marker, including
after reopening the application. Incomplete text is a desktop display supplement,
not a fabricated completed model-history item; deleting the conversation removes it.
Closing a macOS window leaves execution with the host. Quitting asks before
stopping running tasks. A lost response is reconciled through a stable request
ID; unknown outcomes are never automatically replayed.

The host retains up to eight idle Runtime instances where possible. Active turns,
pending approvals and background terminals prevent retirement. Retired sessions
reopen through canonical history; transient Runtime/session approvals and MCP
connections are recreated rather than copied into a second permission system.

## Validated scope and limits

The macOS arm64 desktop implementation and core acceptance flows are verified.
Python adapter/protocol/content tests, Node protocol/state tests, native Electron
Agent execution and independent packaged execution all passed. See
[ACCEPTANCE.md](ACCEPTANCE.md) for the requirement mapping and
[VALIDATION.md](VALIDATION.md) for specific evidence.

- The package is unsigned. Windows/Linux have not been validated.
- Packaged execution requires a separately installed Corki Python interpreter;
  select it on first launch. It does not include a Python distribution.
- Model connectivity depends on the user's configured provider. Tests used local
  deterministic models, not private prompts or paid production endpoints.
- Image/text attachments are supported with a 1 MB per-file and 1.2 MB import
  batch limit, up to eight attachments; text files must be UTF-8. PDFs, OCR,
  upload services and remote image loading are not part of this version.
- Permissions inherit Corki settings. Configuration management remains in the
  existing manager. Choosing a model here changes the session, not global defaults.
- Stop invokes core cancellation; an interrupted goal follows the core's pause
  behavior. Editing the next input while running is supported; stop the current
  task before sending a new turn.
- Archive retains history; Delete removes session data without deleting project files.
- Unknown crash outcomes require reconciliation; the app does not guarantee
  exactly-once side effects or automatically rerun tasks after a backend crash.

## Reference design and core changes

The narrow sidebar, New Chat layout, bottom composer, isolated preload,
composition handling, subscription cleanup and scroll anchoring follow patterns
observed in the locally installed reference application. This is an independent
implementation; no proprietary bundle, account logic or cloud integration was
copied. Corki's local orange visual identity and existing resources are retained.

Desktop host code, tests, resources and build configuration are inside app/.
The general hard-delete store is in src/corki/storage/thread_delete.py: the
existing archive interface retains data and cannot implement physical deletion.
This store has no Electron/UI dependencies and uses existing writer leases.
A separately installed Corki runtime must include this module (upgrade/reinstall
the current project package when updating the desktop host).
Other pre-existing changes in the repository are not part of this delivery.

The desktop accepts `--corki-directory /absolute/workspace` on first launch and
through its single-instance activation. The manager's desktop quick-launch mode
uses this entry point. The path initializes a new-chat draft, never changes the
working directory of an existing Agent session, and never submits a prompt.
