# Corki

Corki is a Python/LangGraph implementation of a Codex-style coding-agent
harness. It provides a streamed model/tool loop, canonical conversation items,
durable LangGraph checkpoints and resume, automatic context compaction,
provider capability profiles, explicit planning state, and coding tools with
cancellation, PTY support, concurrency declarations, and idempotent records.

## Install for development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Start the interactive CLI from any working directory:

```bash
corki
corki resume                 # latest thread for this directory
corki resume THREAD_ID       # explicit persisted thread
```

Press `Ctrl+C` to leave the CLI. `Enter` submits a message and `Esc` followed
by `Enter` inserts a newline. The built-in `/help`, `/status`, and `/clear`
commands are handled locally.

Enable live text steering with `/realtime on` (or `[realtime].enabled = true`). While a response
is streaming, submit a newer instruction to interrupt that sample and continue the same durable
turn with rebuilt context. This is text steering, not microphone/audio realtime.
Turn finalization checks queued input and durable input not yet included in the
model request. Once input closes, `runtime.steer()` raises
`RealtimeTurnClosedError`; the CLI retains that submission for the next turn.
Accepted inputs not processed before cancellation/failure are saved to history
without running another model or tool. Queue acceptance alone is not a disk
commit: a process crash before persistence can still lose an in-memory input.

`runtime.cancel_active()` cancels the active model/tool work in ordinary and
realtime modes. The owned turn task performs cleanup and terminal persistence
without waiting for event consumption. `runtime.aclose()` joins this work before
closing shared dependencies; concurrent close calls await one shielded teardown.
Python tool handlers must cooperate with cancellation: an arbitrary coroutine
that suppresses cancellation cannot be forcibly terminated by this mechanism.

First-use initialization also has an owned task. `/stop`, caller cancellation, and
runtime close cancel pending initialization and wait for rollback before returning;
repeated cancellation does not abandon cleanup or an already-started thread-record
write. A cancelled attempt can be retried unless the runtime was closed. Initialization
currently precedes Turn admission, so stopping it does not emit a fictitious Turn terminal.

Checkpoint rollback preserves the original setup failure or cancellation. If exiting
its context fails, cleanup also attempts to close the owned SQLite connection and
logs secondary errors. The first cleanup error remains reportable by runtime close,
even after a successful initialization retry; other dependencies are still closed.

`TurnStarted` means an owned worker has accepted the Turn, not that its first database
write has committed. That worker owns the initial RUNNING write as well as execution
and terminal persistence. Cancellation joins an in-flight write before recording the
cancelled terminal, so a late write cannot leave the cancelled request resumable.
Initial write failures produce a storage `TurnFailed` without sampling the model.

Explicit interruption appends a model-visible `<turn_aborted>` history item after
tool cleanup and before the cancellation terminal. It warns that commands may have
partially executed. The marker survives restart and reaches subsequent model requests;
if its commit precedes a failed terminal write, recovery finishes cancellation without
resampling. It is not a user request or a changing context section: compaction can
summarize it but does not reinsert it among retained user requests. Raw memory extraction
keeps this interruption fact. Replacing work with `/compact`, ordinary failures, and
within-Turn steering do not create this explicit-interruption marker.

The single-agent marker is enabled by default. Set `[agents] interrupt_message = false`
to disable new markers; previously recorded cancellation intent remains authoritative.
`cancel_active(reason="replaced")` cancels replacement work without claiming a user
interrupt; the default reason is `"interrupted"`.

Configure a model with environment variables:

```bash
export CORKI_API_KEY="..."       # OPENAI_API_KEY is also accepted
export CORKI_MODEL="gpt-5"
export CORKI_API_BASE="https://api.openai.com/v1"
corki
```

Provider adapters support OpenAI-compatible streaming Chat Completions and the
Responses API. Model, loop, context, tool, and runtime settings can also
be placed in `~/.corki/config.toml`; environment variables take precedence for
provider endpoint, model, and secret. Set `api_mode = "responses"` for a
Responses endpoint. Providers such as DeepSeek can enable a
separate reasoning stream with `thinking = true` and an optional
`reasoning_effort = "high"` in the `[provider]` table. Reasoning is rendered
separately from the final answer and replayed when the provider requires it for
tool-call continuation.

Both adapters require a real protocol completion marker and return immediately
when it arrives, validate malformed stream payloads as typed provider errors,
and preserve user/tool image attachments. Context budgeting includes Unicode,
encrypted reasoning, and resized/original image costs.

A completed model response is not necessarily a completed turn: Responses
`end_turn=false` requests another step, even without tools or visible text.
Tool calls also require follow-up, regardless of that flag. Continuation is
checkpointed and bounded by the model-step budget. A valid final completion
may contain no text; a truncated stream or malformed completion remains an error.

Context limits distinguish the configured raw window from its usable budget:
by default requests must stay below 95% of the raw window, and automatic
compaction triggers at 90%. A lower explicit `auto_compact_tokens` is respected;
higher values are capped at 90%. These are approximate input/headroom limits,
not a fixed `max_output_tokens` allocation or automatic model-window detection.

Automatic compaction uses the latest committed provider usage plus locally added
history (user input, context changes, tool observations), not cumulative billing
across steps. Explicit `total_tokens` takes precedence; otherwise nonzero input
plus output is used without counting cached/reasoning subsets again. Missing
usage falls back to local estimation. The usage boundary is committed with model
output, survives reopening, and becomes invalid when compaction replaces that
window. Legacy results without a recoverable boundary use local estimates.
Responses' `x-reasoning-included` header prevents adding old encrypted reasoning
twice; otherwise that old reasoning is estimated separately using Codex's envelope
adjustment. Full-request local hard-limit checks remain as a compatibility safety
guard. A reported full window also fails preparation if no history can be compacted.
Set `auto_compact_token_limit_scope = "body_after_prefix"` to count growth after
the current window's prefix instead of the default `"total"`. The first observed
request's input tokens establish that prefix; its output still counts as growth.
Later input counts do not move the prefix. Compaction resets it. A reopened Runtime
starts with a local estimate of restored history, then replaces that estimate with
the first new server-observed input count. Providers without usage use an estimated
prefix so later growth still counts. In body mode an explicit auto limit is not
capped at 90%, but the full usable context window remains an independent hard cap.
Opt-in TokenBudget can add a fallback buffer to that automatic threshold, never
to the full-window hard cap (see below).

