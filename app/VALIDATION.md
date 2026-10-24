# Desktop acceptance tracking

Final macOS arm64 acceptance evidence is recorded below. The final regression
passed 31 Python tests, 21 Node tests and TypeScript checking. Fixture content,
screenshots, profiles and logs remain outside the repository. See ACCEPTANCE.md
for requirement coverage and README.md for runtime/platform limits.

## Evidence already obtained

- Independent app directory, dependencies, main/preload/renderer and Python bridge.
- No desktop-specific changes to Corki core.
- TypeScript checking and renderer/main/preload build.
- Python lint and formatting.
- Missing/malformed configuration handling in isolated homes.
- Request receipt deduplication and bounded event replay tests.
- Real AgentRuntime completion and canonical SQLite history.
- Cross-process writer lease rejection while a desktop runtime owns a thread.
- Real stdio process with local fake HTTP model: streamed answer, canonical read,
  orderly EOF shutdown.
- Real Runtime cancellation emits TurnCancelled before CancelledError; adapter
  treats that path as successful cancellation, not failure.
- Renderer event reduction: delta/completion identity, preserved partial answer
  on failure, raw reasoning excluded.
- Chromium UI smoke: layout, configured choices, send, Markdown, original copy,
  and a text selection retained across streamed updates then refreshed on release.
- Delayed submission UI tests: editing while awaiting acceptance or rejection does
  not lose the new draft; repeated Enter during admission submits only once.
  Sending is disabled until the selected model exists in the current catalog.
- Native Electron 44.8.0 on macOS arm64 with an isolated home and local HTTP fake
  model: actual Agent stream, single answer identity, switch away/back, stop,
  renderer reload, persisted Recents, IME Enter, draft reload, window close/reopen
  during execution. Window selection is saved before asynchronous history loading.
- Real core ElicitationRouter with the desktop handler: approval acceptance,
  rejection and server cancellation; cancelled requests remove pending UI state.
- Actual stdio split UTF-8, contiguous frames, invalid JSON, oversized unterminated
  frames, and closed output while the parent leaves input open (POSIX).
- Electron bridge protocol tests: split UTF-8, malformed event rejection, unexpected
  backend exit and graceful stdin EOF. No automatic task resubmission.
- Allowlist packaging produces an unsigned macOS application with the Corki icon
  and Python bridge outside ASAR. Packaged startup from a disposable cwd with
  PATH=/usr/bin:/bin, no CORKI_PYTHON/PYTHONPATH/VIRTUAL_ENV and a saved interpreter
  passes the actual Agent stream/switch/stop/reload, IME, draft and window reopen tests. The explicitly selected
  interpreter was first the development environment, then a new external virtual
  environment containing a non-editable Corki wheel and its separately installed
  dependencies. Imports and the verified native compiler resolve inside that
  external installation. Both passed the packaged execution/recovery tests.
- Packaged payload audit: ASAR contains only package metadata and built resources;
  bridge contained exactly four Python source files in that build; native Corki icon exists.
  The new content projection module requires a fresh payload check.
  Launching the package does not add Python bytecode/cache files to its resources.
  `app/node_modules`, `app/dist` and `app/out` are confirmed ignored by Git.

- Real Agent shell execution approval tests accept and decline actual harmless
  fixture commands. Native UI additionally exercises keyboard scope selection and
  session consent reuse, then confirms a new session asks again.
- A real stdio MCP server participates in native UI tests: tool approval, required
  fields, bounded integer validation, boolean defaults, array choices and results
  reaching the model. All owned fixture MCP process IDs are gone after app exit.
- Shell/patch approval scope is mapped through the existing Runtime API just as
  the CLI does. MCP form validation reuses the CLI's pure `validate_field` helper.
  Request kind, rather than a server-controlled name, selects the authority route.
- Native renderer-crash recovery retains Agent execution; Python process crash
  recovery marks an unknown outcome, preserves visible partial output, and does
  not issue another model request. Tested in development and packaged installations.
- Native lifecycle test: second instance exits and leaves one window; full app
  restart restores the draft; corrupt/off-screen bounds are sanitized and clamped;
  the fixture settings file is unchanged.
