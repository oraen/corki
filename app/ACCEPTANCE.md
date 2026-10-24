# Desktop requirement coverage

This maps the user's fourteen-part desktop specification to implementation and
verification. Number ranges below refer to that specification, not test counts.
This records the initial macOS arm64 desktop acceptance; it does not certify
Windows/Linux. Current Windows 11 verification and remaining platform limits
are described in README.md. Runtime tests
use disposable Corki homes and deterministic models. A fixture-only UI test proves
rendering behavior, not backend execution.

| Requirements | Implementation and evidence |
| --- | --- |
| I.1–8: standalone desktop, core chat scope | Independent app package, Electron main/preload and renderer; bridge creates LangGraphRuntime. Native smoke executes the Agent. Sidebar only New Chat/Recents; no account, cloud, commerce, voice or browser integration. |
| II.1–7,10: independent package and low intrusion | app/package.json, package-lock.json, bridge/service.py; desktop metadata separated from canonical history. No production core file changed for the desktop implementation. Existing unrelated worktree changes are not desktop changes. |
| II.8–9,11–13: owned process and versioned stdio | electron/bridge.ts, framing.ts, bridge/protocol.py; native Python framing tests exercise split UTF-8, multiple frames, oversized frames, large logical messages, shutdown and broken output. No CLI terminal parser, manager endpoint or fixed port. |
| III.1–9: reference study before implementation | ARCHITECTURE.md records observed bundle patterns and scope. Extracted reference modules remain in system temporary storage; no reference bundle is shipped. Existing core initialization, events, sessions and storage are reused by the bridge. Local bundle behavior is a design reference, not a claim of reading all proprietary source. |
| IV.1–13: layout, brand, local resources | renderer/style.css, components.tsx, local resources; native/browser screenshots cover 1280×800 and 1440×900. Collapsible sidebar, restrained orange actions, local Corki/crab avatars and provider icons. No CDN or decorative account UI. Native layout tests additionally cover 1920×1080 and 125% zoom, with screenshot inspection. |
| V.1–4,11–12: draft/new chat and directories | renderer/index.tsx; electron-threads verifies Chinese/spaced chooser path, actual exec cwd and invalid path feedback; test_bridge verifies default directory and admission. New blank view does not create canonical history; first input supplies title. |
| V.5–10: immutable workspace, rename/delete | Service.directory resolves/validates paths without shell interpolation; thread test verifies fixed cwd, rename, stop-before-delete and retained project file. Archive retains canonical history; Delete removes session records and ancillary stores, leaving project files untouched. |
| V.13–15: canonical persistence, writer exclusion, pagination | bridge/state.py only stores UI metadata/receipts. test_content checks canonical byte/count pagination and exact full-text reads. test_bridge verifies canonical writer lease exclusion in both directions and successful admission after release. |
| VI.1–10: defaults, model selection, refresh | bridge catalog uses real Corki settings and model capabilities; electron-models holds model A while selecting B, verifies request sequence A/B, unchanged global settings, restored session selection, new-chat default and removed-provider feedback. No availability probe. |
| VI.11–12: credential boundary | Catalog returns selected public fields only; tests assert fixture API key absent. Main/preload expose constrained calls; no configuration editor or manager reimplementation. |
| VII.1–6: input and draft safety | Native smoke covers IME, send, stop, switching and reload. UI smoke covers delayed/rejected/lost admission, editable draft preservation and no duplicate retry. Lifecycle test verifies full process restart. |
| VII.7: attachments | electron-attachments imports original image plus dropped text File, previews/removes attachments, rejects unsupported model/malformed image, checks exact bytes reaching model and history recovery. Content tests validate integrity, ownership, thumbnail bounds and cleanup. |
| VII.8–16: Markdown and message interaction | markdown-ui checks tables, highlighting, raw/code copy, explicit external links and blocked HTML/SVG/remote images. UI smoke verifies selection stability; state tests retain partial output on errors. React message memoization isolates unchanged messages. |
| VIII.1–8: Agent/tools/interactions | LangGraphRuntime is the executor. Native interactions exercises actual exec and patch approvals, sandbox retry, deny, session scope, MCP form/default/type/range handling and URL accept/decline/cancel. Only summary-channel reasoning is forwarded. |
| VIII.9–12: stop, next input and goals | Native stop calls core cancellation. Composer permits next-input editing with stop-then-send. Real Runtime goal tests cover multi-turn continuation, complete and interruption/paused behavior; frontend remains running until host settlement. No duplicate Agent loop or goal engine. |
| IX.1–7: task ownership and submission | Service owns tasks independently of window; UUID/digest receipts and canonical turn IDs. Native window reopen/reload tests, UI uncertain-outcome tests, admission cancellation/retry integration tests. |
| IX.8–12: replay/crash/bounds | Host epoch/sequence, bounded event buffers, snapshot reload on gaps, lifecycle revision checks; Node/Python and UI race tests. Packaged crash test preserves visible output and does not replay tools. Canonical terminal events determine final status. |
| IX.13, X.1–8: snapshots and scrolling | Core display pages, not reconstructed model context. scroll.mjs checks 90-message history, prepend anchor during concurrent user scrolling/output, follow-bottom and return-to-latest; then 120 frame-separated chunks with next-input editing. Selection/observer cleanup is covered by UI tests and component source review. |
| XI.1–6: Electron boundaries | contextIsolation, sandbox, nodeIntegration disabled; narrow preload, sender/frame/origin checks, CSP, navigation/popup denial and local-only renderer requests. electron-security probes invalid calls, foreign window access, listener disposal and unavailable Node globals. |
| XI.7–10: desktop controls/window state | Platform titlebar, drag exclusions and native edit menu. Lifecycle test verifies single instance, malformed/offscreen bounds and draft restart. Native edit/new-chat shortcuts, sidebar persistence and 125% zoom passed in electron-layout. |
| XI.11–16: lifecycle/platforms | Startup/retry/Python chooser, macOS close versus quit, crash reattachment and explicit quit confirmation. electron-exit verifies owned parent/child cleanup without killing an unrelated process. Other platforms use isolated paths but remain unverified. |
| XII.1–9: build/runtime/data | README supplies install, start, build, package and external Python instructions. Package check launches from unrelated cwd with restricted PATH and saved independent wheel interpreter; resources stay immutable. No user environment, secrets or fixed gateway bundled. |
| XII.10–12: repository hygiene | app/.gitignore excludes dependencies, packages, build products and logs. Lockfile resolved URLs are public npm registry. Deliverable text scan found no known internal domain/project/private-key signatures; image metadata audit removed unused EXIF/Photoshop metadata without pixel changes. Rebuilt package avatar matches the cleaned resource byte-for-byte. No commit/push/signing performed. |
| XIII.1–20: layered verification | VALIDATION.md and named tests above map configuration, directories, streams, drafts, uncertain submissions, crashes, models, approvals, leases, scroll, Markdown, attachments, IPC, shutdown and independent package execution. Current test count is evidence of executed cases, not a substitute for this mapping. |
| XIV.1–7: delivery | Runtime, app sources, independent package, README and architecture/validation notes are present. Final delivery scope and platform/runtime limitations are stated in README; no publishing or signing is performed. |

## Final review outcome

Desktop host, preload, protocol, persistence, content projection and runtime
adapter sources were checked against the boundaries above. Build inputs are
allowlisted; generated output, temporary research, screenshots and profiles are
excluded. Dependency records point to public registry sources. Test scripts use
isolated homes and synthetic input, and the only credential-like literals are
intentional fixture/invalid-URL cases. Raster metadata was reviewed separately.

Native layout checks pass at 1280×800, 1440×900, 1920×1080 and 125% zoom; screenshots
were inspected. Undo/redo, select/copy/paste, Shift+Enter, sidebar reload and the
New Chat shortcut pass. The rebuilt package includes the cleaned avatar without
any change to decoded pixels. The package runtime was previously validated with
an independent installed wheel; subsequent changes affect tests, documentation
and non-rendering image metadata only.

The implementation satisfies the requested first macOS desktop scope. Platform
limits, installed-interpreter requirement, supported attachments and unknown
crash-outcome semantics remain explicit in README, rather than being claimed as
unconditionally supported. This review does not certify unrelated repository
changes or guarantee absence of all future bugs.