```toml
[agent]
context_window_tokens = 65536
effective_context_window_percent = 95
# auto_compact_tokens = 49152  # optional earlier compaction
# auto_compact_token_limit_scope = "total"  # or "body_after_prefix"
```

Local compaction retains base instructions and appends a user compaction
request. If that request exceeds the usable window (or the provider reports
input overflow), it removes the oldest items from the summarization request,
including matched tool call/results, and logs the omission. Original history
stays on disk; the summary cannot preserve content it never received. The
current user input is kept verbatim outside the summary; if it cannot fit with
the replacement context, preparation fails without installing that replacement.
Output exhaustion is a separate error and does not trigger history trimming.
Local compaction owns an independent bounded retry loop using `stream_max_retries`
and exponential backoff. Each attempt closes its stream; input-overflow trimming
resets that budget. As in Codex's local path, other model errors retry even when
ordinary sampling would not, and do not use the sampling Retry-After delay.
The adapter does not add a second sampling retry loop. Only the last nonempty
assistant summary is installed; failed attempts and cancellation preserve old history.
Main-sampling input overflow ends the Turn; it does not implicitly compact and resample.
Use `/compact` (or consume `runtime.compact()`) for a standalone manual compaction
turn, including on an empty thread. It cancels active work, requests a summary with
no tools, and atomically installs retained user text plus the summary. It does not
append `/compact` as user input or inject fresh world state; the next ordinary turn
rebuilds that context. A committed marker prevents resampling after cold recovery.
Cancellation before commit preserves old history; cancellation after commit cannot
undo an already-installed summary. Consume or close runtime event iterators to release
their buffered observations; an old iterator no longer blocks new task admission once
its owned worker has finished persistence and cleanup. Closing an old iterator cannot
cancel a newer turn. Waiting normal requests remain serialized, and cancelling a
waiting request does not cancel the current worker.
The top-level TOML `compact_prompt` string overrides the local summary request for
both manual and automatic compaction (blank uses the default).
Remote/server compaction is not implemented yet.

Experimental TokenBudget is opt-in with `[features] token_budget = true` (or
`CorkiSettings(token_budget_enabled=True)`). This implements Codex's explicit,
reset path: `/compact`, the context limit, or the ModelOnly `new_context`
tool installs a fresh world-state window **without a summary**, dropping prior
conversation messages from model visibility, including the current turn's old
input on a mid-turn reset. Original records and environment state remain intact.
`get_context_remaining` reports the unbuffered remaining budget; in Code Mode it
returns `{"tokens_left": ...}`. `new_context` is deliberately unavailable to JS.
Window identities survive reopening, while body-prefix estimation follows the
resume behavior described above. Optional settings use a feature table instead
of the boolean:

```toml
[features.token_budget]
enabled = true
reminder_threshold_tokens = 2000
# reminder_message_template = "Only {n_remaining} tokens remain."
guidance_message = "Keep essential progress in workspace notes."
auto_compact_fallback_prompt = "Save important progress before the window resets."
auto_compact_fallback_buffer_tokens = 4000
```

Reminders and fallback prompts are recorded once per window as developer guidance
(system messages for the Chat compatibility adapter), including after a final
answer; they do not force an extra model request. The fallback buffer allows work
to continue after the unbuffered remaining budget reaches zero. A pending reset
or hard limit suppresses the fallback prompt. A buffer without a fallback prompt
has no effect. Text settings have a 2000 UTF-8-byte limit; thresholds and buffers
must be positive integers. Persisted notice identities prevent duplicate prompts
after reopening; ordinary notice-write failures warn without replaying completed
model/tool work, while cancellation still propagates. Automatic account/model
activation and reset hooks remain open alignment work. The optional native
history/notes client is described below; it is separate from local long-term memory.

Context updates preserve the existing model-message prefix. Project rule changes
append AGENTS replacement/removal notices; other full-section contributors use
explicit keyed replacement notices. Comparison state is separate from rendered
messages and resets at the compaction boundary. Role changes first retire the
old-role section. Old database rows remain unchanged, including legacy tombstones;
their request view renders the missing notices. Compaction reinjects current full
values without old update notices. Section-specific Codex diffs and instruction
source refresh/freeze rules are still under audit, not claimed fully equivalent.

Although `provider.api_key` is supported in TOML for local development,
`CORKI_API_KEY` is preferred. If a key is stored in the file, keep
`~/.corki/config.toml` private (mode `0600`) and never commit it.

## On-demand tool discovery

MCP tools now load on demand by default. The model first calls `tool_search`;
the harness ranks deferred metadata with BM25 and returns complete, budgeted
definitions. Ordinary function-calling providers receive only the discovered
schemas in subsequent requests. Built-in tools remain directly available.

MCP initialization instructions are retained as source descriptions and searchable
metadata. The discovery entry lists sources in sorted order, reserving complete
names before sharing a 512 KiB UTF-8 description budget. This is a metadata cap,
not extra model context: the full request still must fit the configured window.
Ordinary MCP tool metadata cannot impersonate hosted connector provenance.

Use `/mcp refresh` to queue an explicit reconnect of configured servers. Preparation
and MCP call admission apply pending refreshes; already-running calls keep their
exact connection and are never replayed. Subsequent steps update discovery sources
and dispatch bindings. Compatible providers remove stale loaded definitions from
the request view; native search keeps historical outputs intact. Neither rewrites storage.
Embedded callers can replace the complete desired server list with
`runtime.request_mcp_refresh(servers)` (an empty tuple removes all servers).
This does not enable background catalog polling or automatic retries of failed calls.

Each prepared model step owns a read-only tool registry snapshot, including handler
identity, schemas and discovery handler. Publishing a new registration affects the
next prepared step, not calls already bound to the current one. MCP calls still
resolve refreshed connections at admission. Code Mode uses the captured step for
new-cell definitions and nested dispatch; its next-step `exec` description is refreshed.
An old cell retains its real tool names and input kinds, but later calls use the
current step's handlers, schemas, concurrency and output budgets. Already admitted
calls retain their worker even while waiting for its execution gate. Exposure controls
new-cell discovery, not revocation of names held by old cells: Hidden/ModelOnly tools
remain dispatchable there (Hidden runs exclusively); remove a registration to revoke it.
Sampling retries retain the same tool snapshot and nested execution gate. A completed
Step stops new worker admission before preparing context; old-cell calls and notifications
queue for the next worker while already admitted calls may finish. Complete-response
providers keep the worker through their separate tool-execution node, and checkpoint
recovery can start that worker directly without resampling the committed model output.
Checkpoints store only a snapshot identifier, never executable handlers. A restarted
process binds current handlers, validates saved definitions and honors the execution
ledger; it cannot restore a previous process's Python object identity.