- Idle Runtime cache retirement test: inactive hosts close and can reopen canonical
  history; a host with background terminals is retained. Completed live buffers
  are cleared and per-thread admission locks use weak references.

- Native attachment acceptance passes with a >500 KB incompressible image:
  file selection, actual Chromium File drag/drop, text preview, reference-only
  persisted drafts and reload, unsupported-model rejection, malformed-image
  rejection, and exact original image bytes reaching the local fake model.
- Canonical image history supports both legacy attachments and the current core
  content_items normalization. Lazy previews, modal display and full Unicode
  answer copying after reload pass in real Electron. Preview pixels are bounded
  separately from the original; an incompressible RGBA regression fits the frame.
- Three content tests cover asset integrity/path scope/aged unreferenced cleanup,
  long canonical history byte pagination, complete original-text chunk retrieval,
  message ownership, local-only image previews and current/legacy image formats.
  The native image modal screenshot was visually inspected.

- Replay aggregate byte-budget regression passes: legal individual events cannot
  overflow a combined JSON-RPC replay response. An event larger than the replay
  cache is delivered live, and missing sequence ranges require snapshot recovery,
  including the empty-cache case.
- Native Electron security test proves renderer Node/IPC globals are absent,
  invalid methods/parameters/oversized requests/asset paths/unsafe external links
  are rejected, disposed subscriptions stop receiving events, arbitrary popups
  are denied, and another native window with the same origin and preload cannot
  invoke the owner's IPC. The legitimate main window still works afterward.

- Snapshot/live reconciliation now preserves canonical message previews and lazy
  text offsets instead of replacing them with old completion payloads. Tests
  cover named and anonymous delta overlap, a subsequent unfinished answer,
  completed versus still-running tools, and already-resolved questions. Current
  pending interactions own the wait status. Native Electron stream/switch/stop,
  renderer reload and window-reopen regression passed after this change.

- Disconnected admission caller regression passes: the original shielded admission
  finishes, removes its own in-flight bookkeeping, and retry with the same stable
  ID executes once. A restarted host resolves the durable completed receipt without
  constructing another Runtime or invoking the model.
- Shutdown while the factory or initial runtime preparation is awaiting work
  cancels admission, creates no receipt/turn task, and closes any acquired Runtime.
- Native Electron quit test uses a real exec_command spawning a process and child:
  declining quit preserves the active task; accepting quit removes both owned
  processes, while a separately spawned unrelated process remains alive.

- Chromium long-history test covers initial bottom positioning, upward reading
  during streamed output, return-to-latest, and prepending 30 older messages
  while the reader scrolls and more output arrives. It reproduced a scroll jump
  from stale request-time height arithmetic. The renderer now preserves the
  visible message DOM anchor at commit time; the regression passes. Screenshot
  inspected at 1440x900 with the populated sidebar and long message layout.

- Browser interaction tests cover question choice/free-text payload and password
  masking, patch content and prior-attempt details, approval scope, and URL-mode
  explicit link opening plus accept/decline/cancel. No link is opened automatically
  and opening alone never confirms. Pending interactions now render even when the
  message page is empty; the regression runs in that condition. URL screenshot
  inspected. Backend question routing and URL response actions pass separately.

- Native MCP URL integration now passes all three actions: accept, decline and
  cancel travel from the real desktop through the core router to a local stdio
  server and back into the subsequent model request; no external URL is fetched.
- Native apply_patch integration verifies no file exists before consent. With the
  fixture's read-only sandbox, initial acceptance produces a sandbox-denial retry
  review containing execution evidence; the file remains absent until separate
  retry approval. The accepted retry writes exactly the fixture content, while
  rejecting a fresh patch leaves its target absent. Owned MCP processes exit.

- Bounded large-message transport passes real Python-to-Node tests with concurrent
  multi-megabyte Unicode responses and events. Unit cases reject fragment gaps,
  repeats, mismatched sizes, interleaving, truncation and oversize budgets; abandoned
  assemblies expire and disposal clears timers. 23 Python tests and native
  Electron stream/switch/reload/stop/window-reopen regression pass with the new
  transport. Limits: 2 MiB frames, 64 MiB logical message, 16 queued frames,
  1,024 fragments and 30-second assembly timeout.