```toml
[tools]
search_mode = "compatible"  # default; "disabled" restores direct MCP exposure
```

For a Responses provider **and model known to support native tool search and
namespaces**, set `search_mode = "native"`. This is an explicit capability
assertion, not automatic model detection. Native search uses `tool_search_call`
and `tool_search_output`; retrieved definitions stay in history rather than
being injected into top-level `tools`. Both routes are exercised with offline
mock providers, not certified by a live model test.

Definitions survive steps, turns, and durable resume while their search results
remain in the active history. Compaction releases them; the model can search
again. All calls are checked against the current step's dispatch plan. Compatible
loading ignores changes only to concurrency and output budgets, and uses the new
execution settings without searching again; schema/identity changes still unload
definitions. Native output history remains as observed even when tools change or
disappear; it does not override current dispatch eligibility.
Python plugins can opt into discovery with `register_tool(..., exposure="deferred")`.

Raw-input tools can declare `ToolSpec(..., input_kind="freeform", freeform_format=...)`.
The default `[tools] freeform_mode = "compatible"` exposes a JSON function accepting
exactly `{ "input": "raw source" }`; dispatch and durable history retain the raw string.
For a Responses provider/model known to support custom tools, explicitly set
`freeform_mode = "native"` to use custom definitions, calls and outputs. This capability
is independent of `search_mode`; deferred raw tools work with either search route.

Namespaced extension tools use canonical names such as `history::read_item` and
an optional `ToolSpec.namespace_description`. For a capable Responses provider,
`[tools] namespace_mode = "native"` groups definitions by namespace and preserves
the namespace/name split in calls and replay. Namespace order follows first
appearance; functions inside each group are sorted by name. This setting is
independent of search and freeform mode. The default `"compatible"` mode uses
stable, bounded API-safe aliases for Chat and Responses; descriptions retain the
canonical name and namespace meaning. Alias lookup uses the current request's
definitions and loaded discovery history, never a leaf-name fallback. Stored
calls keep canonical identity, so reopening or changing wire mode does not rename
the history. Conflicting descriptions or compatibility aliases fail before
sampling. Code Mode keeps its own JS-safe names and rejects collisions instead
of silently hiding a tool. Native MCP namespace ownership, automatic backend
authentication and the complete Code Mode namespace protocol remain open alignment work.
Native grammar constraints are sent to the provider; compatible transport does not
enforce Lark/regex grammars, so the handler must validate its input. This contract is
also used by the opt-in Code Mode environment below. Verification is offline.

## Code Mode (experimental)

Install the optional engine with `pip install -e ".[code-mode]"`, then configure:

```toml
[tools]
mode = "code_mode"         # default is "direct"; also supports "code_mode_only"
freeform_mode = "compatible"
```

`code_mode` adds `exec` and `wait`; `code_mode_only` routes ordinary tools through
JavaScript while retaining model-only tools. Each `exec` runs a fresh ES module in
a separate QuickJS-NG process, with top-level `await`, `tools`, `ALL_TOOLS`, `text`,
`notify`, `store`/`load`, timers, `yield_control` and `exit`. Nested calls use the
normal schema validation, execution ledger, concurrency and cancellation paths.
Only emitted output reaches the model; nested calls are not fake model messages.
Nested dispatch failures (unknown tool, invalid arguments, handler exceptions,
including `FatalToolError`) reject the JavaScript promise with a string. Catch them
with `try/catch`; an uncaught rejection fails the cell and returns an observation,
not a successful script result. A handler's normal error-valued `ToolResult` still
resolves to its tool-specific value. The ledger preserves this distinction without
replaying failed operations. Direct tool calls retain their fatal Turn boundary;
broken persistence or bridge infrastructure still fails the Turn, even inside a cell.

Cell process cleanup also joins nested callbacks and notifications when reaping or
closing a pipe fails. Observers read the final status after cleanup; infrastructure
cleanup faults cannot appear as a successful script. Cancellation remains cancellation,
and one cell's termination error does not skip the others or realtime deactivation.
Cleanup diagnostics survive the originating Turn: runtime close attempts every remaining
dependency and then raises the first recorded error. This does not replay tool calls or
force normal, yielded cells to end at each successful Turn.

`image`, `audio` and `generatedImage` emit ordered media content. They accept data
URLs or matching MCP blocks (`generatedImage` takes a result object); remote URLs
are rejected. Image helpers accept detail hints/overrides; generated images precede
their optional output hint. PCM WAV clips shorter than 25 ms produce an omission
notice. Text and audio share the output token budget; images survive that budget
but still count toward the full context window. Yield/wait returns new content only.

For a provider/model that supports audio input, enable it explicitly:

```toml
[provider]
supports_audio_input = true
```

It defaults to false and produces an explicit omission notice instead of sending
unsupported audio. Responses retains ordered native
content; Chat uses a call-labelled user content block after the tool message and
supports base64 WAV/MP3 audio. Unsupported-model filtering only changes the request
view, not the durable media history.

Tools may also return typed `EncryptedContent` blocks. These are opaque output,
not plaintext or encrypted reasoning: Corki preserves them through the tool
ledger, history and checkpoints without decoding or slicing them. A Responses
provider that explicitly supports this protocol can enable
`[provider] supports_encrypted_tool_output = true` (default false). Other
Responses providers and Chat receive an explicit unavailable-content notice;
this is not decryption or equivalent history recovery. Switching the request
capability does not erase the stored payload. The ordinary text-output budget
does not truncate ciphertext; aggregate binary/opaque tool content retains the
32 MB safety cap and the full request still counts its estimated token cost.
JavaScript's default tool-result projection excludes encrypted blocks.
The opaque-output flag alone does not enable the history/notes service.

For an already authenticated Codex-backend endpoint, the optional native client
requires all of: `[features.token_budget] enabled = true` and
`use_history_notes_extension = true`, plus `[provider] name = "openai"`,
`api_mode = "responses"`, `codex_backend = true`, a backend `base_url` and its
configured bearer credential. `codex_backend` is an explicit credential declaration,
not an OAuth login flow or a claim that an ordinary OpenAI API key is eligible.
Corki does not read Codex's login files or refresh these credentials automatically.
Configuration changes currently require reopening the Runtime.