- Live answer restoration regression reproduces and fixes the former 64,000
  character tail-only buffer. Assistant text now coalesces by turn/message ID
  even with interleaved tool/reasoning output, preserving the beginning of an
  unfinished 100,000-character reply in an actual Service thread snapshot.
  Anonymous completion boundaries remain separate; tool/reasoning views retain
  their 64,000-character rolling limits. Live projection unit tests and all 17
  bridge tests pass. The new live.py module is in the packaging payload allowlist.

- UI lost-reply tests pass: an accepted request with a lost acknowledgement is
  reconciled without another submission; a preparing request preserves input and
  disables resend; explicit recheck after completion clears only the corresponding
  draft. Previously edited-next-input and explicit rejection cases still pass.
- Rebuilt unsigned macOS arm64 package includes all six bridge Python modules and
  the fragmented transport. Payload/icon/immutable-resource checks pass using the
  external non-editable Corki installation, restricted PATH and a disposable cwd.
  Packaged Agent execution, IME, drafts, switch/stop/reload/window reopening and
  renderer/backend crash recovery all pass with that independent interpreter.
- Preliminary source scan of 59 app files found no known private-company markers,
  hard-coded developer home or key-shaped secrets. Lockfile resolved hosts are
  exclusively registry.npmjs.org. This scan does not replace final source review.

- Browser Markdown/security test verifies tables, highlighted code, exact raw
  answer/code copying, script/event-handler removal, no automatic remote resource
  requests and explicit external-link dispatch. SVG/MathML are now excluded by
  an HTML-only sanitization profile; the window CSP remains an additional layer.
- Modal keyboard regression found initial input focus and Tab wrap gaps. Shared
  dialogs now focus their input, contain forward/backward Tab, close on Escape,
  and restore focus to the opener. The 1280x800 browser test passes together with
  safe Markdown and copying checks.

- Snapshot lifecycle race regression pauses the canonical read, commits the last
  answer and clears live state, then returns the old page. A lifecycle revision
  check now repeats the read and returns the actual final answer instead of an
  old page labelled with the new sequence. All 22 content/bridge tests pass.
- UI out-of-order snapshot test resolves the newest request first and older ones
  afterward. Per-thread request tickets prevent older results replacing the newer
  conversation view; failed requests release their tickets. Existing delayed/lost
  receipt and draft/selection tests still pass.

- Frontend event replay now has both count (512) and byte (1 MiB) bounds with an
  eviction/gap watermark and host-epoch separation. Unit tests cover exact boundary,
  oversized individual events, gaps and instance changes. Snapshots already ahead
  of delivered events remain valid; per-thread snapshot sequence prevents late
  already-included deltas being appended again.
- Browser race test holds a snapshot while 600 events arrive, exhausts the local
  replay range, and proves a second authoritative snapshot is requested. It then
  delivers a late event already covered by that snapshot and verifies no duplicate
  content. Native stream/switch/reload/stop/window-reopen regression passes.

- Host live projection now spools large strings into automatically owned temporary
  files with a 4 KiB in-memory threshold per text value. Assistant deltas append
  without repeatedly concatenating full replies; nested large tool payloads retain
  exact wire content. Logs/reasoning still keep only their rolling view, and the
  projection still keeps at most 200 events. Eviction closes the temporary file;
  these are not another durable conversation store. Four focused live tests, all
  27 prior Python tests, four real Node/Python bridge tests and native Electron
  stream/switch/stop/reload/window-reopen regression pass.

- Native model-selection test holds the first model stream while switching the UI
  to another provider/model, then verifies outgoing request models are A followed
  by B. Global settings bytes remain unchanged, reload restores B, and a new chat
  selects global default A. Removing B's provider disables sending and now shows
  an explicit reselect message. No model availability probe or third request occurs.