This publishes four `history::` and five `notes::` model-only tools through the
normal registry/executor/ledger, also in Code Mode sessions (not inside JS).
It enables native encrypted output for the default Responses adapter, sends trusted
session/turn/window ingestion metadata and a bounded 4000-byte thread hint per
window. Hints that fail or exceed the limit are omitted; cancellation propagates.
Calls use the same backend base URL and credential as inference, a 35-second
overall timeout and no automatic operation retry. The output header requests a
byte limit using Corki's configured tool-output budget; opaque results are not sliced.
Unknown write outcomes are never automatically replayed on recovery.

The current single-root mapping uses the Corki thread ID as session/thread identity
and persisted context-window IDs. Installation identity, source ordinals, multi-agent
identity and live auth/config invalidation remain incomplete. Absent image detail
uses Corki's existing `high` default. Native and flat namespace transports, failure
observations, resets and cold recovery are verified offline; actual backend ingestion,
server-side note limits/consistency and model decryption have not been verified.
When TokenBudget and `use_history_notes_extension` are enabled but native eligibility
is absent, the same nine tools use local compatibility recovery instead. This works
with Chat and Responses, including Code Mode's model-only tool surface. Native HTTP
failures do **not** switch backends or replay writes against local storage. Backend
choice is fixed for the Runtime; switching providers does not migrate private notes
between native and local stores.

Local history is read from this thread's append-only archive, not its compressed
active window. List/search results include stable `item_id` and `window_id` values;
pass them unchanged to `history.read_item`. Reads expose character offsets and a
`next_offset_chars` continuation when truncated. Tool call IDs, error status, role
and search-hit character offsets are retained, with previews around the matching text.
Tool inputs preserve stored raw JSON/freeform text (including malformed JSON), input
kind and parsing diagnostics. Older calls without raw input use their parsed arguments.
Namespace filters apply to tool messages; stored inline images can be read back separately
from text. Private reasoning is not exposed, and native opaque content cannot be
decrypted locally. This is not a byte-identical projection of the backend's normalized
history or its undocumented result schemas.

Local notes use a sibling `<database-stem>.history-notes.db` SQLite file, created
with mode 0600, and are keyed by thread ID plus virtual `/root/notes/...` path.
Model-provided context and path arguments cannot select another thread or a host
file. Relative paths, literal `~`, line ranges (including negative line numbers),
case-sensitive search, ordering and a 1,000,000 UTF-8-byte file limit are supported.
Paths are limited to 4096 UTF-8 bytes; only the current `/root` agent is available.
Append is transactional, and completed writes are immediately visible to reads,
lists and searches. Cancellation joins an already-started SQLite operation before
ending the turn; completed ledger results are reused after restart.

Local schemas omit native encrypted-argument markers, results are readable plaintext,
and no history-ingestion metadata or backend HTTP request is sent. Read/list/search
JSON is bounded without splitting identifiers; large single lines may still require
a larger configured output budget. Local note storage is not encrypted and is not an
OS access-control boundary or a separate cross-thread long-term-memory store.

Audio preparation accepts base64 data URLs for WAV, MP3, M4A, WebM and Ogg,
canonicalizes MIME aliases, and checks a 50 MiB decoded limit (smaller tool/bridge
limits still apply). Bad data URLs, unsupported formats and oversized audio become
distinct text omissions without changing a tool's success status. Preparation
does not require codec decodability or transcode audio; duration estimation is a
separate step. Existing audio history is normalized in a read-only projection.

Images are decoded before entering model history, using PNG/JPEG/GIF/WebP bytes
rather than trusting filename extensions or MIME labels. Invalid images, remote
URLs and `low` detail become explicit text omissions in their original positions.
High/auto detail uses a 2048-pixel edge and 2500-patch budget; supported original
detail uses a 6000-pixel edge and 10000 patches (32 pixels per patch). GIF becomes
PNG; small PNG/JPEG/WebP keeps its original bytes. CPU work is bounded and joined
on cancellation. Existing database history is prepared in a read-only projection.

```toml
[provider]
supports_image_input = true              # default
supports_image_detail_original = false   # enable only for a capable model
unified_image_budget = false             # experimental; also requires original support
```

`view_image` validates local image bytes before returning anything to JavaScript.
Its typed result contains the unresized `image_url` and effective `detail` (detail
is omitted under unified budgeting); `image(await tools.view_image(...))` goes
through the same preparation as direct output. The tool ledger retains that
typed original; newly persisted model content contains the prepared image.

Nested shell calls return objects with `output`, per-call `wall_time_seconds`, and optional
`chunk_id`, `original_token_count`, `exit_code`/`session_id`; the latter is Corki's string process
ID and can be passed to `write_stdin`. Shell collection keeps a bounded head/tail (default 1 MiB),
with explicit omitted-byte metadata. Both shell tools accept a per-call `max_output_tokens`,
including zero; it does not shrink the process buffer or carry into later polls. Nested output
uses this explicit budget only; without it the complete collected output is returned. Direct model
output uses the smaller of the model policy and the requested/default 10,000-token budget, reserving
room for headers and truncation notices. Log previews use a separate display cap. The durable shell
result contains the source-formatted model text and nested value, not an unlimited raw byte archive.
MCP calls retain `content`, `structuredContent` and `isError`
when present, excluding private top-level `_meta`. Block metadata is preserved.
Before both nested and direct MCP projections, image/audio blocks unsupported by the model's
declared input modalities become explicit text notices. Nested results otherwise keep the complete
public result, independent of the direct model-output budget.
Extensions can return `ToolResult(..., code_mode_output=CodeModeOutput(value))` for
an explicit JSON result, including `null`; otherwise the usual text is returned,
without automatic JSON parsing. These values are validated and retained in the
execution ledger, not duplicated into model history. Structured values are limited
to 32 MB UTF-8 JSON, 64 nesting levels and one million nodes; the smaller 4 MB
cell bridge limit still applies and rejects oversized responses without replay.

Yielded cells remain available to `wait`, including across turns in the same runtime.
`store`/`load` are session-local working state, not durable long-term memory. After a
process restart, old cell IDs report missing and scripts are not automatically replayed.
If the optional engine is missing, `code_mode` falls back to direct tools unless
`code_mode_disable_fallback = true`; `code_mode_only` fails closed.

The JS context exposes no filesystem/network APIs or module loader. This is **not
an OS sandbox**: tools retain their existing permissions, and engine vulnerabilities
are not an isolation guarantee. Current limits include a 64 MiB JS heap per cell,
32 retained cells, bounded bridge/output/store data and nested-call budgets.
Codex parity remains partial: V8/host selection, remaining tool-specific return
contracts, resize notices and media provenance, compressed audio duration probing,
and additional lifecycle/accounting edge cases are still open.
Pillow and Rust encoders are not byte-identical after resizing; Corki additionally
limits decoded images to 64 million pixels. Audio helpers validate URL shape;
the Runtime additionally validates and canonicalizes base64 and MIME. Audio
duration estimates currently decode PCM/float WAV and otherwise use URL size.
Tests run real JS with scripted/mock models; live-model selection is not certified.

Scoring follows the pinned `bm25 2.3.2` defaults (`k1=1.2`, `b=0.75`), retains
repeated query terms, and uses an inverted index with cached document weights.
Index generations are published only after a successful build; input or returned
schema mutations cannot corrupt the cached snapshot. Equal-score ties use stable
registration order (Codex does not promise a fixed tie order).

Runtime prepares the discovery index before publishing the search handler. Each
handler keeps its own frozen corpus instead of reading the latest registry while
executing. Immutable MCP instances use weak-identity cache keys; dynamic tools use
search text, loadable definitions and source metadata. Execution-only settings can
rebind returned definitions without rebuilding the index. Initialization/build
failures stop preparation and remain retryable; query errors are tool observations.
Preparing an index after durable resume does not replay the recorded search call.

The tokenizer uses the pinned transliteration, NLTK stopwords, English stemming
and term-hash pipeline. Full ranking parity, experimental source-listing policy
and the remaining harness work are tracked in
[the source-alignment audit](harness-alignment/audit.md).

Recoverable tool exceptions and malformed results become bounded error observations, including
validation of text, error flags, attachment records and plan updates before persistence. Tool
extensions can raise `corki.tools.FatalToolError` when their execution environment cannot safely
continue; Runtime then fails the turn and joins parallel siblings. Cancellation remains control
flow. A per-server MCP timeout reports that the execution outcome may be unknown and does not
automatically retry. This is not a universal timeout or forced isolation of arbitrary Python tools.
MCP protocol, transport and timeout failures become MCP `isError: true` values, including in nested
Code Mode; malformed handler contracts remain dispatch failures. Cancellation is never converted.

Tool recovery also checks the invocation's argument fingerprint, not just its call ID and name.
Equivalent parsed JSON reuses the result; different arguments cannot reuse it. Completed results
are immutable (identical re-commit is allowed). Legacy ledger rows without argument evidence are
preserved but return an explicit unverified-result error instead of replaying or guessing.

Responses `output_item.done` function/search calls now execute during the model stream, after an
atomic partial-step/history append. Parallel groups overlap; exclusive tools form ordered barriers.
Completed results are reused by the subsequent graph node. After a crash, a partial step resumes
from its recorded calls, not by resampling the old request; unknown side effects are not replayed.
Model stream errors drain already-started tools; cancellation closes and joins them. A truncated
response is a failed attempt, never a successful completion. Retryable failures now rebuild the
next request from durable completed items and tool observations; they do not replay the stale
request or an unknown tool side effect. Exhausted or non-retryable failures terminate the turn.
Chat providers without per-item completion retain the completed-
response path. Completed messages and reasoning now share the ordered partial history with tools;
each reasoning item retains its own opaque state. Citation filtering is per message, and assistant
phases survive Responses replay. The final answer is the last nonempty message, not accumulated
commentary. Retry attempts have durable failure records and separate physical sampling indices;
they do not consume the logical step budget. Recovery retains both retry counts and observations.
These changes do not establish complete Harness parity; see the open audit items.

HTTP requests retry 5xx and pre-response transport failures up to four times by default. Failed
sampling attempts have a separate five-retry budget and rebuild history. Both use 200ms exponential
backoff with ±10% jitter, not HTTP Retry-After. A persistent generic 503 can therefore produce 30
HTTP requests across six sampling attempts; an HTTP 429 is not automatically retried.

After request retries are exhausted, main sampling retries connection-establishment failures
indefinitely by default, with delays of 5, 10, 20, 40, then 60 seconds. All waits can be cancelled
or interrupted by steering. To disable ongoing network reconnection while retaining bounded retries:

```toml
[provider]
unbounded_connection_retries = false
stream_max_retries = 5
request_max_retries = 4
```

`max_retries` remains a legacy alias for `stream_max_retries`; the explicit new key takes precedence.
Each count is capped at 100. Set both counts to zero and disable unbounded connection retries to
disable all retries. Setting just the stream count to zero does not disable the HTTP request tier.
Standalone adapter requests (including memory), and providers explicitly named
`bedrock`/`amazon_bedrock`, retain bounded retries. Corki currently uses HTTP transport only;
this does not implement WebSocket-to-HTTPS fallback or native Bedrock transport support.

Responses SSE failures now distinguish quota, policy rejection, invalid requests, overload and
rate limits by error code. During ordinary sampling, non-retryable rejections stop the turn; rate-limit messages can supply
a seconds/milliseconds delay. Error events are retained until stream EOF, while later complete
items are preserved; only a valid completion ends the response successfully. HTTP errors are mapped
after the request tier is exhausted. HTTP 400 is an invalid request, not evidence of input overflow
based on message text. HTTP 401/403 with a static key follows bounded unexpected-status retries;
no OAuth refresh or automatic account switching is added. Missing local credentials fail immediately.

## Local data

Corki stores its configuration and runtime data under `~/.corki`:

```text
~/.corki/
├── config.toml
├── history/
│   └── input-history
├── logs/
├── memories/                   # opt-in cross-thread long-term memory artifacts
│   ├── MEMORY.md
│   ├── memory_summary.md
│   ├── rollout_summaries/
│   └── extensions/ad_hoc/notes/
├── plugins/                    # local Codex-compatible or Corki plugins
├── skills/
│   └── .system/                # fingerprinted bundled skills
└── sessions/
    ├── corki.db
    └── checkpoints.db
```

For tests and isolated installations, set `CORKI_HOME` to override that path.

With long-term memory and its dedicated tools enabled, `memory_add_note` records an
explicit remember/update/forget request as an append-only Markdown file. Filenames use
`YYYY-MM-DDTHH-MM-SS-<slug>.md` with ASCII digits and a lowercase ASCII slug. Writes preserve
the exact UTF-8 note, reject an existing filename, create files with mode 0600, and recheck
the managed directory chain before writing. These checks reject existing symlink redirects;
they are not an OS sandbox or a guarantee against concurrent hostile directory replacement.