- Native directory/session-management test selects a Chinese/spaced path through
  the system-dialog bridge and executes real exec_command pwd there. Canonical
  thread cwd matches the resolved path and the existing-session directory control
  is disabled. Rename persists; deleting an active conversation requires stopping
  first, removes it from Recents afterward, leaves no running task and preserves
  a fixture project file. Recent directories refresh whenever the modal opens,
  fixing their previous startup-only cache.

## Final acceptance pass

Latest focused revalidation:
- Asynchronous state checks now use an awaited polling helper rather than an
  async `waitForFunction` predicate. Native attachment and full application
  lifecycle tests were rerun successfully with that correction.
- Native edge testing covers missing/malformed configuration, correction and
  reload, an empty successful model response, and explicit model failure while
  retaining the user's message. No real model provider is contacted.
- Directory, rename and delete dialog errors now render inside the dialog
  instead of behind it. A real Electron test enters a nonexistent directory,
  observes the in-dialog error and verifies no conversation was created, then
  selects a valid Chinese/spaced path and completes the existing cwd, rename,
  stop-before-delete and workspace-preservation checks. Type checking and build
  also passed after this change.

Verified in the latest acceptance pass:
- All 29 Python adapter/content/live/protocol tests and all 20 Node tests pass.
  The source tests cover byte-bounded canonical pagination with no duplicate or
  missing records, current-turn snapshot completion races, UTF-8 fragmentation,
  invalid frames, broken pipes, timeout disposal and replay-gap detection.
- The long-history browser scenario now streams 120 separate animation-frame
  chunks (over 100,000 characters), while editing the next prompt. Draft content
  survives and the reading anchor remains stable. This is a bounded interaction
  check, not a throughput benchmark or a claim of unlimited history performance.
- The writer-conflict integration test now holds the same canonical lease used
  by CLI before desktop admission. Desktop rejects submission without a receipt
  or running task; after lease release the same request runs successfully.
- The current macOS arm64 package was rebuilt. Its payload allowlist, icon,
  immutable bridge resources and launch with an independent installed wheel,
  restricted PATH and external cwd all pass. Packaged Agent stream, stop,
  IME, window reopen, renderer reload, persisted history, renderer crash and
  backend-crash reconciliation also pass.

- Two additional real Runtime integration cases now cover desktop goal execution:
  a first reply followed by automatic continuation that calls update_goal, and
  cancellation while that continuation is inside the model. Each emits two
  distinct turn IDs, one host settlement, exactly one actual user input and no
  extra model calls. Final receipts point to the last turn. Normal completion
  records complete; cancellation uses the core's interrupted behavior and records
  paused, with a cancelled receipt. A frontend state test separately verifies that
  intermediate TurnCompleted/TurnCancelled events do not end the host run before
  run.settled. Both integration cases and all eight state tests pass. No production
  implementation change was needed for these semantics.

Final desktop layout/keyboard review passed: native 1280×800, 1440×900,
1920×1080, 125% zoom, undo/redo, select/copy/paste, Shift+Enter, persisted sidebar
and New Chat shortcut. Capture used Electron capturePage for correct scaled
screenshots; inspected wide and zoomed views retain visible composer controls.
README now documents the supported runtime, attachment bounds and platform
limits. ACCEPTANCE.md reconciles the fourteen-part specification with sources
and verification. At the time of this initial desktop review, Windows/Linux
were unverified; later Windows 11 x64 packaged Agent checks passed with an
isolated fake model. Windows 10 and Linux remain unverified. No user settings
or real model requests were used in this acceptance testing.

## Deliverable hygiene review

The candidate inventory at this review contained 66 files (54 UTF-8 text files
and 12 local image resources), excluding ignored dependencies/builds/packages.
Text scanning found no known internal company/project names, private home paths
or credential signatures. All 184 lockfile resolved dependency URLs use
registry.npmjs.org without URL credentials. Other addresses are official Electron
documentation, package funding metadata, loopback fixtures or reserved
example.invalid test cases. This scan supports, but does not replace, source
review. The avatar had non-rendering EXIF/Photoshop segments: removed without
recompression and verified identical decoded pixel bytes. Other image resources
had no EXIF fields. The rebuilt ASAR's avatar exactly matches the cleaned source.
The new ACCEPTANCE.md maps all fourteen specification sections to scoped evidence
and records the final review scope and supported-platform limits.