The request is considered by a subsequent eligible background consolidation, subject to
generation settings and cooldown. Recording it is not a synchronous memory edit or secure
erasure. Consolidation receives notes as data, is instructed not to execute embedded commands,
and marks derived information with `[ad-hoc note]`. Original notes and conversation archives
remain; a forget request does not remove every copy from disk or backups. Offline tests verify
the data flow and instructions, not the real model's forgetting or prompt-injection resistance.

Memory reads preserve UTF-8 text and original line endings, using LF for 1-based line ranges;
the empty line following a final LF is readable. Search line numbers use LF/CRLF boundaries,
not arbitrary Unicode separators. Normalized search removes non-alphanumeric characters,
with optional lowercase comparison (not case folding); a query that normalizes to empty is
rejected. Full equivalence of Python and Rust Unicode property/version tables is not yet proven.
Summary and read truncation preserve the beginning and end on UTF-8 boundaries using the
source's approximate four-bytes-per-token policy; the omission marker is extra. This is separate
from the complete model-request budget. Default root search rejects a symlinked root, and scoped
paths reject dangling links. Summary injection still reads a trusted local file as in the source;
these checks do not establish a general filesystem sandbox or prevent concurrent rename races.

## Architecture

The CLI depends on a streamed runtime-event protocol rather than on LangGraph,
model SDK types, or tool handlers directly.

```text
CLI -> LangGraphRuntime -> explicit StateGraph
                         ├── ContextBuilder -> prompts + AGENTS.md + Git
                         ├── ModelPort -> Chat/Responses adapter + capabilities
                         ├── ToolRegistry/Executor
                         │   ├── exec_command / write_stdin
                         │   ├── apply_patch
                         │   ├── update_plan
                         │   └── view_image
                         ├── Skills -> project/user/system/plugin discovery
                         ├── MCP -> stdio/HTTP tools + resources + prompts
                         ├── Plugins -> skills + MCP + namespaced Python tools
                         ├── RealtimeController -> live text steering
                         ├── ContextWindowManager -> history + compaction
                         ├── ConversationMemory -> same-thread continuous history
                         ├── LongTermMemoryService -> extract/consolidate/retrieve
                         └── evaluation -> step/tool bounds + validation
```

Codex-style cross-thread long-term memory is opt-in. Enable it in
`~/.corki/config.toml`:

```toml
[memories]
enabled = true
generate = true             # background extraction and consolidation
use = true                  # inject the compact routing index
dedicated_tools = false     # optional memory_list/read/search/add_note tools
disable_on_external_context = false
```

At startup Corki leases a bounded number of eligible, idle threads, extracts durable facts with a
separate model request, and performs singleton global consolidation into `~/.corki/memories`.
Normal turns receive only `memory_summary.md`; the model progressively searches `MEMORY.md` and
rollout summaries when relevant. This deliberately follows Codex's layered Markdown retrieval
instead of introducing an embedding/vector store. Memory generation failures are isolated from the
interactive turn, and cited source thread IDs update usage ranking.

Memory extraction now defaults to `gpt-5.6-luna` with low reasoning effort; consolidation defaults
to `gpt-5.6-terra` with medium effort, following the pinned Codex reference. These jobs no longer
implicitly reuse the main model. Selection order is `[memories].extraction_model` /
`consolidation_model`, then `[provider].memory_extraction_model` / `memory_consolidation_model`,
then those defaults. Third-party providers can set their own model IDs at either level. To keep
using the main model, set both memory model fields explicitly to that ID. An unavailable memory
model fails the background job; it does not silently fall back to the main model.

Per-request memory reasoning settings do not mutate a shared adapter or subsequent main turns.
They are sent only when the provider capability supports reasoning effort.

Extraction reads a structured, provider-neutral archive, preserving call/result IDs, errors,
discovered definitions and ordered media. It excludes context snapshots, reasoning, compaction
markers and explicitly marked retained user copies; complete AGENTS/skill injection fragments
are filtered per user text part. New user input remains eligible even when first stored in a
compaction replacement bundle. Older unmarked copies are not guessed away. Secret redaction is
best-effort, applied before JSON encoding. Its patterns, ordering and replacement markers follow
Codex for Bearer tokens, OpenAI keys, AWS access IDs and common secret assignments; this does not
guarantee detection of every credential.

Extraction uses 70% of the selected model's effective window. Configure static model metadata
when an independent extraction model has a different window:

```toml
[models]
# context_window_override = 32000  # explicit global override, clamped to each model's maximum
[models.catalog."my-extraction-model"]
context_window = 16000
max_context_window = 32000
effective_context_window_percent = 95
[memories]
extraction_model = "my-extraction-model"
# extraction_token_limit = 8000  # optional extra cap; omission selects the automatic budget
```

Catalog lookup first uses the longest prefix of the full requested name. If none matches, it
retries after stripping one namespace containing only ASCII letters/digits, `_` or `-`; multi-level
namespace stripping is not allowed. A full-name match always takes priority, and the provider
request keeps the original model name. A main model not in the catalog uses the existing `[agent]`
window settings. When no custom catalog is configured, Corki ships the pinned Codex catalog's
window-only projection; the main agent keeps its existing window settings, capped by the bundled
maximum. An explicit custom catalog, including an empty one, replaces rather than merges with
the bundled entries. Another missing model uses Codex's generic 272,000-token window metadata and
emits a warning: this is a fallback assumption, not verified provider capacity. A catalog entry
with neither window falls back to 150,000 rollout tokens (or an explicit `extraction_token_limit`).
Known larger windows are not capped at 150,000 by default. The resolved
main window also drives automatic compaction and skill-catalog sizing. This is the static catalog
path, not automatic remote/provider catalog discovery. Rollout truncation preserves UTF-8 head
and tail at approximately four bytes per token, with an additional omission marker.

The extraction request uses a strict three-field output schema, but response parsing accepts an
omitted optional `rollout_slug`, matching Codex's deserializer. Unknown/duplicate fields and invalid
types fail extraction. Raw memory, rollout summary and slug retain their whitespace; only an actual
empty raw memory or summary produces the no-output transition, not a whitespace-only string.

Extraction excludes the current thread and disabled/polluted sources; a successful turn is not
required. Age/idle filtering and recency ordering preserve millisecond resolution. Each pass scans
at most 5,000 eligible threads before checking prior outputs/jobs. The configured startup limit also
caps valid running extraction leases across processes sharing the database. Three recorded failures
exhaust a source's extraction budget; only a newer source version resets it and bypasses backoff.
Successful source watermarks survive later failed attempts and restarts, including no-output results.
An empty raw memory or rollout summary invalidates that thread's prior extraction and schedules
consolidation when old inputs were removed; previous published detail changes only after consolidation.
Legacy jobs retain owners and backoff during migration; previously unrecorded retry counts start at three.

Each background generation pass first prunes at most 200 stale, unselected extraction records.
Recency means last use, or source update time when never used—not extraction generation time.
Previous successful consolidation selections and extraction job watermarks survive pruning; refreshing
an extraction preserves its old selection baseline until consolidation succeeds. Pruning failures warn
and allow generation to continue; shutdown joins any already-started database prune before returning.
Marking a thread polluted also enqueues consolidation if that source participated in the last
successful baseline, including repeated pollution marks until a new baseline clears its selection.
The mode and enqueue commit together; an active consolidation owner and the normal success cooldown
are preserved. Cancellation waits for a started write to finish. This schedules later reconciliation,
not immediate artifact deletion. Trusted handlers can set `ToolResult.contains_external_context`;
the boolean survives normalization, media processing, the result ledger and conversation history.
The Runtime consumes it for direct and nested Code Mode calls, including cached results on recovery.
Tool search sets this flag even with no matches. When marking fails, the turn continues with a log
warning; cancellation still propagates. This best-effort path cannot guarantee exclusion while
the memory database is unavailable. MCP marks at actual call admission, after argument/exposure and
current server/tool checks, under the selected connection's lease and before the remote request.
Names resembling MCP tools do not trigger this policy. Host code can supply
`mcp_server_metadata={"docs": MCPServerMetadata(pollutes_memory=False)}` to Runtime creation, or
`server_metadata=...` to `request_mcp_refresh`; import `MCPServerMetadata` from `corki.mcp`.
Unspecified servers default to true. These values come from trusted host code, never remote hints,
tool metadata, model arguments or plugin manifests. Pending calls use the refreshed connection's
policy; admitted calls retain their connection and policy through preparation/execution.
This connection lease does not implement Codex's catalog read/write lock or complete approval
authority. Completed native search calls retain an external-context fact even when local search
execution is rejected. Completed hosted web/search events and call-id-less notifications are stored
as `HostedToolItem`, without creating a local tool execution or a synthetic result pair. They survive
partial-response failures and cold recovery. Responses replays their native payload; Chat uses a
labeled user-role external-data message. Search-specific payloads also use that compatibility message
when native tool search is disabled. Raw history, compaction inputs and history search/read retain the
events. Startup rechecks saved call-id-less notifications, matching the corresponding Codex path.
Hosted function outputs now use model-specific byte/token truncation on a model-visible copy,
leaving the raw event available to the archive and history retrieval. The default history allowance
is 1.2 times the model policy, rounded up; trusted per-item `fallback_token_limit_override` values
instead use exactly that token budget. Remote payload metadata cannot set this override. Text keeps
UTF-8-safe head/tail evidence and an extra marker; ordered outputs budget text and audio sequentially,
retain images/ciphertext, and append omission counts. Search/Web event payloads do not use this
function-output truncation branch. Preparation, compaction, retry and request-budget accounting share
the same visible projection. Old records remain readable without new fields.

Static model catalog entries accept `truncation_policy = { mode = "tokens", limit = 10000 }` (or
`mode = "bytes"`). Bundled defaults follow the pinned Codex catalog; unknown models default to
10,000 bytes. `tools.output_token_limit` overrides this policy in token units for both hosted and
ordinary function-output history. Ordinary tool results and error diagnostics remain intact in the
execution ledger and original conversation archive; model copies are budgeted before context
accounting, compaction and recovery. Nested Code Mode calls receive the original result, independently
of their outer cell's output budget. Trusted handlers may set `ToolResult.fallback_token_limit_override`
(including zero); new search-output classification comes from the registered handler type, not its
name or returned text. Legacy search rows retain an explicit compatibility fallback.

`tools.output_char_budget` now limits UI previews rather than replacing the model policy. An explicit
`ToolSpec.output_char_budget` remains an additional compatibility-only cap on the direct model copy;
it cannot increase the model budget and never truncates the ledger or nested result. Ordered content
using that legacy setting retains its older token approximation. Prefer the model policy and trusted
token override for new integrations. Ordinary result bodies have a separate 32 MB raw transport guard;
oversized results become error observations, and oversized diagnostics are bounded with an omission
marker. MCP now follows its source-specific conversion: non-null `structuredContent` takes precedence
unless content includes a typed encrypted block. Otherwise ordered text/image/audio/opaque blocks
are preserved, with unknown or malformed blocks represented as JSON text. MCP text metadata
`codex/encryptedContent: true` creates opaque output rather than plaintext; `codex/imageDetail`
selects the requested image detail, followed by normal model-capability and image-preparation checks.
For example, default detail-based preparation still rejects `low`; conversion alone does not imply
that every media representation is supported. Logs use text-only projection; recognized media and
encrypted blocks are not serialized into log text. Unknown blocks intentionally become JSON text,
so this is not a general-purpose secret filter for arbitrary block contents.

Direct MCP outputs include `Wall time: … seconds` and `Output:` and use the effective policy's 1.2
serialization allowance. The corresponding exact token fallback travels with the durable result;
the nested public result and independent log preview are not first truncated to this model budget.
Configure a positive per-tool override in `[mcp.servers.docs.tools.read]` with `output_token_limit = 80`.
It may override the model default in either direction. Execution admission captures this setting from
the exact selected connection, including after refresh; in-flight calls retain their captured policy.
Remote tool/result metadata cannot set the budget. Old fixed MCP character limits are no longer used.
Malformed MCP result envelopes (`content` not an array or non-boolean `isError`) and invalid HTTP
JSON become MCP error values, including inside Code Mode, without automatically replaying the call.
Correlated response IDs must be integers matching the request; booleans/floats cannot alias them.
Missing `content` and null optional `isError` retain compatibility behavior; this is not a claim of
complete rmcp wire-schema, session-expiry recovery or streaming timeout parity.
Other shell lifecycle and outer Code Mode formatting, full MCP approval/catalog authority, complete media conversion and the
distinction between original handler bytes and prepared media still need further source alignment.
Raw hosted envelopes also use a 32 MB transport/storage guard, and
incoming events still count toward the adapter's full response limit; neither is a content truncation
policy. Full hosted notification producers, media capability conversion/non-WAV audio duration and
hosted discovered-tool registration remain incomplete. No metadata is inferred from untrusted text.