## Archive and hard deletion

- The Recents menu now separates Archive (retained canonical history) from Delete.
- `test_thread_deletion.py` verifies canonical and ancillary row removal,
  transcript removal, retained unrelated records/project files, desktop cleanup,
  idempotent retry, stale submission rejection, writer conflicts and refusal to
  follow a symlinked transcript directory. All tests use disposable homes.
- The Electron thread test verifies stop-before-delete, confirmation cancellation,
  an actual SQLite row-count check, 24-hour suppression after renderer reload,
  expiration restoring confirmation, and archive retaining history. A temporary
  screenshot of the confirmation dialog was visually inspected.
- Python bridge/deletion tests: 21 passed. Node protocol/state tests: 21 passed.
  TypeScript checking, build and targeted Python lint passed.
- Shared memory artifacts and workspace files are outside session deletion scope.
  SQLite frees pages for reuse rather than promising an immediate smaller file.

## Live backend compatibility and immediate submitted input

- The initialization handshake advertises thread-management version 2. New UI
  checks it before reminder access or destructive session actions; archive-only
  older hosts get an explicit restart message instead of executing old semantics.
- Submitted text renders immediately, before the admission round trip. The bridge
  retains a bounded transient submitted-input projection until canonical history
  catches up. Deduplication uses turn identity, not matching text.
- Real Electron smoke testing delays model output and asserts the user's input
  is visible first, remains single after completion, and does not leak into New
  Chat. Session switching, window reopen, cancellation and reload also passed.
- Targeted bridge/deletion tests: 23 passed. Node logic/protocol tests: 23 passed.
  TypeScript and targeted Python lint passed. All runtime testing used isolated
  homes and local fake models.

## History refresh and streaming performance

- Removed the second admission snapshot read and the TurnStarted history read.
  Concurrent reads for the same host/session share one pending request; a newer
  required event sequence still forces a fresh read when necessary.
- Snapshot merges retain an overlapping loaded history prefix and its oldest
  pagination cursor. Unchanged message objects retain identity for React.memo.
  Disjoint pages reset to the authoritative page rather than hiding a history gap.
- Stream events are applied once per animation frame, with a 50 ms hidden-window
  fallback and a 128-event flush boundary. Control/terminal events flush promptly.
  Snapshot watermarks include replayed events to prevent buffered double application.
- Status-only/unhandled events no longer copy the message array unnecessarily.
- Browser instrumentation verified zero history reads for 120 sustained streamed
  chunks; settlement performed one read and retained 90 loaded historical messages
  plus the answer. Pagination/read anchors and composing during streaming passed.
- Real Electron smoke flows passed. 28 Node tests and TypeScript checking passed.

## Stopped replies and follow-up requests

- A cancelled turn preserves incomplete visible assistant text in desktop.db,
  separate from canonical model history. A stopped marker is placed at that turn.
  Completed items remain canonical; raw reasoning is never saved as reply text.
- Isolated bridge tests cover stopping, reopening, bounded text previews, full
  copy paging, a subsequent turn and permanent deletion of the display supplement.
- Native Electron smoke testing covers partial text retention, the stopped marker,
  renderer reload and a successful follow-up using a local fake model.
- Core change: item_metadata excludes TurnAbortedItem from local wire-ID annotation.
  Its durable local ID may contain a colon, which Responses rejects. Codex likewise
  submits interruption context as a message fragment, without that local event ID.
  Fixing serialization avoids rewriting existing histories or duplicating model
  conversion in the desktop adapter. A synthetic upstream request with a colon ID
  returned 400; the equivalent request without the ID returned 200.
- Text discarded by older versions before persistence cannot be recovered.

## Transcript grouping, historical errors and waiting state

Reference inspection preceded implementation. These are packaged modules from the
locally installed ChatGPT.app archive, not claims about unpublished source:

- `local-conversation-thread-turn-entries-602db2846c41.js`: function `y` creates
  entries with `turnId`, `turnKey`, and per-turn state. Adopted turn-scoped errors
  instead of appending every failed historical turn to the transcript tail.
- `split-items-into-render-groups-455d213e44db.js`: function `l` separates
  `agentItems`, `assistantItem`, and `systemEventItem`, examining `final_answer`
  and the final eligible assistant item. `system-error` stays inside that turn.
  Corki's live completion events do not carry the same phase/render-group protocol;
  the desktop projects earlier assistant messages followed by tool activity or a
  later answer as compact progress text, preserving every original message.
- `agent-activity-item-fa8288319d19.js`: `ln` groups exec/patch/MCP activity;
  exec records already expose output and execution state. Corki instead receives
  separate call/result items, so its display projection pairs by turn + call ID,
  including out-of-order parallel completion. Orphan paged results stay visible.
- `command-execution-command-fda2ded549ba.js`: `t` selects a meaningful command
  from command actions, with a raw command fallback. Corki uses its actual `cmd`
  argument, bounded-preview fallback and CSS ellipsis with a full-title tooltip.
  It does not copy shell-wrapper parsing which assumes different input records.
- No directly reusable waiting-indicator reducer was established in these modules.
  Corki derives waiting from accepted/submitting state and current-turn visible
  output, with explicit approval/error/stop exclusions. An earlier turn's terminal
  state cannot suppress the next submission. Tool continuation shows waiting again.

Implementation remains under app/. Historical errors are inserted in the bridge
snapshot at their own turn boundary, including pages older than 60 turns. Live
TurnFailed uses the same stable ID to prevent duplicate error rows. Runtime history,
model requests, tool execution and error records are not rewritten or deleted.

Targeted validation covers old errors across 65 turns/pagination, streamed and
canonical message reconciliation, parallel tool pairing, commentary/final answer
separation, bounded arguments, first-response/continuation waiting, safe highlighted
JSON/commands, copy, and 1280/1440 desktop widths. Native Electron smoke verified
actual runtime streaming, stop/reload/follow-up, switching, IME and window reopen.
Screenshots, extracted reference code and disposable data remain outside the repo.

## Answer avatar anchoring

- Avatar ownership is derived per turn from the first visible assistant text.
  Converting that text into progress commentary does not remove its avatar, and
  later answers do not move or duplicate it. The bottom waiting indicator has no
  avatar. A turn stopped before any text uses the stopped marker as its fallback.
- Tests cover progress/tool/final/cancel transitions, separate turns, no-text stop,
  DOM placement and absence of avatars in the waiting indicator. Native smoke
  checks the partial answer avatar both after cancellation and after reload.

## Tool duration

The expanded output header displays elapsed time (for example `2h 3m 21s`).
Completed durations use the core tool_executions started_at/completed_at ledger,
scoped to the conversation and call; snapshot reads batch the visible calls.
Running calls update once per second from the host start timestamp. Missing
historical timing stays absent instead of guessing from output text. Tests cover
hour/minute/second formatting, ledger ownership, running entries and the rendered
header. No core schema or execution behavior is changed.

## Transcript folding

Execution process blocks stay open while their turn runs and default to closed
on completion. Opening a child tool/reasoning item pins its parent open; explicit
parent open/close choices take precedence. These are view-only states: no messages
or runtime history are changed. Finished user questions and assistant answers
have independent collapse controls and single-line previews. Targeted browser
checks cover automatic completion collapse, preserving manually opened children,
reopening process blocks, and collapsing/restoring both message roles.

## Automatic older-history loading

Scrolling up to the top requests the next history page without a button. An
upward wheel gesture also works when the current page is shorter than the viewport.
A synchronous in-flight guard prevents duplicate requests. The header shows a
rotating gear while loading and an exhausted-history message when next is null.
The existing commit-time reading anchor is preserved; process-group headings are
included as anchors. Browser tests cover automatic loading, repeated top gestures,
scrolling during a pending request, exhausted state and 120 concurrent stream chunks.