Consolidation excludes disabled/polluted sources and blank outputs before choosing its Top-N, ranked
by usage count, use/source recency, source update and thread ID. Selected inputs return in stable
thread-ID order. Usage persistence counts each supplied signal, including repeated IDs, with one
timestamp per batch. Pruning does not delete conversation history or directly rewrite published memory.

Phase-two startup claims are independent of DB input/completion watermarks. After acquiring the
global lease, Corki synchronizes inputs and checks workspace fingerprints and artifact validity;
file-only edits, deleted last inputs, and a new empty database can therefore require consolidation.
Successful consolidation (including an unchanged-workspace pass) starts a six-hour cooldown,
matching the pinned Codex default. New inputs do not bypass that cooldown. A failed pass instead
observes its retry delay; repeating startup no longer re-enqueues unchanged input to bypass it.
Actual extraction changes or an explicit enqueue advance the input watermark monotonically and
reset non-running retry backoff, while preserving an existing successful cooldown. Phase-two
failures are not permanently disabled by the stage-one three-failure limit. Legacy databases with
no recorded finish time get a normal workspace check, not an invented cooldown. With generation
enabled, the first empty memory store may now make one background initialization request.

Consolidation renews its owner-token lease while working. Workspace writes and final publication
are fenced by a SQLite write transaction, so an expired/replaced owner cannot overwrite a newer
owner's files. Cancellation joins already-started filesystem writes before releasing ownership;
selected inputs are marked only when their exact source version still matches. Background model
streams stop and close at completion, and failure-recording errors do not mask cancellation or
abandon sibling extraction jobs.

The version-2 consolidation baseline records both the sampled inputs and the published outputs.
Edits to detail, summary, or generated skills invalidate the skip decision; existing skill text is
included in the next consolidation request. Skipping also requires valid artifacts (`v1` summary,
regular files, no visible workspace symlinks). Legacy baselines trigger one normal rebuild because
they cannot prove the outputs are unchanged. Notes added during sampling remain pending for the
next pass. Baselines store fingerprints, not old memory text.

Publication is still atomic per file, not across all files and SQLite: model failures before
publication preserve the previous files, but I/O failure or a crash during publication can leave
mixed generations. The inspected Codex local path likewise uses a live workspace; cross-store
atomic publication is not an established Codex contract. These limits and remaining parity work are tracked in
[the source audit](harness-alignment/audit.md); the complete Codex memory contract is not yet verified.

See [development.md](development.md) for the full architecture, dependency
rules, roadmap, and development guide. For a source-led course on building the
harness itself, read [teach.md](teach.md).

## Verify

```bash
ruff check src tests examples main.py
ruff format --check src tests examples main.py
pytest
python -m compileall -q src
python examples/core_loop_demo.py
python examples/builtin_tools_demo.py
python examples/extensions_demo.py
```

The three deterministic demos need no API key. They verify the coding loop and cross-turn memory,
every default built-in tool, and project skill/plugin/stdio-MCP recall respectively. See
[examples/README.md](examples/README.md).

The bundled catalog contains 10 skills: six derived from Codex (`imagegen`, `openai-docs`,
`plugin-creator`, `review-agent`, `skill-creator`, `skill-installer`) and four complementary general
skills selected from Hermes (`arxiv`, `grounded-citations`, `pdf`, `xlsx`). Together they add
newest-first paper retrieval, evidence-backed web research, PDF workflows, and spreadsheet/data
work without duplicating the coding baseline. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Custom skills use the same `SKILL.md` package shape. Corki loads project skills from
`.corki/skills`, and also accepts Codex-compatible `.codex/skills` and portable
`.agents/skills` directories from the current directory up to the project root. User skills may
live in `~/.corki/skills`, `~/.agents/skills`, or `~/.codex/skills`; project scope wins on name
collisions. Directory symlinks are supported for project and user skills.

The model-visible skill catalog has a metadata budget: by default 2% of the configured
model window, or 8,000 characters when no window is known. `[skills] max_context_tokens`
overrides it (positive integer, capped at 10,000). Descriptions share the budget while
complete names and paths are preserved first; root aliases are used when they improve
coverage or size, including their table cost. Omission from the prompt does not disable
`skill_list`, explicit selection, or `skill_read`. Budget warnings are transient and emitted
only when a catalog is rendered, deduplicated within the turn.

Skills may set `policy.allow_implicit_invocation: false` in `agents/openai.yaml`.
This hides them from automatic catalogs and model-facing `skill_list`, but explicit
mentions and known-name `skill_read` remain available. Optional metadata errors are
logged and fall back to the default visible policy without dropping a valid `SKILL.md`.
To disable a skill, use ordered `[[skills.config]]` entries with `enabled = false` and
either `name = "skill-name"` or `path = "/absolute/path/SKILL.md"`; later matching rules
win. Disabled skills remain in the internal discovery snapshot but cannot be selected
or read through the skill tool. These controls are not filesystem permissions and do
not erase bodies already present in conversation history.

`[skills] include_instructions = false` hides the entire automatic catalog while
keeping explicit skill input, `skill_read`, and requested `skill_list` available.
This differs from `enabled = false`, which disables the skill service and its tools.
The first hidden/empty catalog is silent; changes from an existing catalog append
a distinct hidden or unavailable notice. Comparison-only records survive recovery
but are not model messages. Compaction rebuilds the current state without replaying
old notices. Apply configuration changes by reopening the runtime; a live config
event bus is not yet implemented.

Skill headers allow an omitted/blank name (defaulting to the parent directory),
retain full descriptions, and repair specific unquoted prose fields. Header parsing
does not apply the catalog's description budget. Typed YAML fields preserve scalar
text; `no`/`off` are not aliases for a false invocation policy.

Explicit text selection accepts exact, unambiguous `$name` and `[$name](path)`
mentions, including `skill://` paths and discovery symlinks. Paths are selected first,
then names, each in discovery order. Common environment variables are ignored;
a linked label does not fall back to name selection if its path fails to match.
Structured UI skill inputs and hosted-connector name collision checks remain open.

An explicit skill at turn start is read once and appended after that user input.
Its body is input-attached history, separate from the changing skill catalog:
later steps do not reread or revoke it, and another explicit turn can load it again.
Preparation/recovery preserves an already-recorded body even if its file changes.
Mid-turn compaction does not automatically reinject old skill bodies; the model can
use `skill_read` again. Mentions added through steering do not rerun turn-start
injection either. They remain available for model-directed discovery and reading.
# corki
