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

Default CLI and SDK execution now requires the pinned native permission backend.
For a source checkout, build it with Rust 1.95.0 and install the verified artifact:

```bash
python native/sandbox/build.py /path/to/codex /absolute/new-output/corki-sandbox
python native/sandbox/install.py /absolute/new-output/corki-sandbox
```

The installer creates a Git-ignored, host-platform bundle in this checkout and
refuses to overwrite an existing one. A local native platform wheel includes the
backend already; a generic wheel requires an explicit host compiler configuration.
See [native backend setup and limitations](native/sandbox/README.md). Missing or
invalid default assets are startup errors, not permission to execute unrestricted.

Start the interactive CLI from any working directory:

```bash
corki
corki resume                 # latest thread for this directory
corki resume THREAD_ID       # explicit persisted thread
```

Implicit CLI resume restores the thread's last persisted model, provider identity
and selected reasoning effort before constructing clients or context budgets.
Changing global TOML defaults does not silently change that group. A stored unset
effort clears the new configuration's effort; model metadata can still supply its
default. Old databases without this record use current configuration.

Use `--model`, `--provider` or `--reasoning-effort` to explicitly override resume.
Specifying any one skips the entire persisted group; unspecified fields then use
current configuration, not the old thread. Nonempty `CORKI_MODEL` or `CORKI_API_BASE`
also count as explicit host overrides. API-key rotation alone does not. CLI flags
take precedence over the corresponding model environment value.

Provider identities resolve against the current inline `[provider]` table or a
named `[providers.ID]` profile. An unnamed inline provider has identity `default`.
Keep old profiles configured if those threads should remain resumable. A missing
profile fails before creating model clients rather than sending history to the
current default provider. Credentials and endpoints are never stored in thread
model metadata. To separate profile identity from the provider's descriptive name:

```toml
[provider]
id = "private-deployment"

[providers.private-deployment]
name = "deepseek"
base_url = "https://your-provider.example/v1"
api_mode = "chat_completions"
# Supply credentials through configuration or CORKI_API_KEY.
```

Inline fields override a same-identity profile only. Other profiles do not inherit
the inline provider's credentials. Resume does not restore reasoning summary,
service tier or an entire configuration snapshot. `Runtime.create(settings=...)`
accepts an already-resolved explicit configuration; it does not implicitly replace
that object from storage.

In an async host, construct the runtime with
`runtime = await LangGraphRuntime.acreate(settings=..., database_path=...)`.
This waits for cleanup of newly owned resources if construction fails or is cancelled.
The synchronous `LangGraphRuntime.create(...)` entry remains available outside a
running event loop. Inside a running loop it rejects unowned construction before
allocating resources; do not move it to another loop or bypass ownership to avoid
the error. Successfully constructed runtimes still require `await runtime.aclose()`.

The host API `await runtime.update_thread_settings(model="...", reasoning_effort="high")`
commits defaults for future Turns without changing an active Turn or sampling a model.
`runtime.thread_settings` returns the immutable committed selection. Omitted fields
are retained; explicit `reasoning_effort=None` clears effort selection, and
`service_tier=None` requests the default tier. `reasoning_summary=None` leaves the
summary unchanged. Updates serialize with Turn admission and retain ownership through
commit/publication even if the caller is cancelled. This API does not change provider,
permissions, environments, or a running Turn's/Step's settings.

With `[features] step_model_switching = true` (default false), the separate
`await runtime.update_turn_settings(turn_id, model="...", reasoning_effort="high")`
publishes settings for later Steps of that exact live task. It returns a result with
`status` equal to `applied`, `target_unavailable`, or `rejected` (with `reason`).
`runtime.active_turn_settings` shows the current publication, not necessarily the
snapshot used by the in-flight request. Sparse field semantics match the thread API.
Updates serialize through metadata resolution, do not change future thread defaults,
and do not force another model sample. Already captured requests/retries/tool calls
keep their Step; tool-output policy and remaining-budget limits retain the admitted
Turn owner. Captures are checkpointed before asynchronous preparation, and cold
recovery restores the captured Step without re-resolving its metadata. Unconsumed
publications are process-local, not promised durable across a crash.
Compaction ownership follows the pinned implementation: remote requests and
token-budget resets use the captured Step; local summary requests still use the
initial Turn model/settings (or the explicitly selected previous-model Turn).
Skill catalog metadata budgets also retain the Turn's extension model; publishing
a Step model does not refresh that separate owner.

Activation rejects fallback/unknown-provenance model metadata and changes to native
model-owned legacy authority. Catalog authority comes from the pinned bundled models
or explicit custom catalog fields; old snapshots without authority remain unknown.
This API currently covers the unmanaged, fixed full-access host path. It does not
implement managed approval policies, Guardian review execution, reviewer-only updates,
provider changes, or environment/permission changes; those remain alignment gaps.
Remote catalog hosts can supply `model_metadata_resolver` at Runtime construction;
Runtime owns and joins that asynchronous preparation on cancellation/close.

Each admitted Turn pins model choices, resolved model metadata, window/output policy
and skill allocation. Its credential-free snapshot is saved in the first Turn record
and in checkpoints, including the crash window before a graph checkpoint exists.
Pending recovery uses that snapshot, rejects conflicting snapshots or a different
provider identity, and leaves future defaults independent. Legacy records without a
snapshot retain their existing current-configuration fallback. This is a Corki
checkpoint adaptation, not a promise to restore every host configuration field.
The pinned metadata applies only to that exact admitted model; it is not inserted
into the prefix-matched catalog used for other models or previous-model compaction.
Resolved metadata records whether it came from the catalog or fallback defaults.
Unknown models emit a warning for their admitted Turn; old snapshots without
provenance retain an unknown value rather than being treated as a catalog match.
Checkpoint metadata arrays are restored to immutable tuples; a present but invalid
model snapshot fails recovery instead of silently selecting current defaults.
Context body-prefix accumulation remains thread-owned across these Turn views.
Already-admitted Code Mode calls retain their owner; a surviving cell's later new
calls are admitted by the current worker, matching the native dispatch broker.

Press `Ctrl+C` to leave the CLI. `Enter` submits a message; `Shift+Enter` inserts
a newline on terminals supporting CSI-u/Kitty or xterm modified-key reporting.
`Alt+Enter`, `Ctrl+J`, or `Esc` followed immediately by `Enter` also insert a newline.
Terminals that send exactly the same bytes for Enter and Shift+Enter cannot be
distinguished by the CLI; use one of those alternatives or configure a terminal
key mapping to send `ESC [ 13 ; 2 u`. Keyboard reporting is restored when the
prompt finishes or is interrupted. The built-in `/help`, `/status`, and `/clear`
commands are handled locally.

Live text steering is enabled by default in the CLI. Use `/realtime off` (or
`[realtime].enabled = false`) to disable it for subsequent turns; existing explicit settings
are preserved. `Enter` adds instructions to the current turn; `Tab` queues a separate
follow-up turn in FIFO order. `Alt+Up` retrieves the last queued message for editing,
replacing the current composer draft; submit it again to send or requeue it.
Instructions submitted with `Enter` while streaming wait for the next sampling boundary
in the same durable turn. They do not cancel the current response, its tools, or retry backoff. The original Turn
input is sampled first; after automatic compaction, an outstanding model/tool continuation
also gets a request before queued input. `/stop` or `runtime.cancel_active()` explicitly cancels
active work. This is text steering, not microphone/audio realtime.
Stopping a turn restores queued follow-ups into the composer instead of starting them
automatically. Review or edit the restored text and submit again when ready.
On cancellation (including replacement), uncommitted steering input is returned in
`TurnCancelled.unsubmitted_inputs`, not appended to model history. SDK hosts that miss
that event can call `runtime.take_unsubmitted_inputs()` after the turn joins. This consumes
a process-local handoff; hosts that need durable drafts must persist them themselves.
Inputs already committed before cancellation retain their original history identity.
During active work, Enter on `/compact` reports that the command is unavailable; it does
not interrupt the turn. Submit it while idle, or use Tab to queue it after the current turn.
With the default live composer enabled, input remains available while compaction runs.
Enter and Tab both queue follow-ups for after the summary; they do not modify its input.
`/stop` cancels compaction and restores those follow-ups to the draft for review and resubmission.

With the live terminal composer, streamed pipe tables stay in a mutable preview until the
answer finishes, so later rows can resize earlier columns. Only newline-terminated rows enter
the preview. Final answers still use the source-backed transcript; general streaming Markdown
and commit animation are not yet fully aligned with Codex.

The realtime application owns both its output consumer and terminal input task. On
completion, cancellation or renderer failure it joins both, even under repeated
cancellation; a pending prompt cannot outlive the turn and consume later input.
Renderer failure explicitly closes the Runtime event iterator. Prompt-task cleanup failures are
logged by type without replacing the primary error/cancellation or logging prompt data.
Turn finalization checks queued input and durable input not yet included in the
model request. Once input closes, `runtime.steer()` raises
`RealtimeTurnClosedError`; the CLI retains that submission for the next turn.
After a normal failure, accepted pending inputs are saved without running another
model or tool. Cancellation instead returns genuinely uncommitted inputs to the host;
already committed identities remain in history. Queue acceptance alone is not a disk
commit: a process crash before persistence can still lose an in-memory input.

`runtime.cancel_active()` cancels the active model/tool work in ordinary and
realtime modes. The owned turn task performs cleanup and terminal persistence
without waiting for event consumption. `runtime.aclose()` joins this work before
closing shared dependencies; concurrent close calls await one shielded teardown.
If saving pending inputs fails, closing reports the error and retains storage and
the Thread writer lease. Calling `aclose()` again retries only storage cleanup;
model/MCP services stay closed and new turns remain disabled. A later turn admission
also retries a failed normal input flush when the Runtime has not been closed.
These retries retain in-memory input; they do not guarantee recovery after process loss.
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
Runtimes with databases in the same directory share `checkpoints.db`. Its idempotent
WAL/schema bootstrap retries numeric SQLite `BUSY` errors within a five-second retry
window, including extended BUSY codes. The driver's per-statement timeout remains in
force, so an already-running SQLite statement can finish after that retry window;
cancellation still joins connection cleanup. Other database errors are not retried or
treated as corruption recovery. This bootstrap policy never replays checkpoint writes,
Turn admission, model requests or tool executions.

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
export CORKI_API_BASE="https://your-provider.example/v1"
corki
```

Provider adapters support OpenAI-compatible streaming Chat Completions and the
Responses API. Model, loop, context, tool, and runtime settings can also
be placed in `~/.corki/config.toml`; environment variables take precedence for
provider endpoint, model, and secret. Set `api_mode = "responses"` for a
Responses endpoint. Provider names and hostnames, including DeepSeek, do not
automatically enable private thinking fields, reasoning replay, or alternative
structured-output formats. Ordinary transport defaults depend on `api_mode`;
hosts embedding a custom adapter can declare its capabilities explicitly.
Reasoning received from the model is rendered separately from the final answer.

Local project settings use `.corki/config.toml`, from the detected project root
through the working directory, with deeper layers taking precedence. Admit a
project in your user configuration with `[projects."/absolute/project"]` and
`trust_level = "trusted"`. Unknown or explicitly untrusted project layers are
retained as disabled sources: they do not contribute configuration or command
rules. This layer gate is separate from AGENTS discovery, which only explicit
untrusted active-project status disables.

Project `.corki/rules/*.rules` files use the same admission decision, including
when the directory has no `config.toml`. Plugin directories, skill-rule paths,
and local MCP working directories are resolved against their declaring config
directory before merging. Permission workspace-root patterns remain relative to
the policy working directory. Project settings cannot replace provider routing
or credentials (`provider`/`providers`), or the host's `execution.compiler`.
Ignored settings and disabled layers are reported once through Runtime warnings.
Settings and admitted rule sources are session snapshots; editing files does not
silently reload a running session. Cold startup re-evaluates them.

The project directory spelling is Corki-specific; `.codex/config.toml` is not
automatically loaded. This local loader does not yet implement the complete
Codex system/cloud/MDM/session config stack or project hook execution. Without
an execution compiler, the existing legacy execution path still applies.

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
Live Responses ignore `x-reasoning-included`, private usage metadata and Codex
rollout budget units. Ordinary token counters remain authoritative; older reasoning
is estimated separately using the local envelope adjustment. Legacy stored header
flags remain readable in original archives but cannot change runtime counting.
Local estimates exclude archive-only classifications and private item metadata;
discovered tool definitions count in the request's loaded tools, not again as
hidden search-result schemas. Ordinary content, identities and media retain their costs.
Full-request local hard-limit checks remain as a compatibility safety
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
include_environment_context = true
effective_context_window_percent = 95
# auto_compact_tokens = 49152  # optional earlier compaction
# auto_compact_token_limit_scope = "total"  # or "body_after_prefix"
```

The default local environment context uses durable typed snapshots: date/timezone
updates omit unchanged cwd/shell, and field values are XML-escaped. Compaction restores
the full current environment, not the last delta. Setting
`agent.include_environment_context = false` suppresses new environment fragments and
silently resets their comparison baseline; it does not erase earlier conversation
messages. Re-enabling emits a full snapshot. Legacy/unknown snapshot metadata also
falls back to a full refresh. This does not yet supply remote/starting environments,
PowerShell versions or Codex permission/network/subagent metadata.

System timezone names are captured at Turn admission and checkpointed; each Step
still refreshes the local date. Steering and token-budget resets keep the admitted
name. A legacy checkpoint lacking this field captures it at its next prepare boundary,
without rewriting an already prepared model request. Linux uses system timezone files
(including OpenWrt fallback); macOS refreshes CoreFoundation's system timezone cache.
Windows uses a fixed, non-interactive, two-second PowerShell query of the WinRT
Calendar timezone API; unavailable/failed resolution falls back to `Etc/UTC`.
This adapter differs from Codex's direct Windows API call and can add startup latency.
macOS native resolution and offline platform fixtures have been exercised; native
Windows/Linux execution and other Codex platform resolvers have not been verified.
Shell selection is Session-owned, separate from this Turn-owned timezone state.

Local compaction retains base instructions and appends a user compaction
request. Only when the provider reports input overflow does it remove the oldest
items from the summarization request,
including matched tool call/results, and logs the omission. Original history
stays on disk; the summary cannot preserve content it never received. The
current user input is kept verbatim outside the summary; if it cannot fit with
the replacement context, preparation fails without installing that replacement.
Output exhaustion is a separate error and does not trigger history trimming.
Past user text is retained newest-first under a separate 20,000-token budget,
using Codex's approximate four UTF-8 bytes per token, then restored to chronological
order. A partly retained message keeps both ends with an explicit truncation marker;
its source identity and timestamps remain intact. This text budget does not charge
message/annotation overhead. Automatic compaction independently checks the entire
replacement request, including the marker, tool definitions and protected current
input; small provider windows can therefore retain less past text. Raw history is
never shortened.
Local compaction owns an independent bounded retry loop using `stream_max_retries`
and exponential backoff. Each attempt closes its stream; input-overflow trimming
resets that budget. As in Codex's local path, other model errors retry even when
ordinary sampling would not, and do not use the sampling Retry-After delay.
The adapter does not add a second sampling retry loop. Only the last nonempty
assistant summary is installed; failed attempts and cancellation preserve old history.
Main-sampling input overflow ends the Turn; it does not implicitly compact and resample.
Use `/compact` (or consume `runtime.compact()`) for a standalone manual compaction
turn, including on an empty thread. It cancels active work and atomically installs
the replacement history. The local path requests a summary with no tools and retains
user text plus the summary. It does not
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

Base instructions can be set with top-level `instructions = "..."` or
`model_instructions_file = "rules.md"`. Relative file paths resolve against the
configuration file's directory. The UTF-8 file takes precedence over the TOML text;
its trimmed contents must be nonempty, and read errors stop configuration loading.
Contents are captured when settings load, not reread at each model step. Hosts can
also supply `CorkiSettings(base_instructions=...)`; an empty string is an explicit
override. Explicit instructions take precedence over saved session instructions,
then the local model catalog. A resumed override does not rewrite the original
session's base metadata; new sessions and forks persist their selected base and origin.

All providers use this ordinary summary flow, including OpenAI-labelled providers and
official-looking configured URLs. Legacy remote-compaction flags do not select another
Runtime path. Model downshifts summarize with the previous model before admitting the
current input. TokenBudget triggers also use ordinary model-generated summaries.
Dedicated SDK transports have been removed. Explicit legacy SDK compaction requests
fail before networking and direct callers to Runtime.compact(). Cleanup is still in
progress for old opaque-history protocol handling and configuration compatibility.
Old opaque compaction windows are rejected before networking; their original records
are not deleted. Automatic migration from the original history is not implemented yet.
Plain archived messages remain valid ordinary Responses input.

Responses SSE now validates its typed envelope independently of Chat Completions:
invalid constants, typed duplicate fields, string/index types and materialized Unicode
or nesting failures are skipped without publishing their output. A valid terminal is
still required. Nested SSE JSON objects retain last-key-wins semantics; legacy compact
and durable item decoding reject duplicate known fields but tolerate unknown/skipped
duplicates. Legacy byte input uses UTF-8, not Python's UTF-16/32 autodetection. Top-level
ignored fields are not subjected to the materialized-value checks. Index-only compatible
provider routing remains supported separately from Codex's native envelope fields.
Native JSON numbers now retain precise decimal/exponent spelling when ordinary Python
numbers cannot roundtrip it. Remote compaction, archived replay, media/hosted output
projection and Responses HTTP requests use an exact numeric encoder; native search
history also retains the original argument Value alongside any error Observation.
Numbers remain JSON numbers, not quoted strings. Chat's separate JSON transport is
unchanged. Streamed added/done items now apply the Value-to-buffered-Content numeric
boundary, including unknown nested fields; this is not a global 64-bit number limit
on raw legacy JSON. Private Number/RawValue maps are classified only at their relevant
typed/Value boundary and only by the first key. Unfinished added/delta tool buffers
are never promoted to executable calls at stream completion. The compatible terminal
output list applies the same item admission check and replaces unfinished argument
previews with the complete item's arguments, without replaying already-completed calls.
Added/done and compatible terminal items now validate required/optional fields and
nested content before preview mutation, publication or tool dispatch. Invalid item
shapes are skipped; malformed JSON inside a valid argument string instead becomes
an error Observation, retaining even an empty string in replay. Function/custom names
and call IDs preserve exact strings: unregistered empty names produce tool errors,
not preview-name execution or a fatal turn, and empty call IDs are not replaced.
Full typed projection, lossless-f64 reformatting and ordinary local handler argument
number parsing remain separate gaps. WS/model-switch fallback, extreme ignored-field
encoding/depth details, adjacent image-resize notices and full
multi-agent/client-authored metadata retention remain separate alignment gaps.
Ordinary model and summary completions share typed usage validation: present usage
requires signed i64 input/output/total counters, and present token-detail objects
require their defined counters. Missing or falsy values are not synthesized as zero.
The latest typed stream error is retained until EOF or a later valid completion;
invalid terminals cannot install a compacted window. Cache-write counts, exact rollout
budget numbers and usage metadata survive model-step persistence and cold recovery.
Opaque checkpoints retain observed completion metadata separately from model-visible
content. Existing rows receive empty usage-detail defaults without rewriting their
historical payloads. Saving rollout budget numbers does not implement Codex's complete
budget enforcement or analytics control plane; those remain alignment gaps.
Each prepared turn now stores a host-only model/compaction-hash snapshot. After cold
resume with a different model, a qualifying context-window downshift first compacts
the existing history with the previous model; two present, differing `comp_hash`
catalog values also trigger this step, even for the same model. Current user input
is added afterward. Every compaction trigger, including TokenBudget, uses ordinary
model-generated summaries with Harness-owned history replacement. The current model's normal
budget check still runs afterward. Installed same-turn checkpoints prevent repeated
compaction during recovery. Old histories without a trusted model snapshot keep normal
budget-based preparation; no previous model is guessed. This does not add live `/model`
switching or an official-account fallback after the old model fails.
Previous-model compaction also resolves its own reasoning effort: retain a supported
selection, otherwise choose the lower middle of the old model's ordered presets, then
its default if there are no presets. Static catalog entries accept
`supported_reasoning_levels = [{ effort = "low" }, { effort = "high" }]`, optional
`default_reasoning_level` and `multi_agent_reasoning_effort`; bundled reference entries
include these fields. Missing metadata means an empty preset list and no default.
The temporary request translates `persistent` to `disabled` and `ultra` to the pinned
model's supported preference/max/last non-ultra effort (or medium). An explicitly
resolved empty effort does not inherit the new model's adapter setting. This applies
to ordinary summary requests without mutating the shared adapter or following
main requests. Main, compaction and memory requests now carry immutable model metadata
through the shared reasoning resolver. Explicit effort wins over the model default;
the same wire conversion applies to ordinary requests, while already-resolved temporary
requests are not converted twice. `[provider].reasoning_summary` optionally selects
`auto`, `concise`, `detailed` or `none`; otherwise each model's `default_reasoning_summary`
applies. `supports_reasoning_summary_parameter = false` suppresses the summary parameter
independently of effort. Bundled defaults come from the pinned catalog, not a blanket
auto setting. Model-aware Responses requests include reasoning even when empty and
request encrypted reasoning content; there is no dedicated compact endpoint.
Compatible Chat sends effort only, and provider capability filtering remains in effect.
Standalone adapter requests without metadata retain their existing inheritance behavior.
Memory extraction/consolidation keep their selected low/medium effort and resolve their
own model's summary; neither changes the shared main adapter. Complete cold-step/live
configuration snapshots and complete media/tool capability gates remain alignment gaps.
Explicit `[provider].service_tier` now supports `fast`/`priority`, `flex`, `default` or a
model-advertised custom ID. `[features].fast_mode` defaults true but does not select a
tier; false suppresses all request tiers. Catalog `service_tiers = [{ id = "priority" }]`
describes support; `default_service_tier` never automatically selects a paid/faster tier.
Unsupported selections produce a startup warning and are omitted. The first owned Turn
carries that warning, without adding it to model history. Composed providers explicitly opt in with
`[provider].supports_service_tier = true`. Supplied adapters keep their own capability.
Main, ordinary compaction and memory requests filter the captured explicit selection
against their own model; temporary old-model compaction cannot recover a tier already
filtered out by the initial current model. Provider names, domains and API-key sources
do not enable capabilities or dedicated compaction routes. No actual paid request is made
by configuration loading, and no live tier-switch or native-auth branch is implied.

Responses Lite is removed. Legacy `use_responses_lite` catalog settings are ignored, and
checkpoint metadata is normalized to false. Ordinary Responses keeps top-level instructions
and tools, preserves image detail, and sends neither the internal Lite header nor
`additional_tools` prefixes or `reasoning.context = "all_turns"`.

The former Lite tool-inventory opt-in no longer generates request metadata. Legacy checkpoint
inventory slots are not evidence of currently exposed tools. The inventory helper is removed.
Responses does not transmit legacy `ModelRequest.client_metadata` or derive window/turn
headers from it, even for OpenAI-labelled providers and addresses. Caller-owned request data
is not modified. Remaining item metadata, routing and compaction extensions are still being
removed as part of the ordinary-protocol cleanup.

`[features.tool_registry].error_on_tool_collisions = true` opts into strict selected
Turn planning (default false). Enabled Harness `exec`/`wait` and `tool_search`
controls normally shadow conflicting source tools only in that Turn's plan;
strict mode instead fails the Turn before sampling. It also rejects conflicting
nonempty namespace descriptions, including hidden tools. The removed Lite inventory no longer imposes remote owner-schema collision checks.
Internal groups keep the first nonempty description; both model transports expose
flat ordinary function definitions, never native namespace or tool-search output.
Historical search results remain archived, but only definitions matching the current
catalog and active context grant loaded-tool eligibility. A replaced definition must
be searched again. This does not relax trusted registration or owner-scoped publication.

Deferred namespace context chooses the first nonempty description in the captured
registry order before sorting namespace names for display. Directory reordering
therefore produces a context update when that winning description changes. Context,
wire grouping and strict collision checks share Rust-compatible whitespace rules.

External candidates are selected in fixed order: trusted core, MCP, extension,
then dynamic tools. Duplicate external names keep the first winner; strict planning
reports the first collision. MCP collisions skip only the affected tool, retaining
the connection and healthy tools. Plugins publish and roll back their own extension
catalog. Embedders can reserve a dynamic publication capability before startup with
`owner = registry.create_owner(source=ToolSource.DYNAMIC)` and atomically replace
its candidates using `registry.replace_owned(owner, tools)` (`ToolSource` is exported
by `corki.tools`). Removing a winner restores lower-priority candidates in subsequent
plans; already captured plans retain their handlers and definitions. External tools
cannot claim plain `exec_command` or `shell_command`, even when core omits them.
Tool descriptions and remote metadata cannot change these host-selected priorities.

Responses request copies always remove top-level internal item metadata and private
function-argument extensions, regardless of provider name/address or legacy capability flags.
Same-named business fields inside user/tool content and schemas remain untouched. Stored
history is not rewritten. Legacy content-kind settings cannot re-enable this wire extension.
Response-side archival metadata remains readable while its remaining special handling is
being cleaned up separately; this is not a claim of full ordinary-protocol isolation.
Raw hosted-tool archives also retain compatible provider data, but native request
replay now decodes their metadata: server-supplied cell IDs or executed-call records
cannot impersonate host-owned fields. The original archive is not rewritten.
Chat/compatible Responses hosted-event text also omits root protocol metadata;
otherwise wrapping the payload as text would bypass the native field filter.

New Responses assistant, reasoning and tool-call records retain only ordinary server
item IDs as provenance. Root passthrough metadata and encrypted function arguments
are ignored before live item decoding, including malformed private values; same-named
fields inside business content remain untouched. Old SQLite/checkpoint provenance
remains readable but private fields are not replayed, even after provider switching. Request
projection now copies only the ordinary server item ID: it does not generate
private timestamps, turn metadata, content-kind vectors or encrypted arguments and
then depend on the final filter to remove them. Stored archival fields remain intact.
Internal media classifications and archival compatibility still need separate cleanup.
Internal history
still uses durable host identities and creation times, never a fresh request clock.
Wire IDs are independent of storage and execution IDs: host IDs receive variant
prefixes, while unprefixed server IDs stay archived but are omitted from requests.
User content kinds follow emitted text/image/audio parts internally. Ordinary content,
identity and call changes in repeated completion events still fail the Turn; ignored
private-field changes do not. Legacy rows without the optional field remain readable.

Native assistant content arrays and reasoning summary/content arrays are now stored
separately from the flattened display text. Responses replay and compaction use the
typed original body, including citation markup; UI text and the structured citation
remain clean and independent. Repeated completion cannot change part boundaries or
raw reasoning while retaining the same displayed text. Body data, including hidden
text and media, participates in response limits and applicable context estimates;
opaque reasoning keeps its existing decoded-envelope estimate. Media preparation and
unsupported-media notices operate on model copies, not the stored body. Chat retains
its text-oriented assistant format and carries additional source media/input context
in a labeled data message before the assistant call group, keeping tool replies
adjacent. This compatibility projection is not identical native message serialization.
Old rows/checkpoints without a typed body retain their previous text fallback.
Memory citations use the native `<oai-mem-citation>` delimiter, with the earlier
`<corki-memory-citation>` accepted as a compatibility alias. Streaming and complete
messages hide opened blocks even when metadata is invalid or the closing delimiter
is missing, preserve surrounding whitespace, and immediately release text after a
closed block. Matching is literal and non-nested. Entry and ID sections are optional
independently; raw `rollout_ids` remain provenance while only valid UUIDs affect
usage. Paths/line numbers in citations are inert metadata, not file access grants.
Usage is recorded per completed message, not again at response completion or durable
step replay. It remains best-effort telemetry, not a crash-atomic transaction with
the model ledger. Chat now replays raw output text too: when no native body exists,
changed display text retains an explicit typed compatibility body. Already-erased
citations in legacy rows cannot be reconstructed. This parser change does not claim
the full memory read/consolidation instruction policies are aligned.
Unsupported-image/audio replacement now updates the corresponding positional kind
to `images.unsupported`/`audio.unsupported` on the model copy. Native message
normalization fills missing legacy kinds with `unknown` and drops excess entries;
when both modalities are supported it leaves the source vector untouched. User
media copies carry optional kinds through image-then-audio preparation so a host
notice is not relabeled as user text. The original archive remains unchanged, old
user rows/checkpoints without the optional field still load, and provider/privacy
filters are applied after projection. Processing-error/resize notices remain
separate alignment work.

Context producers now carry an optional `content_kind` through prompt rendering,
durable context history, updates/removals, cold recovery and Responses projection.
Native counterparts declare their source-owned classes (AGENTS, environment,
collaboration mode, selected skills, deferred namespaces, memory read instructions
and token-window guidance). Classification-only changes publish an update;
removals retain the preceding producer's classification. Typed text arrays keep
content and classification paired, with existing provider/privacy gates applied
afterwards. Old rows/checkpoints remain unclassified; neither keys nor literal
tags in user text manufacture provenance. Corki-specific inventory and steering
producers use `corki.*` classes, not fabricated native lineage.

Context message membership is frozen before atomic history writes. Initial context
bundles normal developer fragments, then standalone developer fragments, then user
context; incremental updates preserve slot/insertion order and merge only adjacent,
same-role mergeable fragments. Selected skill bodies remain separate input-attached
messages. The per-key comparison journal stays intact; later appends cannot enlarge
a previously sent message. Responses projects paired text/kind arrays and Chat uses
multipart text for grouped messages. Request estimates count a shared envelope once.
New-window reinjection assigns fresh membership, while local overflow removes a
whole oldest message rather than leaving a partial group. Old ungrouped rows remain
separate, and incomplete or inconsistent new groups fail validation. These changes
do not close the skills-catalog authority split, all producer-specific ordering/hint
placement, retained agent/client-developer policies or full
fragment-bound/body alignment. Local archive recall still exposes the per-item
comparison journal, not an identical native history service.

Local compaction summaries are user-level handoff messages, not system/developer
instructions. Chat and Responses share the pinned Codex handoff prefix; capable
Responses requests classify the input text as `compaction.summary`, respecting the
provider metadata and content-kind switches. Token estimates include the full prefix
and classification. Readable archive/legacy-message projections use the same text and
role. Durable checkpoints keep their bare summary, identity and replacement position,
so old rows and cold reopen do not require rewriting history. Repeated compaction does
not turn a checkpoint into real user input. Opaque remote compaction and Token Budget
reset retain their separate contracts; this is not complete local-compaction parity.

Local summary requests include the entire accepted model-visible history, including
the current user input during mid-turn compaction. Incoming pre-turn input is not
submitted until after compaction; retaining a verbatim user copy in the replacement
is independent of letting the summarizer read accepted input. Local token estimates
do not delete summary evidence before the first request. Only a provider context-window
error removes the oldest message (and its matching tool counterpart), resets the
local retry counter and retries with the same compact-turn routing state. A rejected
prompt-only request terminates without further trimming. Normal-request and compacted
output budget checks still apply; raw stored history is never removed by this fallback.

V2 compaction retains the successful attempt's modality-normalized history. Native
user messages returned by earlier legacy compaction keep their payload identity,
metadata and content types; they do not become fresh user or memory evidence.
Budget-boundary selection moves each content part with its positional classification,
including atomic image labels. Retained ordinary users keep their original wire ID
and source lineage while receiving new append-only journal IDs. Local compaction
still rebuilds text-only user messages, with a single user.text classification.
Unsupported media already replaced in an installed V2 window is not resurrected
by later model capability changes; the original archive remains unchanged.

Corki does not capture or replay the Codex-specific `x-codex-turn-state` header.
Ordinary model retries, multi-step turns, steering, cancellation and cold recovery do not
depend on an official routing token. This does not change tool execution ledger ownership.

Experimental TokenBudget is opt-in with `[features] token_budget = true` (or
`CorkiSettings(token_budget_enabled=True)`). Budget thresholds, `/compact`, and
the ModelOnly `new_context` request use the same Harness-owned ordinary summary
path. They do not install an empty window without a summary. Failed summaries leave
the original history intact. Current-input protection, retained user copies, and
atomic replacement markers follow the ordinary compaction rules; archived records
remain available. Manual compaction rebuilds world-state context on the next turn.
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
the harness ranks deferred metadata with BM25 and returns complete Top-K
definitions (default 8). Matching tools are not silently dropped by a separate
fixed schema-token budget. Ordinary function-calling providers receive only the discovered
schemas in subsequent requests. Built-in tools remain directly available.

MCP input schemas are normalized and unused root definitions removed before model exposure.
Above 5000 normalized JSON bytes, the adapter progressively drops descriptions, local
definitions, deep object detail and compositions. This is best-effort, not a hard cap for
ordinary MCP tools. Agent Plugin functions additionally fall back to open object parameters
above 8000 serialized bytes; ordinary flat declarations then have per-tool 8000-byte and
combined 64000-byte Agent limits, counting the larger Chat/Responses UTF-8 representation.
Overflow tools remain Hidden across Step replanning.
An unrepresentable tool schema skips that tool without disabling valid server siblings.
Raw shared catalogs remain separate from these model-facing projections.
Remote MCP arguments are parsed as JSON objects, not validated against the reduced schema;
server policy, approvals, visibility and execution claims still apply. Direct whitespace-only
arguments are omitted on MCP wire. Code Mode retains its object boundary and supplies `{}`
when no input is passed. Internal output schemas describe results but are neither HTTP tool
fields nor local result validators. `ALL_TOOLS` descriptions include input/return contracts
for on-demand discovery without eagerly adding deferred schemas to model requests.

Direct MCP raw arguments retain arbitrary-precision JSON numbers through host approval,
stdio, HTTP and executor HTTP bodies; exponent spelling follows the pinned native scanner.
The model adapters keep the original text even when a decoded float cache cannot be safely
stored. Execution claims additionally bind precise raw values when they differ from that
cache, so distinct remote payloads cannot reuse one observation after rounding. Ordinary
legacy hashes remain compatible; old lossy hashes cannot prove an exact payload and are
refused without replay or backfill. This does not yet establish numeric/typed equivalence
for incoming MCP catalogs, structured results, or the JavaScript Code Mode boundary.

Loaded definitions count toward the full request budget, including native search
outputs and freeform grammars. Normal context compaction can release discovered
definitions from the active window; they remain intact in the archive and can be
searched again. The raw transport-size guard and bounded UI previews still apply.

MCP initialization instructions are retained as source descriptions and searchable
metadata. The discovery entry lists sources in sorted order, reserving complete
names before sharing a 512 KiB UTF-8 description budget. This is a metadata cap,
not extra model context: the full request still must fit the configured window.
Ordinary MCP tool metadata cannot impersonate hosted connector provenance.

Set `required = true` on an MCP server to make its startup a session-admission
requirement (default `false`). Failures of enabled required servers are reported
together before a Turn or model request starts; ordinary server failures remain
warnings. Disabled or policy-denied servers are not started. Retrying initialization
can retry failed connections, but never replays a model request or tool call.
MCP connections prepare in parallel; directory publication retains captured source
order, independent of completion order. Cancellation joins every startup task before
connection cleanup. Session admission waits only for required servers. Optional
startup continues after `TurnStarted`; Step capture includes ready tools and waits
up to the shared `mcp_optional_startup_grace_ms` deadline (top-level TOML, default
1000). Later Steps do not restart that deadline. Setting it to 0 disables the grace
and waits for startup completion, rather than skipping pending tools immediately.
Named tool/resource calls wait for their own server, not unrelated pending servers.
Interrupting an active Turn retains session-owned startup; an idle interrupt cancels
unfinished active startup, and Runtime close joins all remaining work.
Cold checkpoints record the server identities behind their saved Step definitions
and rebind those servers without resampling or replaying completed calls. Legacy
checkpoints without this field retain their former full-directory readiness wait.
Runtimes share a process-local server-definition cache (32 identities, 30-minute TTL).
Pending optional connections can expose a cached directory immediately, including
when grace is 0; actual calls still wait for their own live connection and its approval
policy. Explicit/configured required servers still wait. Cache entries omit all tool
annotations, and apply current host filters, exposure and plugin attribution. Failed
startup cannot retain an ordinary server's cached tools in the next captured view.
Transport configuration, named credential values and concrete environment identity
separate entries; dynamic header helpers and remote-sourced stdio env bypass sharing.
The experimental `codex/tool-catalog-cache: {cacheable: false}` initialize capability
revokes sharing for that identity. Hosts can pass a separate `MCPToolCatalogCache`
as `LangGraphRuntime.create(mcp_tool_catalog_cache=...)` to isolate embedded hosts.
Host-tagged `SessionSource.subagent(...)` sessions can defer transport construction
when a fresh cached catalog has at least one allowed model-visible tool. This includes
initial session creation. Ordinary CLI/editor and `Internal` sessions remain eager;
selected-plugin servers and explicit `request_mcp_refresh()` remain eager too.
A dormant configured-required server may satisfy admission from its cached directory,
but explicit input requirements and actual tool/resource calls start and await it.
Cache expiry/opt-out triggers startup on subsequent capture. Idle cancellation leaves
unused dormant entries untouched; replacement and shutdown join them without starting
a transport. Idle background prewarming remains an alignment gap. These mechanisms
are separate from the already implemented tool-search index/cache.

MCP tools with `_meta.ui.visibility` arrays that omit `model` are app-only: neither
live nor cached model discovery exposes them, and model-call admission rejects them.
Their raw names still participate in catalog-wide collision normalization.

Explicit user inputs also request startup readiness before model sampling:
`[$docs](mcp://server)`, `[@plugin](plugin://package-id)`, and explicitly selected
skills with MCP dependencies (or an owning plugin). They extend the current Turn's
requirements, including newly steered inputs, without restarting the shared grace
deadline for unrelated servers. Requirements survive compaction/checkpoint recovery
and do not leak into a new Turn. They do not install missing servers, override
permissions/exposure, or turn an optional server failure into a fatal startup error.
Only actual current user input contributes requirements, not assistant/tool output
or retained summaries; basic Guardian evidence inputs do not activate these paths.

Embedded hosts can pass `mentions=(InputMention("label", "mcp://server"),)` to
`runtime.stream(...)` or `runtime.steer(...)`; import `InputMention` from
`corki.protocol`. For a skill, use its exact file path and `kind="skill"`.
These typed selectors persist separately from user text; the display label is not
inserted into the model's user message. Old records without selectors remain valid.
Explicit plugin selection adds an input-scoped developer hint (at most 4096 UTF-8
bytes) for available MCP capabilities and the plugin's skill namespace. Skill
`agents/openai.yaml` dependencies retain normalized type/value and optional fields;
unresolvable entries do not erase valid siblings or the implicit-invocation policy.
Malformed metadata syntax/types still discard that optional metadata as a whole.

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
deferred_tool_world_state = false  # opt-in experimental namespace context
non_prefixed_mcp_tool_names = false  # experimental; default names use mcp__server::tool
# non_prefixed_mcp_tool_servers = ["docs"]  # only these raw server names omit the prefix
```

MCP tools use canonical `mcp__server::tool` identities. The whole staged catalog is
normalized before publication: invalid characters (including hyphens) become `_`,
sanitized namespace/leaf collisions receive deterministic identity hashes, and
duplicate raw tools keep their first definition. Remote calls still use the original
server and tool name. Initialize instructions supply namespace descriptions; tools
cannot claim connector provenance through their own metadata. The opt-in non-prefixed
feature omits all prefixes when the server list is absent, none when it is empty,
and only listed raw servers otherwise. With the feature disabled, the list is ignored.

Existing flat-name history and execution ledgers are not rewritten. Old compatible
discovery definitions become stale and require another search; native search history
is retained. Verified completed results and unknown-outcome refusals remain authoritative,
even after a tool is renamed or removed. Unexecuted obsolete names return a recoverable
tool error; the harness never guesses a replacement route or replays an unknown effect.

With `deferred_tool_world_state = true`, search metadata no longer repeats its
source directory. Instead, each prepared Step supplies a developer `<tools>`
catalog of non-default deferred namespaces from that same frozen tool snapshot.
This matches Codex's opt-in experimental path; it does not change BM25 ranking or
eagerly load schemas. Descriptions use their trimmed first line (250 characters);
escaped fragments are capped at 4096 UTF-8 bytes. Complete comparison maps remain
durable even when visible entries are omitted. Changes append added/removed entries;
compaction reinjects a full current catalog. The option has no effect with search
disabled. Responses preserves the developer role; the Chat compatibility adapter
maps it to system. Both wire paths are tested offline, not with a live model.

The default `search_mode = "compatible"` uses Harness-owned search through an ordinary
function tool. Search results load selected definitions into subsequent requests' `tools`;
the model then calls ordinary functions. This path does not require native search or
namespace model capabilities. Legacy `search_mode = "native"` uses the same compatible
path; it cannot enable native search envelopes. Explicit `"disabled"` disables search.
Available direct tools follow their original exposure masks; omitted surfaces are not restored.
Search registration and MCP context use the same frozen tool snapshot. Retries and cold
checkpoints preserve admitted state; compaction builds its own plan. Existing raw archives
are preserved, but historical native items cannot re-enable excluded request protocols.
Internal MCP grouping and routing are encoded as flat ordinary function names on the wire.

Definitions survive steps, turns, and durable resume while their search results
remain in the active history. Compaction releases them; the model can search
again. All calls are checked against the current step's dispatch plan. Compatible
loading ignores changes only to concurrency and output budgets, and uses the new
execution settings without searching again; schema/identity changes still unload
definitions. Raw archived output remains as observed even when tools change or
disappear; it does not override current dispatch eligibility or restore native wire protocols.
Python plugins can opt into discovery with `register_tool(..., exposure="deferred")`.

Raw-input tools can declare `ToolSpec(..., input_kind="freeform", freeform_format=...)`.
The default `[tools] freeform_mode = "compatible"` exposes a JSON function accepting
exactly `{ "input": "raw source" }`; dispatch and durable history retain the raw string.
The legacy `freeform_mode = "native"` setting also uses this ordinary function path.
Custom tool definitions and calls are not emitted; native custom response events are
rejected as protocol errors. Deferred raw tools use Harness-owned function-tool search.

Namespaced extension tools use canonical names such as `history::read_item` and
an optional `ToolSpec.namespace_description`. The legacy
`[tools] namespace_mode = "native"` setting does not enable native namespace objects.
All providers use the ordinary `"compatible"` projection with
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

[features.code_mode]
direct_only_tool_namespaces = ["notes"]
excluded_tool_namespaces = ["private"]
```

The namespace lists default to empty and match canonical namespaces exactly; plain
tools (including prefixed plain MCP names) belong to `functions`, not their MCP
server or search source label. `direct_only_tool_namespaces` converts Direct,
Deferred and CodeModeOnly registrations to DirectModelOnly, bypassing search and
remaining directly visible in `code_mode_only`. Hidden and existing model-only
registrations are unchanged. The projection also applies in direct/fallback mode.
`excluded_tool_namespaces` only removes tools from new cells' nested objects,
`ALL_TOOLS`, and exec definitions; it leaves exposure unchanged. Such tools remain
directly usable in mixed mode if already Direct, or searchable if Deferred. Exclusion
does not restore a direct surface hidden by `code_mode_only`. Both lists feed the
same frozen registry view used by discovery, context and Lite inventory, including
owner-scoped tool refresh. Neither list grants permissions or revokes calls from
already-created cells; existing cell/Step admission and side-effect recovery still apply.
`features.code_mode = true` (or its table's `enabled = true`) and
`features.code_mode_only = true` also select a mode when `tools.mode` is absent;
the existing explicit `tools.mode` selector takes precedence over those feature flags.
An optional `[models.catalog.<model>] tool_mode = "direct" | "code_mode" | "code_mode_only"`
overrides the configuration mode. Unknown string selectors are ignored; non-string values
are invalid. Tool availability follows the model captured when a Turn is admitted, even
if an active Step switches its sampling model, matching the pinned Codex implementation.
A new Turn adopts the new model's mode. Independent candidate/previous-model compaction
plans cannot overwrite the selected tools or Lite inventory. Each selected snapshot also
retains its original host directory for realtime candidate planning. MCP masks, search,
nested definitions and MCP context descriptions consume that same selected view.
Saved Turn metadata and Step mode survive cold recovery; existing definition/ledger checks
still prevent unsafe redispatch. MCP server masks compose separately below.
The bundled pinned catalog also supplies its real mode defaults: its seven Lite model
entries select `code_mode_only`; the other four entries leave mode selection to configuration.
An explicit custom catalog is authoritative and can set a different mode for its model.

`code_mode` adds `exec` and `wait`; `code_mode_only` routes ordinary tools through
JavaScript while retaining model-only tools. Each `exec` runs a fresh ES module in
a separate QuickJS-NG process, with top-level `await`, `tools`, `ALL_TOOLS`, `text`,
`notify`, `store`/`load`, timers, `yield_control` and `exit`. Nested calls use the
normal handler parsing/validation, execution ledger, concurrency and cancellation paths.
Only emitted output reaches the model; nested calls are not fake model messages.
Cell `exec`/`wait` bodies use the requested/default 10,000-token output budget (four UTF-8 bytes
per approximate token). Truncated text retains both ends and an explicit omission marker; mixed
media follows the shared ordered-output policy. Script status is prepended after body truncation,
and the complete result then follows the configured model history budget. There is no extra fixed
40,000-character cap on newly executed cells; old persisted explicit caps remain compatible.
Physical bridge/cell buffer limits still apply independently of these model-output policies.
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
ledger, history and checkpoints without decoding or slicing them. Every Responses
and Chat provider receives an explicit unavailable-content notice instead of ciphertext;
native encrypted tool output is excluded. The old
`[provider] supports_encrypted_tool_output = true` setting is rejected.
This is not decryption or equivalent history recovery and does not erase stored
payloads. The ordinary text-output budget
does not truncate ciphertext; aggregate binary/opaque tool content retains the
32 MB storage safety cap. Request budgets count the unavailable-content notice,
not the archived ciphertext that is never sent.
JavaScript's default tool-result projection excludes encrypted blocks.
No provider label or legacy capability flag enables the official history/notes service.

Readable archived user/assistant messages can be replayed through either ordinary
Responses or Chat Completions, without rewriting the stored envelopes or sending
internal metadata. Opaque legacy compaction records still require migration;
they cannot reactivate a dedicated compaction protocol.

History/notes recovery is local for every provider, including OpenAI-labelled configurations.
Enable `[features.token_budget] enabled = true` and `use_history_notes_extension = true`.
The former `provider.codex_backend` setting is ignored. No account credentials, remote notes
requests, encrypted argument extensions or history ingestion metadata are used.

Four `history::` and five `notes::` model-only tools use the normal registry/executor/ledger,
including Code Mode sessions. A bounded 4000-byte thread hint is loaded per window; failures
omit the hint and cancellation propagates. Unknown write outcomes are not automatically replayed.
Configuration changes require reopening the Runtime.

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

The default shell is selected once per Runtime Session from the Unix account shell and
available supported executables, not from ambient `SHELL`. macOS falls back through zsh
then bash; other Unix systems try bash then zsh; Windows tries PowerShell. The last
fallback is `/bin/sh` or `cmd.exe`. Environment context names the same selected shell.
Both pipe and PTY execution preserve it when `login=false`; only startup flags change.
An `exec_command.shell` name/path selects a supported type whose executable is discovered
by the host—it does not execute that supplied path directly. Per-call overrides do not
replace the Session default. Set `tools.allow_login_shell = false` to default to non-login
execution and reject explicit `login=true` before spawning. This is a login-mode policy,
not an OS sandbox or command approval system.

Prepared Steps checkpoint their default shell kind/path so a cold-resumed request and
its tool execution remain consistent even if the reopened Session selects another shell.
New Steps use the current Session selection. Old checkpoints without executable identity
use the reopened Session default; environment prompt text is never parsed as executable
authority. Missing selected executables produce an error, not an automatic shell switch.
Direct and Code Mode paths, pipe/PTY and cold recovery have offline local verification;
Windows shell discovery/argv have fixtures, not native Windows execution evidence.
Remote shells, zsh-fork/shell snapshots and elevated Windows sandbox policies remain open.

Shell tools build an explicit child environment from `[shell_environment_policy]`.
The default is `inherit = "all"` and `ignore_default_excludes = true`; ordinary API
keys are therefore inherited unless configured otherwise. `inherit = "core"` uses
the platform core-variable list; `"none"` starts empty. Filtering order is inherited
variables → optional `*KEY*`/`*SECRET*`/`*TOKEN*` exclusion → custom exclusions → `set`
overrides → nonempty include-only patterns → restricted launch-context removal.
Patterns match whole names case-insensitively with only `*` and `?`, not regex or
bracket classes. Canonical filters cannot coexist with legacy `exclude`/`include_only`
arrays, even empty arrays; case-variant duplicate filter keys are configuration errors.

```toml
[shell_environment_policy]
inherit = "all"
ignore_default_excludes = false

[shell_environment_policy.filters]
"PRIVATE_*" = "exclude"

[shell_environment_policy.set]
PROJECT_MODE = "development"
```

Direct and nested Code Mode shell calls apply the same policy to pipe and PTY
launches. Each admitted call captures its own environment before asynchronous startup;
later parent-environment changes affect later calls, not an admitted call. Fixed exec
overrides then set no-color/dumb-terminal, UTF-8 locale, cat pagers and the compatible
`CODEX_CI=1` flag, even if excluded/configured differently. Five restricted Codex/OpenAI
launch-context variables cannot be restored by `set`, and plugin metrics output is
removed. This limits accidental propagation, not filesystem access or shell startup
scripts; it is not an OS sandbox. Environment values are not injected into prompts or
checkpoints. MCP has its separate environment policy. Reopened Runtimes load the current
policy. Codex's ordinary cold resume likewise takes this policy from current Config;
its persisted Turn context does not serialize the old policy. In-memory selected
environments and remote shell environments remain separate alignment work.
`experimental_use_profile` is accepted
but has no execution effect, matching its unused execution field in the pinned source.

The host writes compatibility `CODEX_THREAD_ID`, `CODEX_SESSION_ID` and `CODEX_VERSION`
after environment filtering, using **Corki's** actual identities and package version.
They are informational metadata, not a claim that the process is OpenAI Codex or that
a sandbox/approval policy is enforced. Ordinary root Sessions use their Thread ID as
Session ID. The internal memory consolidation task gets an independent Session ID,
matching Codex's fresh internal-start path rather than its parent-linked spawn path.
Session identity is stored atomically with thread creation, restored before tools can
run, and never replaced by new configuration on reopen. Legacy database rows migrate
to their existing Thread ID; their original separate internal identity, if any, cannot
be reconstructed. Corrupt stored identity fails initialization before model/tool calls.
The host `session_id` property is available after identity initialization (including
host terminal prewarming); an optional
`LangGraphRuntime.create(session_id=...)` applies only to a newly created thread.
History/notes tools use this same restored Session ID; window IDs and local storage remain
Thread-scoped even if a host shares a Session ID. No ingestion metadata is sent.
This does not add multi-agent addressing.

Runtime-owned managers resolve durable identity inside their owned startup task before
any physical spawn, including host prewarming before `stream()`. Cancellation or close
during identity resolution waits for ownership to settle and prevents command launch.
Inherited/configured permission-profile and apply-patch rollout markers are removed
while the corresponding host features are unavailable. ProcessManager identity binds
once before any process admission; standalone managers without an owning Runtime do
not inherit an outer Runtime's identity. Identity is not a model-editable tool argument
and is distinct from the terminal `session_id` returned by `exec_command`. Parent-linked
multi-agent APIs and actual permission/patch rollout features remain separate open work.

Nested shell calls return objects with `output`, per-call `wall_time_seconds`, and optional
`chunk_id`, `original_token_count`, `exit_code`/`session_id`; the latter is Corki's string process
ID and can be passed to `write_stdin`. Shell collection keeps a bounded head/tail (default 1 MiB),
with explicit omitted-byte metadata. Both shell tools accept a per-call `max_output_tokens`,
including zero; it does not shrink the process buffer or carry into later polls. Nested output
uses this explicit budget only; without it the complete collected output is returned. Direct model
output uses the smaller of the model policy and the requested/default 10,000-token budget, reserving
room for headers and truncation notices. Log previews use a separate display cap. The durable shell
result contains the source-formatted model text and nested value, not an unlimited raw byte archive.
Process completion is detected independently of pipe EOF. After leader exit, Corki gives output
a bounded 50 ms drain window, retires its owned POSIX process group, joins reader/timeout work,
and closes its subprocess transport. Observation, timeout and shutdown share termination/cleanup
ownership; cancellation waits for that cleanup. PTY reads use event-loop readiness, not blocking
threadpool reads. On macOS, a denied group signal falls back to enumerating that exact group and
rechecking each member's current group before signalling it; this does not enumerate arbitrary
process trees. Windows Job containment and escaped process groups remain unimplemented; signal
failures without a successful delivery are still reported.
Startup is manager-owned even while asynchronous OS creation is handing back its handle:
cancellation/close joins that handoff and retires stale startups before returning. Closing
temporarily rejects new starts; the manager can then be reused. Cancelling just an observation
after handoff retains the stored session, including Runtime Turn interruption. Interruption
still cancels and joins tool observations and CodeMode cells; already-published terminal
processes survive for later Turns. Startup cancelled before handle handoff is still reclaimed.
The host Runtime provides `list_background_terminals()` (immutable initial call ID, process
ID, command and cwd), `terminate_background_terminal(process_id)` and
`clean_background_terminals()`. They operate on owned session IDs, not arbitrary OS PIDs;
listing does not start providers or replay tools. Single termination returns false for
unknown IDs or failed process termination and retains the handle on failure. Explicit
cleanup and `aclose()` reclaim owned processes; a new Runtime does not recover live OS
processes from durable history. These are host APIs, not new model tools or CLI controls.
Reader/timeout failures latch the first error, wake initial and subsequent observations, and
return through tool error handling after cleanup; failed commands are not automatically replayed.
Unix PTYs use nonblocking descriptors for both reads and writes. Reads retry interrupted or
stale readiness; closed-slave EIO is EOF. A single owned writer drains a 128-message queue,
retries partial/interrupted writes and awaits writable readiness under backpressure. Enqueue
is cancellable and shutdown joins the writer before closing its descriptor; PTY input gets
the source's 100 ms reaction window before observation. Unexpected writer errors follow the
same first-failure path. Ordinary pipe commands start with null stdin (immediate EOF),
and non-empty `write_stdin` requests are rejected with guidance to use `tty=true`; exact
Ctrl-C interrupts the owned pipe process group on Unix. PTY input, including Ctrl-C,
goes through the byte writer: raw mode receives the byte, while terminal signal mode
targets the foreground process group. An isolated stdlib-only helper attaches the
controlling terminal before exec; a bounded close-on-exec status pipe reports setup/exec
failure before startup handoff. Cancellation joins failed-start cleanup. No Python
`preexec_fn` runs in the threaded parent. Windows console and remote parity remain unverified.
New shell observations project Unix signal termination onto the pinned Codex backend contract:
plain pipes report `128 + signal`, while default portable-PTY observations report `1`.
Normal exit codes are unchanged. The raw asyncio status stays intact for cleanup; direct text,
display text, nested CodeMode values and persisted results use the projected status. A nonzero
command exit is not a tool dispatch failure. Existing history is not rewritten, and this does
not implement Codex's separate inherited-fd PTY path (which uses the pipe-style signal code).
Both `exec_command` and `write_stdin` permit parallel tool dispatch, including streaming
calls and CodeMode `Promise.all`. Independent commands/terminals can overlap; exclusive
tools still form barriers, and interactions with one terminal remain serialized. Concurrent
input calls need not acquire that terminal's lock in source order: await a write before
issuing the next when input order matters. Results retain their call identities and model
call order even when execution overlaps. No failed command is automatically replayed.
Shell yield arguments accept unsigned 64-bit integers and are clamped rather than rejected
outside their effective wait interval. Initial exec waits default to 10000 ms (the explicit
`command_yield_seconds` host override is retained), clamped to 250–30000 ms; Windows initial
waits have a 10000 ms floor. `write_stdin` defaults independently to 250 ms: non-empty writes
use 250–30000 ms, empty polls use 5000 ms through `[tools].background_terminal_max_timeout`
(milliseconds, default 300000, configured values below 5000 are floored). These are maximum
observation windows, not minimum command durations or process-kill timeouts. Corki's existing
resumable commands have no default lifetime cap. An explicitly configured positive
`command_timeout_seconds` remains a host override; existing files specifying 120 still use
that limit, while newly generated config leaves it disabled. Sessions survive normal Turn
completion and interruption until exit, explicit cleanup/close or retention pruning. The manager has a
soft 64-session cap: protect the 8 most recently used, prefer older exited/failed sessions,
then older live sessions. Active stdin interactions are serialized and protected; a locked
exited session can temporarily permit overflow instead of evicting healthy work. Pruned
processes are cleaned up, not replayed. Managed-policy one-shot execution remains unimplemented.
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
`code_mode_disable_fallback = true`; `code_mode_only` fails closed. Fail-closed modes
retain `exec`/`wait` and report the missing engine as a tool error Observation, without
starting a cell or silently exposing hidden direct tools; they do not abort Runtime startup.
The first affected Turn emits an actionable warning, once per Runtime session. Direct-only
Turns and temporary candidate plans do not consume that warning. The session keeps yielded
cells across mode changes: a direct Turn pauses new nested calls, while a later Code Mode
Turn can resume dispatch through its newly selected worker. Already-admitted calls retain
their original execution ownership. Engine processes/cells themselves do not survive a restart.

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
indefinitely by default, with delays of 5, 10, 20, 40, then 60 seconds. All waits can be cancelled;
queued steering does not reset the retry budget or replace the current request. To disable
ongoing network reconnection while retaining bounded retries:

```toml
[provider]
unbounded_connection_retries = false
stream_max_retries = 5
request_max_retries = 4
```

`max_retries` remains a legacy alias for `stream_max_retries`; the explicit new key takes precedence.
Each count is capped at 100. Set both counts to zero and disable unbounded connection retries to
disable all retries. Setting just the stream count to zero does not disable the HTTP request tier.
Standalone adapter requests (including memory) retain bounded retries. Provider names do not
change the main sampling retry policy. Corki currently uses HTTP transport only;
this does not implement WebSocket-to-HTTPS fallback or native Bedrock transport support.

Responses SSE failures now distinguish quota, policy rejection, invalid requests, overload and
rate limits by error code. During ordinary sampling, non-retryable rejections stop the turn; rate-limit messages can supply
a seconds/milliseconds delay. Error events are retained until stream EOF, while later complete
items are preserved; only a valid completion ends the response successfully. HTTP errors are mapped
after the request tier is exhausted. HTTP 400 is an invalid request, not evidence of input overflow
based on message text. HTTP 401/403 with a static key follows bounded unexpected-status retries;
no OAuth refresh or automatic account switching is added. Missing local credentials fail immediately.

## Local data

Third-party MCP OAuth is independent of model API-key authentication. Saved credentials
in File or Keyring storage, bound to the configured
MCP server name, URL and issuer can now refresh before initialization and subsequent
RPCs. Refresh serializes per credential, rereads the pinned file, and atomically
saves rotating tokens before using them. Auto checks Keyring first at client startup;
after selecting a source, rereads and refresh writes stay with it even if it later fails.
The Keyring service is Corki-specific, not a Codex/OpenAI account store.
It never follows token-endpoint redirects,
forwards model keys or MCP headers to that endpoint, or retries a business tool to
recover authentication. Cancellation joins an already-started refresh transaction.
Authentication preparation has separate bounded waits and does not consume the
HTTP MCP handshake or RPC timeout; the actual handshake/RPC retains its timeout.
Public-client browser authorization now supports a configured client ID or dynamic
registration and a bound loopback callback. Skill dependency installation uses this
flow; standalone login for a configured local HTTP server is also available:

```bash
corki mcp login SERVER_NAME
corki mcp login SERVER_NAME --scopes read,write
```

The top-level `mcp_oauth_credentials_store` selects `auto` (default), `keyring`, or `file`.
Auto first tries Keyring and falls back to File if the login write fails; explicit Keyring
reports failure instead of downgrading. Successful Keyring login best-effort removes the
matching old File entry, preserving other services. Cleanup failure warns without repeating
the token exchange. Standalone login does not create a model session or require
a model API key, and prints success only after saving credentials. If automatic browser
launch fails, open the printed URL manually. Explicit scopes override server-configured
scopes; otherwise configured or discovered scopes are used. Plugin-provided
standalone server discovery, header-helper login and the remaining callback/scope variants
are unfinished; these limitations are not a complete OAuth parity claim. There is no
Codex/OpenAI account integration.

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
    ├── corki.db.thread-writer-locks/ # live Runtime ownership, not conversation data
    └── checkpoints.db
```

For tests and isolated installations, set `CORKI_HOME` to override that path.

A persistent Runtime acquires an OS-backed writer lock before initializing its canonical Thread and
retains it between Turns until shutdown finishes. Another Runtime using the same resolved
database path and Thread ID fails with `ThreadWriterConflict` (`RuntimeError`, "active writer")
before admitting input or sampling the model. Different Threads can remain active concurrently.
Close the current owner before reopening that Thread. Failed initialization releases ownership
and can be retried; cancelling a close waiter does not abandon the shared teardown.

Process exit releases the kernel lock. Recovery still treats claimed tools without a durable
result as unknown outcomes and does not automatically repeat their side effects. Opening a
repository does not mark other live tools interrupted. Lock files are cleaned under a separate
coordination lock; do not delete them while any Runtime is active. This is cooperative local
Runtime ownership, not a security sandbox, distributed lease, or protection for arbitrary direct
database writers/hard-link aliases. Cross-process and crash recovery are tested on macOS;
the Windows locking branch has not been executed in this workspace.

Hosts can call `await runtime.archive()` after a Thread has materialized canonical history.
It closes that Runtime and moves the Thread into a transactional archived view, preserving
conversation rows, checkpoints, tool outcomes, model settings, source identity and memory mode.
The shutdown wait is bounded to ten seconds; a timeout never grants permission to mutate a
Thread whose writer is still alive. An admitted archive joins its work before reporting caller
cancellation. Failed archive metadata writes roll back, and a failed archive leaves the closed
Runtime closed. Empty/unmaterialized Threads are rejected before shutdown.

Default resume and latest-Thread lookup exclude archived Threads. `await runtime.unarchive()`
restores visibility and refreshes the source's idle timestamp; it does not reopen an already
closed Runtime. Create a new Runtime with the same `thread_id` to continue. Advanced hosts can
explicitly set `include_archived=True` when creating a Runtime to read/write archived history
without changing its archive state, matching the separate low-level core path. Archive and
unarchive reject another live writer. Custom session backends can inject an `archive_store`
implementing `corki.sessions.archive.ThreadArchiveStore`; it must share their writer exclusion.
`SQLiteThreadArchiveStore` also exposes bounded active/archived `list_threads` views.

Archive prevents new background memory extraction (before the source scan cap), but does not
forget existing derived memories or notes. Those remain subject to their existing eligibility
and retention rules. This implementation covers individual local Threads through host APIs;
it does not provide Codex's spawned-subtree lifecycle, app-server notifications, or a CLI archive
command. Ephemeral execution and parent execution-permission enforcement are separate concerns.

For a private in-process session, hosts can use `LangGraphRuntime.create(..., ephemeral=True)`.
Canonical Thread/Turn/history, tool claims, model results and graph checkpoints remain in RAM
from construction through shutdown: no session database, checkpoint file or Thread writer lock
is created. The same Runtime still supports multi-Turn context, deferred tool search/execution,
compaction and recovery of its own live in-memory work. Ordinary sessions remain persistent.
The internal memory consolidation worker uses this path while retaining its separate working
copy for artifact edits. Deleting that working copy is not what provides non-persistence.

Ephemeral mode skips background memory generation and has no persistent source metadata or
archive operations. Existing memory summary/read tools remain controlled by their usual feature
settings, with optional database usage feedback absent. It owns private state and rejects injected
session/archive/memory repositories. A newly created ephemeral Runtime does not import an old
database just because the host supplied an existing Thread ID; the volatile session cannot be
cold-resumed after process exit. Durable-source fork/resume import and a CLI ephemeral flag are
not implemented by this host API. No general sandboxing, secure memory erasure or remote deletion
is implied: explicit tool outputs, memory notes, separately enabled history/notes services,
plugins, configuration and provider-side retention have their own contracts.

With long-term memory and its dedicated tools enabled, `memories::add_ad_hoc_note` records an
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

Output events use an unbounded queue by default (`[runtime] event_queue_size = 0`),
so a temporarily paused UI does not stop the owned Turn from making progress.
Embedding hosts should keep draining the stream or close it when abandoning a Turn;
unconsumed events otherwise accumulate in memory. A positive `event_queue_size`
explicitly enables bounded backpressure. Existing positive settings are preserved,
including `256` in previously generated configuration files. Cancelling or closing
still joins the owned Turn; terminal delivery does not require a free queue slot.

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
generate = true             # initial memory-source eligibility of new threads
background_enabled = true  # Corki host switch: allow automatic background passes
use = true                  # inject the compact routing index
dedicated_tools = false     # optional memories namespace tools; also requires use=true
disable_on_external_context = false
```

After each newly admitted input Turn, Corki leases a bounded number of eligible, idle threads,
extracts durable facts with a separate model request, and performs singleton global consolidation
into `~/.corki/memories`. Initialization alone, manual compaction, pending-Turn recovery and
steering an existing Turn do not schedule a new pass. Passes may overlap; database claims
prevent duplicate extraction and simultaneous global consolidation. Runtime shutdown cancels
and joins every accepted pass before closing its dependencies. Waiting observes the currently
accepted passes and returns the latest report; cancelling a waiter does not cancel those jobs.

Configuration migration: `generate=false` now matches Codex's source-eligibility semantics;
it does not stop maintenance or processing of other eligible threads. If you previously used
it to pause background work, also set `background_enabled=false`. This extra Corki switch
only pauses automatic startup; it does not change source modes, recall, or explicitly invoked
pipeline work. User configuration files are not rewritten. Background checks still use the
configured model and may make requests even when the current thread's source mode is disabled.

Normal turns receive only `memory_summary.md`; the model progressively searches `MEMORY.md` and
rollout summaries when relevant. This deliberately follows Codex's layered Markdown retrieval
instead of introducing an embedding/vector store. Memory generation failures are isolated from the
interactive turn, and cited source thread IDs update usage ranking.
Searching or reading a candidate does not count as adopting it; usage feedback comes from
completed assistant citations. Previously stored usage counts are not retroactively rewritten.

Memory read instructions now retain the complete pinned quick-pass, drift/verification-cost,
citation and explicit-update-only policy. Only the routing summary is capped (2500 tokens by
default); the policy body is not shortened to fit that cap. Markdown summaries route to source
thread IDs, and the prompt identifies the actual SQLite session archive for bounded read-only
queries through ordinary shell tools. A custom non-SQLite repository receives no guessed file
locator. This does not create a new history-read tool or claim that the archive is JSONL.
`use=false` suppresses both current read instructions and dedicated memory tool registration;
generation remains independently controlled. It is not a filesystem access restriction and
does not erase prior messages or memory files. Reopening the same thread appends context
removal instead of rewriting its old memory guidance. The complete phase-two policy is
described below; full consolidation permission/configuration equivalence remains open.

Dedicated tools use the canonical names `memories::add_ad_hoc_note`, `memories::list`,
`memories::read` and `memories::search`. Capable native Responses requests use the `memories`
namespace; compatible Responses, Chat and Lite use the existing bounded hashed aliases.
Code Mode calls use `tools.memories__read(...)` and equivalent leaf names. List/search take
string cursors and `max_results` (zero clamps to one); search takes a tagged `match_mode`
object such as `{"type":"all_within_lines","line_count":2}`. Read accepts only `path`,
`line_offset` and `max_lines`, with a fixed 20,000-token backend budget. Optional nulls use
the native defaults. Memory handlers own typed argument parsing; other tools retain their
existing schema validation. All four tools are exclusive, including nested concurrent calls.

Direct results are JSON text; Code Mode receives JSON objects. List entries use `entry_type`,
pagination returns string cursors, and a successful note write returns `{}`. Optional internal
`ToolSpec.output_schema` metadata describes nested results but is never an HTTP tool field.
This is not a clone of Codex's TypeScript declaration renderer. Removed flat `memory_*` names
are not automatically rebound: old completed history retains its original identity, and an
unavailable pending handler produces an error instead of executing the new tool under an old ID.
Parsed tool arguments containing integers outside MessagePack's range are checkpointed as exact
JSON-backed mappings, without pickle; raw arguments and call identity remain unchanged.

Dedicated memory tools distinguish invalid requests (including missing paths and duplicate
notes) from storage failures. Invalid requests return model-visible errors; metadata,
directory enumeration, read or write I/O failures stop a direct-call Turn without automatic
retry. Invalid UTF-8 fails a dedicated read, while search skips binary files. A directory or
entry disappearing during enumeration is skipped; a file disappearing after validation at
read time is a storage failure. Nested Code Mode calls preserve their separate catchable-error
boundary: invalid memory requests reject the nested promise rather than resolving an error
value. Note creation never overwrites an existing note; a write failure may leave partial
bytes, and reopening a terminal failed Turn does not replay that uncertain write. These rules
do not change the isolated background-generation failure policy or add filesystem sandboxing.

Memory extraction uses low reasoning effort; consolidation uses medium effort.
Selection order is `[memories].extraction_model` /
`consolidation_model`, then `[provider].memory_extraction_model` / `memory_consolidation_model`,
then the configured main model. No fixed vendor model is selected for background jobs.
Providers can set their own model IDs at either level. An unavailable explicitly selected memory
model fails the background job; it does not silently fall back to the main model.
The default shared model must also fit consolidation instructions and tool definitions.
If its window is too small, consolidation fails in the background without changing models
or stopping the main conversation; configure a suitable consolidation model explicitly.

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

Phase-one extraction uses the complete pinned Codex generation instructions, including
the minimum-signal/no-op rule, evidence priority, per-task outcome grading, task-first
reference summaries and raw-memory retrieval structure. These are model instructions,
not a host guarantee that inferred preferences are correct. The input names the actual
SQLite source thread and cwd rather than inventing a JSONL rollout path. The 70% budget
limits transcript content, not the complete request: full instructions, framing and
schema still occupy context. A provider rejection for a too-small model is recorded as
an isolated extraction failure; Corki does not shorten the instructions to force a fit.
This does not imply identical phase-two or memory read-path prompt bodies.

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
Once claimed, extraction completion checks the running job's ownership, not the thread's
current mode or updated timestamp. A thread disabled/polluted or edited during sampling can
therefore retain that already-claimed extraction in the database. Disabled/polluted sources
remain excluded from future claims and consolidation selection; this is not erasure of raw data.
Completion cannot overwrite a newer stored output; equal versions may refresh it. Timestamp
comparison preserves sub-millisecond ordering and normalizes legacy naive UTC and offset strings.
Job success, output changes and enqueue are one transaction; failures roll them back together.
Cancellation joins a started database write before returning, without promising rollback of a
write that already completed. Reset or takeover still invalidates old ownership tokens.
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
authority. Live native search/web events and call-id-less hosted notifications are rejected as
protocol errors; the model adapter does not create new `HostedToolItem` records from them.
Old `HostedToolItem` archives remain readable and keep their external-context classification.
Both Responses and Chat replay them as labeled user-role external data, never native wire items.
Raw history and history search/read preserve the old records; this compatibility does not enable
provider-hosted tool execution.
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
marker. MCP validates remote content before capability filtering or output conversion: only `text`,
`image`, `audio`, `resource` and `resource_link` blocks are accepted, with their required typed fields
(including media `mimeType`). An invalid block rejects the entire result as a tool error observation;
unsupported media cannot hide a malformed block. Unknown fields are removed from typed blocks,
while protocol metadata extensions are retained. Embedded resources select valid text before blob.
Known typed fields reject duplicates, including duplicates whose first value is null. Duplicate
fields invalidate only the resource candidate that reads them: repeated `text` may still select a
valid `blob`, while repeated `blob` does not invalidate valid text. Unknown fields and JSON-valued
metadata/structured-content keys keep their separate last-key-wins behavior.
Content audience roles and icon themes accept the pinned RMCP unit-enum forms:
a variant string or a single-key object with a null value. Public results always
use the canonical string; duplicate keys, unknown variants and non-null payloads
remain invalid. This does not broaden content `type` tags or string constants.
Raw tool responses also check the audited earlier RMCP result candidates: a valid list,
prompt, initialization/discovery, completion, subscription, elicitation or task
result cannot masquerade as a successful tool result just by including `content`.
An invalid earlier candidate falls through; merely having a `tools` or `messages` key is
not a rejection condition. This classification is not repeated on already-projected
public results. Task-result matching validates every present payload before checking which
payload its status requires; missing `resultType` defaults, but explicit null does not.
Nested sampling/elicitation/roots requests are classified, never executed by this check.
Audited ordinary structs also accept complete positional arrays in their RMCP declaration
order, including nested candidate fields, content annotations and icons. The tool-result
helper uses `resultType`, `content`, `structuredContent`, `isError`, `_meta`, in that order;
the same candidate precedence and typed checks apply before it becomes a public object.
Missing optional slots and extra slots are invalid. This does not turn metadata maps,
internally tagged content or flattened task types into positional structs. Other RPC
entry points and other typed numeric projections remain under audit.
Content `annotations.priority` now follows the pinned RMCP f32 conversion: integers
are rounded directly to binary32 (without an intermediate f64 rounding), then emitted
using its shortest decimal representation. For example, `16777217` becomes `16777216.0`.
Projected values retain that spelling through validation and durable recovery; Code Mode
subsequently applies JavaScript Number formatting. This is not a new 0-to-1 range check.
Raw fractional/exponent tokens still follow the pinned buffered-deserializer rejection
rules; exact public number tokens are not mistaken for fresh remote typed input.
JSON-valued MCP result fields retain exact numeric literals through model text, events,
the execution ledger and the Code Mode transport, including values outside Python's float
range. Code Mode then applies JavaScript's normal Number precision/range when parsing the
result; this can round large integers or produce Infinity. Numbers are not quoted to avoid
that conversion. Metadata roots remain maps, distinct from their JSON-valued children;
serde private Number/RawValue markers are interpreted only at raw JSON Value boundaries,
not reinterpreted when durable public results are restored. Logging notification data also
uses the JSON Value boundary, so numeric log messages cannot abort a tool response reader.
This structural validation does not yet reproduce every RMCP numeric-deserialization boundary.
For valid results, non-null `structuredContent` takes precedence unless content includes a typed
encrypted block. Otherwise ordered text/image/audio/opaque blocks are preserved, and valid resource
blocks become JSON text. MCP text metadata
`codex/encryptedContent: true` creates opaque output rather than plaintext; `codex/imageDetail`
selects the requested image detail, followed by normal model-capability and image-preparation checks.
For example, default detail-based preparation still rejects `low`; conversion alone does not imply
that every media representation is supported. Logs use text-only projection; recognized media and
encrypted blocks are not serialized into log text. Resource blocks retain their public JSON fields,
so this is not a general-purpose secret filter for arbitrary resource contents.

Direct MCP outputs include `Wall time: … seconds` and `Output:` and use the effective policy's 1.2
serialization allowance. The corresponding exact token fallback travels with the durable result;
the nested public result and independent log preview are not first truncated to this model budget.
Configure a positive per-tool override in `[mcp.servers.docs.tools.read]` with `output_token_limit = 80`.
It may override the model default in either direction. Execution admission captures this setting from
the exact selected connection, including after refresh; in-flight calls retain their captured policy.
Remote tool/result metadata cannot set the budget. Old fixed MCP character limits are no longer used.
At `[mcp.servers.docs]`, `omit_tools_from = ["code_mode", "deferred", "direct"]`
selects which model-facing surfaces to remove from that server's tools. Any subset
is accepted; omitted configuration preserves a lower config layer's value and an
explicit `[]` clears it. This is not a raw tool allow/deny filter and does not affect
unrelated host tools sharing the server's canonical namespace. Remote tool metadata
cannot set it. The resolved mask follows canonical names, including non-prefixed MCP
names, and feeds registry, search, Code Mode and Lite inventory together.

After applying the mask, enabled search prefers a remaining deferred surface; otherwise
a remaining direct surface is used. In `code_mode_only`, omitting Code Mode also removes
deferral, allowing a remaining direct surface to stay visible as DirectModelOnly.
Omitting direct and deferred leaves CodeModeOnly; omitting all three leaves Hidden.
A namespace direct-only override removes nested/deferred surfaces before selection
and cannot restore a server's omitted direct surface. Namespace exclusion only filters
the nested publication and does not perform this server-mask selection.

`request_mcp_reconcile` applies mask changes without relisting/reconnecting a healthy
transport, replacing the registry and search generation at the normal boundary.
Old Step views remain isolated; cold recovery validates saved effective definitions
before execution. Masks control model exposure, not permission revocation: already
admitted calls and existing cells retain the documented execution/ledger semantics.

Optional `enabled_tools` and `disabled_tools` arrays filter exact
raw remote tool names before model-name normalization and at call admission. An omitted
allowlist permits all tools, `enabled_tools = []` permits none, and deny entries take
precedence. These are not canonical names or provider aliases. Explicit MCP refresh
applies changed lists to subsequent admissions; already admitted calls retain their
connection. Tool filtering does not disable the server's resources or prompts.

MCP user review can be enabled with `[mcp] approval_policy = "on-request"` (or
`CorkiSettings(mcp_approval_policy="on-request")`). Set a server's
`default_tools_approval_mode` and/or `[mcp.servers.docs.tools.write] approval_mode`
to `auto`, `prompt`, `writes`, or `approve`; a per-tool override wins. `auto` uses
the native read-only/destructive/open-world hint ordering; `prompt` asks every time,
`writes` asks unless read-only, and `approve` skips review. The existing default is
`never`: like Codex's Never + disabled sandbox, this auto-approves **all** modes,
including `prompt`. It does not mean “deny all.” This setting controls MCP tool calls
only; it is not local shell approval, OS sandbox enforcement, or Guardian review.

The Runtime/CLI host channel receives `ElicitationRequest.kind == "tool_approval"`
before memory pollution or the remote call. Decline, cancel, missing host and host
delivery failure produce failed MCP observations without executing the tool. Only
`auto` offers session remembering through the legacy `remember` boolean and the
optional `persist` field (`once`, `session`, `always`). Hosts may instead return
`meta={"persist": "session"}` or `meta={"persist": "always"}`; this metadata takes
precedence. The `tool_call_mcp_elicitation` feature defaults to true and enables the
long-term choice. Disabling it or using a selected-plugin server limits remembering
to the current Runtime. `prompt`/`writes` ignore remember requests.

Long-term approval writes the exact raw tool's `approval_mode = "approve"`, preserving
unrelated TOML and comments. All servers, including one named `codex_apps`, use
the highest admitted project layer declaring that server, then its user declaration,
then the active plugin's user-config override. Writes happen only after the prepared
catalog revision is checked. Failed writes retain session consent; a successful write
reloads user-backed plugin approval policy while retaining project snapshots.
The Runtime Apps policy update API, manager evaluator, and standalone Apps policy
configuration/evaluation modules have been removed;
the Apps-specific approval destination has also been removed. Approval reload ignores
the legacy Apps tree without changing its text; malformed TOML and invalid ordinary
MCP/plugin settings still fail validation. Startup settings no longer expose Apps
policy or enablement fields; managed loading discards the legacy Apps tree before
ordinary policy validation. Old Apps grants are not migrated into ordinary approvals.
Configuration loaded by `for_directory`
retains its layer provenance, including when other settings are replaced. Plugin
approval overrides are recomputed from the captured declarations on each reload:
installed plugins accept overrides, while selected-plugin overrides may only restrict
the declared modes. Explicit catalog/reconcile replacements supply a new declaration
baseline; existing prepared calls retain their old policy.
Ordinary servers retain their materialized policy during this file-only reload; the
saved approval is loaded by a cold Runtime or an explicit materialized host update.
The current Runtime also remembers the accepted ordinary-server session consent.
An already-started write is joined on cancellation before releasing
catalog authority; cancellation does not undo an approved configuration change or replay
the business call. Broader live configuration reload, managed approval-policy routing,
permission hooks and automatic reviewer execution remain unfinished.
A prepared call retains its exact connection/settings across host waiting and
configuration replacement; later calls use the newly admitted policy. A successful
same-connection Apps catalog refresh invalidates older prepared revisions. Human
review does not block that refresh; accepted grants and RPCs require a current revision.

Default CLI and Runtime startup load managed MCP requirements from
`/etc/corki/requirements.toml` on Unix, or the Windows ProgramData known folder's
`Corki/requirements.toml`. The Windows lookup does not trust the `ProgramData`
environment variable; a failed OS lookup warns and falls back to `C:/ProgramData`.
Only a missing file means no system policy. Read/UTF-8/TOML/shape/effective-regex
errors abort startup before user directories, databases, plugins or clients are
allocated. Supported shell approval, sandbox, permission-profile and execution-rule
requirements are routed to native execution admission; unsupported global domains
still fail explicitly. Managed `allowed_approval_policies` requires a rebuilt native
compiler: configuration falls back to the required default, with a startup warning
for an explicit disallowed choice. Tools and model context share the admitted value;
command-time revalidation cannot silently change it. Background memory workers retain
their native Never-only approval constraint. See [permission backend](native/sandbox/README.md).

Local platform-wheel builds include a receipt-verified native permission compiler
(`CORKI_BUILD_SANDBOX_COMPILER` at build time, or an installed source bundle for a
direct wheel build). CLI configuration and ordinary `CorkiSettings(...)` construction
now both select native defaults: unknown project trust is read-only; explicitly
trusted/untrusted local projects use the native workspace profile, with their native
approval policy and managed restrictions applied. The effective policy is published
before model sampling. Explicit profile/approval configuration can omit the compiler
path, and an explicit host compiler override remains supported.

SDK `execution_permissions=None` is an explicit compatibility opt-out placing
execution under the embedding host's control; it is no longer the default. It is
not Codex's native `danger-full-access` profile and cannot satisfy managed execution
requirements. Use an explicit native disabled/full-access profile when command-rule
and approval enforcement must remain active. No TOML omission or empty table requests
the SDK compatibility opt-out. Cross-platform proof, signing, native redistribution
notices and public release remain open; see the backend README for the exact boundary.

The native backend now protects Corki's `.corki` metadata alongside `.codex`, `.git`
and `.agents`, using an audited additive patch to the pinned build export. Default
workspace commands and the patch helper cannot create or rewrite that metadata.
Native explicit overrides still apply, including independent grants to an external
symlink target; this is not an unconditional deny. Older explicit compilers must be
rebuilt and are rejected before admission. Real OS verification is macOS-only; see
the backend README for symlink, build provenance and remaining platform limitations.
User config, `CORKI_HOME`, workspace files and model arguments cannot redirect this
authority path. Corki does not read another application's `/etc/codex` policy.

The CLI captures the policy once and passes it to Runtime. Embedders can use
`load_mcp_requirements(system_path=absolute_path, layers=(...))` from
`corki.config.managed_mcp` and pass the returned `MCPRequirementsSnapshot` as
`mcp_requirements=`. Additional `MCPRequirementsLayer(source, contents)` TOML
fragments are trusted host input in low-to-high priority order, after the system
file. Every layer is structurally validated before merge; tables extend recursively
and scalars/arrays replace. A higher empty table does not erase lower rules. Only
the final effective regex patterns are compiled, so discarded invalid expressions
do not fail startup, but malformed individual layer shapes always do.
`runtime.mcp_requirements_snapshot` exposes the immutable policy and per-top-level
contributing sources in high-to-low order. Later disk changes do not alter an
existing Runtime's authority; live policy reload is not implemented.

An embedding host can instead supply a complete policy, replacing automatic system
loading, with
`LangGraphRuntime.create(..., mcp_requirements={"mcp_servers": {"docs":
{"identity": {"url": "https://example.test/mcp"}}}})`. The host may also pass an
immutable `MCPRequirements` from `corki.config.mcp_requirements`. This is separate
from editable server settings and tool approval: it is validated before Runtime
resources are allocated and checked before client/header-helper construction on
every startup, reconciliation and forced refresh. Denied servers publish no tools,
resources or prompts. Subsequent calls through previously exposed tools receive
an error; already admitted calls retain their exact original connection.

This preserves Codex's legacy exact string `identity.command`/`identity.url`
branch. Legacy command matching intentionally ignores arguments; URL matching authorizes
the remaining HTTP configuration without normalizing the URL. An omitted
`mcp_requirements` argument loads system policy; no loaded MCP restrictions means
unrestricted. An empty `mcp_servers` table denies config and plugin servers.
A nonempty global table restricts ordinary servers, not plugin servers. Plugin rules
use `{"plugins": {"package-name": {"mcp_servers": {"raw-server-name":
{"identity": {"command": "server"}}}}}}`: once any package specifies an MCP table,
unlisted packages/servers are denied. Package and raw server identities come from
the plugin loader, not route-name splitting or remote metadata. Caller mutations
or user server reconciliation cannot widen the captured host requirement.
Typed command rules use `identity.command = {executable = "server", args = [...]}`;
the executable, argument count and each argument position must match. Each argument,
or `identity.url`, accepts `{match = "exact", value = "..."}`, `{match = "prefix",
value = "..."}` or `{match = "regex", expression = "..."}`. Typed forms reject unknown
fields; legacy string identities retain their original parsing precedence and ignored
sibling behavior. Regular expressions match the entire value, including when a later
alternative is needed. Invalid expressions and invalid full-value wrappers fail before
Runtime allocates clients, plugins or databases.

Regex evaluation uses the exact pinned `regex-lite 0.1.8` engine in a bundled no-import
WebAssembly module, not Python `re`: ASCII classes/case folding, codepoint literals,
native flags and non-backtracking matching are preserved. Normal installs include the
module and Wasmtime dependency; they do not require Rust. Reproducible wrapper source,
lockfile and build instructions are in `native/mcp_regex`. Each evaluation closes its
private guest store, with 256 MiB memory and 100 million instruction-fuel host ceilings.
A ceiling/engine failure rejects configuration or denies execution-time matching; it
never falls back to unrestricted access. These ceilings may reject exceptionally large
work that native Codex would eventually complete. This isolates regex computation only,
not MCP servers or tool side effects.

Runtime now retains MCP declarations in a source-aware catalog. Discovered plugins use
their raw server names, not `package__server`; an earlier package name wins within the
discovered tier, and config wins over plugins. Only winning, enabled servers construct
transports. Disabled winners remain present and veto later host overlays, except for
disabled thread-selected plugin registrations. A disabled losing declaration does not
itself disable the winner. Ordinary Python plugin tools keep their `plugin__...` names.
Old package-prefixed MCP routes are not automatically aliased to a new winner; update
call sites to the raw server name. Existing history stays intact and unavailable old
routes produce errors instead of being rewritten.

Ordinary host plugin MCP files accept either a bare server map or an `mcpServers` wrapper.
An invalid server is warned about without discarding valid siblings or other plugin capabilities;
a malformed optional MCP file disables that MCP contribution, not the whole plugin.
Missing stdio cwd inherits the Runtime directory; an explicit relative cwd is joined lexically
to the plugin root (no shell `~` expansion). HTTP helpers use the Runtime directory as well.
To run a stdio server from its package root, configure `cwd = "."` explicitly.
Native `mcpServers` file paths must start with `./`, name a file and contain no parent component;
invalid path declarations warn and fall back to `.mcp.json`. Ordinary plugin files may be symlinks,
matching the trusted-host source contract; this is not Agent Plugin resolved-path containment.
Plugin `type` annotations do not override command/URL transport inference; unknown types warn.
Agent Plugin packages use a separate path: root `plugin.json` must declare the supported
`https://agent-plugins.org/schemas/1.0.0/plugin.schema.json` schema. It takes precedence over
legacy manifests; unsupported Agent schema versions and symlink/nonfile roots cannot fall back.
The fixed `mcp.json` has its own required versioned schema, strict transport fields and
regular-file checks. Invalid MCP contributions do not disable valid package capabilities.
Agent stdio defaults to the canonical package root, injects `PLUGIN_ROOT` and `PLUGIN_DATA`
(`.plugin-data` beneath that root for directory-loaded plugins), and contains command/cwd paths.
Arguments and environment values receive single-pass path substitutions but remain opaque.
Directory discovery does not create the data directory. Agent HTTP rejects client-owned
headers after validating every configured header, and requires HTTPS except for loopback.
The `.codex-plugin/plugin.json` overlay can only append local `env_vars` to matching existing
stdio declarations; it cannot add servers, replace commands or supply HTTP credentials.
Ordinary legacy fallback also recognizes `.claude-plugin/plugin.json` and `.cursor-plugin/plugin.json`.
This does not implement marketplace installation, executor plugin placement or OAuth flows;
remaining parser compatibility checks and verification status are recorded in the harness audit.
Agent endpoint and redirect URLs now use the exact pinned `url 2.5.8` parser in a bundled
no-import Wasm module. Raw URL spelling remains catalog/policy identity; outgoing Agent
requests use canonical URLs, including native numeric-IP, IDNA and encoded path handling.
Normal installs do not require Rust. Per-call 256 MiB/100-million-fuel ceilings fail explicitly
without parser fallback. Ordinary MCP URL parsing and complete overlay/hook metadata parity
remain separate review items; this does not claim all plugin behavior is identical.

Embedding hosts can inspect `runtime.mcp_catalog`, pass `mcp_catalog=MCPCatalog(...)`
at creation, or call `runtime.request_mcp_catalog(catalog)` to replace declarations at
the next prepare/call admission boundary. Types live in `corki.mcp.catalog`.
`MCPRegistration` carries settings plus `MCPCatalogSource`; `MCPRemoval` is an ordered
compatibility/extension action. Precedence is discovered plugin, selected plugin,
config, compatibility, then ordered extension. Equal priorities use insertion order;
earlier plugin selection orders win. `catalog.extend(...)` preserves materialized
disabled-name vetoes. Returned catalog/settings values are detached from Runtime state.
Transport-only `request_mcp_reconcile` retains known source attribution; it is not a
new declaration or a way to change a plugin into a config-owned server.

Compatibility/extension registrations are trusted host contributions, not ordinary
user config or remote metadata; as in Codex's host-only projection, controller MCP
allowlists do not directly filter those new host-owned declarations. An existing
disabled base winner still vetoes a same-name overlay. Do not expose this host API to
model arguments or untrusted plugin code as an authority grant. Cloud policy retrieval,
macOS managed preferences, legacy global-policy conversion, live authority replacement,
automatic selected-root discovery and remote
environment attachment/executor enforcement remain unfinished alignment work.

Hosts applying configuration changes can call `runtime.request_mcp_reconcile(servers,
server_metadata=...)`. It publishes new policy views through normal preparation/call
admission while reusing healthy sessions and their initialized, unfiltered catalogs.
Transport changes or dead clients require new sessions. Each admitted call retains
its original timeout, output budget and memory-pollution policy; shared clients are
closed only after all owning views release them. `request_mcp_refresh()` and CLI
`/mcp refresh` remain explicit forced reconnects, including a fresh tools/list request.
Reconciliation is an in-process host API, not a configuration-file watcher or a claim
of OAuth, remote-environment or lazy-startup parity. Reopening a Runtime still needs
the desired settings; this does not persist configuration changes to disk.
Local stdio MCP processes inherit a selected default environment, not all parent variables.
MCP declarations retain an exact `environment_id`; omitted or null means `local`.
The default Runtime has an implicit local binding. Without an explicit host binding,
unknown IDs (including empty strings or `local ` with trailing whitespace) are isolated as server startup failures
before client creation or reuse, never run against local processes/credentials, and
do not cause fallback to a lower-priority same-name plugin. Local low-level stdio/HTTP
client constructors also reject unknown bindings. Other healthy servers remain usable.
Environment identity participates in connection reuse; a previously admitted call keeps
its original lease, while later calls must pass current admission. Ordinary nonlocal
configuration does not expand `~` using the controller home or receive the Runtime's
default local cwd. Plugin-source path normalization is a separate contract.
Cold reopening uses current declarations and current system requirements, not execution
permission inferred from old discovered schemas. Native history retains old definitions;
compatible request projection removes stale definitions without changing stored records.
Embedding hosts can supply `mcp_runtime_context=MCPRuntimeContext((binding, ...))` to
`LangGraphRuntime.create`, using `MCPHTTPEnvironment(environment_id, transport,
requirements=MCPRequirements(...))` from `corki.mcp.runtime_environment`. The concrete
`httpx.AsyncBaseTransport` is the host-selected HTTP capability, not a factory inferred
from editable configuration. The host owns and closes that shared carrier after all
borrowing Runtimes finish; MCP closes its responses and sessions, including recovered
sessions, without closing the carrier. Owner requirements apply before catalog winner
resolution to config, plugin and host overlay declarations, in addition to controller
requirements. `request_mcp_runtime_context(context)` queues new host handles for the next
preparation/call admission; a different binding object forces a new physical session even
when its ID and carrier are unchanged. An admitted call retains its exact old binding.
Reopening a Runtime requires the host to provide the bindings again; history grants none.

For a Codex-compatible executor WebSocket endpoint, the host can call
`await ExecutorHttpTransport.connect(endpoint, headers=host_headers)` from
`corki.mcp.executor_http`, then put the resulting carrier in `MCPHTTPEnvironment`.
This performs the executor `initialize`/`initialized` handshake and forwards HTTP through
`http/request`, with streamed `http/request/bodyDelta` responses. It does not send those
HTTP requests from the controller. Stream IDs are unique, ordered frames are validated,
and queues are bounded to 256 frames per stream, 1 MiB per decoded frame and 16 MiB shared
queued body bytes. Overflow and disconnect report errors rather than successful truncated
EOF; a slow stream does not block unrelated RPC responses. MCP startup forwards its remaining
millisecond deadline, while ordinary operations retain the outer MCP timeout. The host
must close this carrier after all borrowing Runtimes finish.

For this executor carrier, a configured `bearer_token_env_var` is resolved by the executor
when its `httpHeaderEnvVars` capability is true. The wire contains the variable name and
`Bearer ` prefix, not the controller's token; existing Authorization entries are replaced.
Legacy initialization metadata is fetched with `environment/info` only when needed.
Unsupported capability uses the existing controller-resolution branch; malformed capability
fails startup instead of falling back. Session404 recovery retains the selected credential
source. Ordinary `env_http_headers` still use controller resolution, as on this config path.

This does not complete executor parity: automatic CLI attachment/discovery, Noise or stdio
executor connections, remote process pipes, selected-root lifecycle and transparent executor
session reconnection remain unfinished. A WebSocket disconnect fails pending calls without
replay; the host can explicitly connect a replacement and publish its new binding. Each executor
HTTP hop stays stopped; the MCP layer separately validates any follow-up redirect request.
Merely declaring a nonlocal ID never creates a connection.
Declare additional names using `env_vars = ["SERVICE_TOKEN", {name = "REGION", source = "local"}]`
in the server table; literal `env` entries override inherited values. Internal launcher/identity
variables are removed even from explicit overrides. Nonempty inherited CA file paths are made
absolute before changing the server's working directory; literal CA overrides remain verbatim.
`source = "remote"` is parsed but fails local startup before spawning, without disabling other
servers. `env_vars` is stdio-only. These rules also apply to plugin MCP declarations.
Configuration reconciliation tracks explicitly referenced parent values, even when a literal
override masks them; changing unrelated/default parent variables alone does not restart a
healthy session. Use explicit refresh to force newly inherited defaults into a process.
This is environment selection, not an OS sandbox or remote execution implementation.
On POSIX, each local stdio server starts in its own process group. Explicit close or
last-client release sends TERM to that retained group; successful delivery schedules
KILL after two seconds independently of asyncio cancellation and the leader's exit.
Startup handoff and explicit close have owned tasks: cancelling their waiters still
joins cleanup before propagating cancellation. Close reaps the direct child, joins
readers and closes the owned pipe transport. Refresh still lets already-admitted calls
finish before retiring their connection; cold history does not replay completed calls.
Windows job containment, remote process cleanup and descendants escaping their group
are not implemented by this POSIX policy. Abrupt host termination is not verified.
HTTP MCP supports URL-only configuration, optional `http_headers` string tables,
`env_http_headers` mapping header names to parent variable names, and
`bearer_token_env_var`. Legacy `headers` remains available, but cannot be combined
with `http_headers`. Cross-transport fields and literal `bearer_token` are rejected.
Each connection snapshots its headers: valid environment values override literals,
missing/blank/invalid environment headers retain literal fallback, and explicit bearer
overrides Authorization. Missing, empty or invalid-Unicode bearer references fail that
server's startup without disabling other servers. Invalid header entries are skipped
with diagnostics that omit their values. POST protocol headers and session identity
override conflicting configured names case-insensitively; DELETE uses the same auth
snapshot. Named environment changes invalidate a reconciled connection, while admitted
old calls and their eventual DELETE retain old credentials. No automatic call replay.
The default User-Agent identifies Corki; a valid configured User-Agent overrides it.
Header-value UTF-8 is covered by a real loopback HTTP test. The underlying HTTP/1 carrier
rejects some outer-whitespace values accepted by Codex's header representation; this
currently produces a sanitized transport error, not trimmed credentials or fallback.
For local POSIX HTTP MCP, optional `http_headers_helper` runs a configured shell command
in the connection's local working directory, with the selected MCP environment. It must
not declare a non-local `environment_id`; such helper configurations are rejected. It must
emit one UTF-8 JSON string table within 10 seconds and 64 KiB; duplicate, reserved or
invalid headers fail without echoing helper output. The short-lived helper's process
group is reclaimed on completion, timeout, cancellation and owner release.
One connection shares cached results or failures. Cancelling a waiter leaves that shared
attempt running; closing/releasing its owner cancels and reclaims it. Helper headers are
same-origin only and preserve explicit Authorization. Each HTTP hop stops before a separate
MCP layer validates the next destination. Only rejected
POST 401/403 responses can refresh once, excluding Bearer insufficient_scope; the request
is resent only when effective helper headers change. Concurrent rejections share one
refresh cohort; failed/unchanged refresh retains the original challenge and old cache.
Transport errors and unknown outcomes are not retried. This does not add general OAuth,
Windows helper job containment or remote helper execution.
Legacy MCP requests can follow up to ten same-origin redirects. Each destination is checked
before transmitting credentials or a tool body; plaintext non-localhost DNS redirects are
rejected. POST301/302 and303 become bodyless GET (HEAD remains HEAD), while307/308 preserve
the method and body. Abandoned response streams are closed before the next hop, and startup
deadlines are shared. This applies to local, host-bound and executor HTTP, including SSE GET
resumption and session DELETE. Plaintext helper Proxy-Authorization redirects remain rejected.
The host catalog can explicitly mark plugin/selected-plugin attribution as `agent_plugin=True`;
that mode stops redirects when configured headers or Authorization are present. Ordinary
plugins remain legacy. Discovery and requests carrying MCP protocol2026-07-28 always stop.
Changing this source bit replaces the physical connection; it is not accepted from MCP metadata
or inferred from package names. Directory-loaded Agent Plugins now carry this attribution
automatically from manifest discovery. This does not implement the complete modern MCP lifecycle.
Locally created MCP, model, compaction, memory-quota and history/notes HTTP transports share
TCP connection handoff and TLS upgrade cleanup:
cancellation after a socket connects must close it even before HTTPcore has published a pooled
connection. The connector retains IPv6/IPv4 fallback and timeout behavior, and is installed for
locally created proxy pools as well. Explicit host transports are not inspected or mutated.
This adapter uses bounded HTTPX0.28/HTTPcore1.0 integration hooks and an attributed AnyIO-derived
TCP race with winning-stream cleanup; it does not globally patch those libraries or replay requests.
The shared layer only owns local connections: MCP redirect/helper/session policy is not applied
to model or memory requests. Caller-supplied clients, transports and mounts remain unmodified;
their internal connection ownership is the caller's responsibility.
HTTP MCP preserves valid JSON-RPC error code/message bodies on eligible non-2xx
responses. Session-404/authentication challenges and transient lifecycle statuses
retain transport-error precedence; lifecycle retry boundaries are described below. Response bodies
are collected incrementally under Corki's existing 16 MiB decoded-byte cap, with the
carrier closed on normal completion, overflow, read failure or cancellation. Accepted
POST 202/204 bodies are not read and cannot replace the session ID. This is not full
persistent SSE/GET support or a cap on a decompressor's individual allocation.
Each response owns one close task, including HTTPX's automatic EOF close. Repeated
cancellation waits for that cleanup before propagating; cleanup errors cannot replace
an existing cancellation/read error. This does not forcibly terminate a non-cooperative
custom transport or claim every pre-header handoff boundary has been verified.
After transport/header-helper handling, a tools/call 401 challenge becomes an
`Authentication required` error result without another request. Initialization/list,
bare 401 and 403 are not converted to that same result. Challenge response bodies are
not read. `ToolCallCompleted.mcp_result_json` carries a bounded JSON host result,
including private top-level `_meta`; `mcp_error` instead identifies transport failure.
The fields are mutually exclusive, retained in the execution ledger and excluded from
model history, Code Mode return values and ordinary CLI text. Host metadata is untrusted
data, not instructions or automatic login authority. Event results larger than 1 MiB
become a whole-result text preview with metadata/structured fields removed; re-escaping
that preview can make its final JSON larger than 1 MiB. This is not interactive OAuth,
or scope negotiation.

HTTP MCP initialization retries transient transport errors and HTTP 408/429/500/502/503/504
up to three attempts, with 250 ms then 1 s delays under one startup deadline. Each retry
rebuilds the session and repeats the handshake, including `notifications/initialized`.
`tools/list` uses the same bounded transient retry policy; ordinary tool/resource/prompt
calls do not automatically replay these failures. Only a POST 404 for a client-negotiated
session triggers a fresh handshake and one retry of the rejected operation. A literal
configured session header is not sufficient authority. A second 404 is returned as an
error; auth failures, malformed replies and unknown network outcomes are not session recovery.
Concurrent expiry shares a replacement by failed-generation identity. Static credentials
remain snapshotted, while the helper and session state are fresh; existing calls retain
their old generation until completion. Shutdown cancels owned work, joins cleanup and
prevents late publication. Retiring a session does not close a shared injected HTTP carrier;
the outer owner closes it after all generations. This is not full OAuth, modern discovery,
persistent SSE or remote transport support.

Request-scoped MCP SSE responses are now parsed incrementally: complete correlated events
return without waiting for server EOF. CR/LF/CRLF, fragmented UTF-8/BOM, multiline data and
the locked parser's field rules are supported. Control frames and inbound server requests
cannot complete an outbound call; unterminated final events are failures, not partial success.
One unfinished event/line has a 16 MiB defensive cap; completed comments/events do not
accumulate against a whole-stream byte limit. This cap remains a Corki policy difference
from Codex's unbounded legacy adapter, not a claim that all Codex paths use 16 MiB.
SSE read failures after a successful POST do not trigger initialization/tools-list send
retries. Cancellation still joins owned response cleanup.
Ordinary request streams now resume through GET with `Last-Event-ID` after a completed
event supplies a cursor, with or without a negotiated session. The original request's
URI, credentials, protocol/session headers and RPC identity stay captured; GET has no
request body. Graceful EOF waits for the server's `retry` value or the default one second;
stream/parser errors with a cursor retry immediately, except event-size violations remain
terminal. Failed GET attempts back off exponentially under the original operation deadline.
GET 404/auth/status failures never trigger session reinitialization or POST replay.
No completed cursor means no resumption; initialize retains its separate non-resumable path.
Each old carrier closes before the next GET, and shutdown/cancellation joins owned cleanup.
Inbound request/notification dispatch, complete all-message serde,
dynamic authentication-provider refresh and the reference's separate 50 ms connection-reuse
drain remain open transport work.

Legacy MCP initialization now validates the JSON-RPC response and required
`protocolVersion`, `capabilities`, and `serverInfo` fields, including known nested
capability flags and implementation/icon fields. Optional null and unknown fields are
accepted; protocol and implementation name/version strings follow the reference's type contract, not a local
version whitelist or nonempty restriction. Invalid results fail before the initialized
notification and catalog publication, including replacement-session handshakes.
HTTP initialization SSE uses its distinct first-response rules: event type is ignored,
empty data skipped, malformed nonempty JSON rejected, non-response messages skipped,
and a conflicting first response ID fails immediately. It does not resume via GET.
Initialized notifications accept 202/204 or a valid JSON-RPC JSON body, not SSE or an
untyped body. Local legacy stdio instead strips BOM, skips invalid JSON syntax and replies to
invalid handshake message framing with `Invalid Request` before continuing to wait.
Known bad initialization results fail independently of healthy servers; they cannot
contribute tools to search. Initialization now retains raw field occurrences until
the same typed result selection used for tool responses: duplicate known fields
are rejected, ordinary struct arrays and unit-enum objects are accepted, and a
valid earlier `DiscoverResult` cannot masquerade as `InitializeResult`. Capability
JSON values are projected before checking the catalog cache opt-out. Invalid
reinitialization does not publish a replacement session or replay the tool call.
This does not implement all ordinary-message serde,
unsolicited notification dispatch, or modern discovery.

Legacy `tools/list` also retains raw fields until complete typed result selection.
Every tool requires `inputSchema`; malformed members and duplicate known fields
invalidate the entire catalog before registration or caching. Valid struct arrays,
annotations and icons are projected to their public forms; schema/metadata values
keep their JSON semantics. An earlier matching response variant is not a tool list.
This legacy path deliberately ignores a validated `nextCursor` and admits at most
2,048 tools from its single response for all servers. Neither `codex_apps` nor a
legacy `host_owned_apps=True` source grants extra capacity or bypasses environment
MCP restrictions. The limit is enforced before tool filters and caching, including
custom client implementations. Pending/ready reuse retains the ordinary capacity;
changing only the legacy hosted flag no longer changes that capacity.

The account-scoped Apps cache and its host injection/update APIs have been removed.
Ordinary MCP catalog caching remains. Old `cache/corki_apps_tools` and
`cache/corki_apps_server_info` files are not read, migrated or deleted by this cleanup.
Legacy account-cache constructor arguments now fail explicitly rather than enabling
an alternate cache path. The manager no longer creates an Apps recovery controller
based on a server name. All server names use ordinary startup and explicit refresh;
the old Apps recovery helpers and their connection/generation leases are removed.

`await runtime.mcp_tool_catalog()` remains a host-only metadata query. Its detached
entries contain tool definitions and source metadata, not client handles or a grant
to execute a tool. The account cache and `refresh_mcp_apps_tools()` API are removed.
Use ordinary configured-server refresh; it must not replay business tool calls.
trigger integration and threadless discovery fallback remain separate alignment work.

Each connection generation owns its catalog revision. Refresh requests serialize within
that generation; listing uses its tool timeout and host catalog capacity. Successful
publication waits for already admitted tool preparation/RPCs, while independent calls
retain concurrent read access. Failed or cancelled listing leaves that generation's
prior catalog and revision intact. Human approval waits retain the exact connection but
do not hold the catalog lock. After review, the call checks its captured revision under
the read lock before applying session grants, marking external context or invoking RPC.
A refresh during review therefore rejects the stale call without remembering its grant;
declined reviews remain denials. Normal model dispatch captures the latest call authority.
That selection owns its generation before waiting for cold startup:
configuration replacement cannot redirect an already waiting call or hard refresh.
If it selected an initial startup which fails, later recovery cannot rewrite that
operation's result; a subsequent operation may use the recovered client. Named
resource fallback follows the same initial-outcome rule within its Step generation.
An unavailable initial startup is reported with its original failure; an explicitly
cancelled startup reports cancellation as a protocol error to its waiting operation.
MCP model calls expose these as error values (including `isError` inside Code Mode),
while cancellation of the calling task itself remains cancellation control flow.
The same error-value channel applies when an already selected MCP handler loses its
server or tool, or its tool is disabled before admission. These typed admission errors
retain the host API's `KeyError`/`ValueError` compatibility, but do not reject the Code
Mode promise. Unselected/unknown top-level tools and ordinary handler programming
errors remain separate dispatch failures; they are not converted by this MCP adapter.
Replacing configuration does not wait on an old generation's calls.

Hard refresh keeps its raw catalog override local to the generation. A same-account
cache race may return newer shared metadata while local callable definitions remain
those fetched by this client. Host catalog reads prefer that local override too.
Policy-only reconciliation resets the override without rewriting the physical owner's
original catalog. Schema snapshots already sampled by a model remain Step-owned.

All MCP servers use ordinary server/raw-tool naming, including a server named
`codex_apps`. Reserved connector metadata does not rename tools, grant identity,
or impose an extra `link_id` argument. A definition without connector metadata can
be searched, loaded and called through the ordinary Harness path.

Apps-specific exposure filtering and prepared-call policy execution have been removed.
Ordinary server/tool allowlists, approval settings and catalog revision checks remain.
Prepared calls retain exact-client metadata and reject stale revisions before effects.
The official Apps authentication URL/reconnect flow was also removed; generic MCP OAuth
and server-requested elicitation are separate capabilities.

Legacy Apps configuration parsing remains cleanup work. It is not a supported platform feature or
requirements to reproduce. Existing historical connector identities are not guessed or
silently rewritten during this cleanup.


Resource list/read RPCs now validate complete typed results before exposing them.
The three resource entry points are registered whenever an enabled, permitted MCP
server is configured, even if every server is still pending, dormant or failed.
An aggregate over an empty ready subset returns an empty list without starting dormant
servers; an exact server request can activate and wait for that Step's server. Empty,
disabled and managed-denied configurations do not register these entry points. Corki's
additional prompt helpers retain their separate ready-server registration condition.

The legacy `[orchestrator.mcp] enabled` setting no longer grants or denies ordinary
MCP tool, resource, template or prompt access by server name. Use ordinary server
enablement, tool filters and host environment requirements for access control.
Explicit host-supplied resource exclusions remain enforced independently.

`list_resources(cursor=None)` and `list_resource_templates(cursor=None)` return one
page object, including its optional `nextCursor`; custom `MCPClient` implementations
must implement this single-page contract rather than return a tuple of all entries.
The model's `list_mcp_resources` / `list_mcp_resource_templates` accept `server` and
`cursor`: naming a server returns one page; omitting it concurrently collects ready
servers, skips failed servers with a warning, and returns sorted entries with their
server provenance. Aggregate collection owns one deadline per server and enforces
100 pages, 2,048 entries and 64 KiB cursors; a valid empty server-returned cursor still
advances to another page. These aggregate limits do not reject a named single page.
Resource JSON is bounded by the current model's output policy before entering
durable history or Code Mode. Repeated cancellation joins collector cleanup before
releasing captured connection leases. Runtime captures an independent resource binding
with each Step: refresh cannot redirect that Step's calls to a newer connection. Aggregate
listing uses only the ready subset captured before sampling; a named request can wait for
that same generation's pending/dormant server. Step replacement releases its ownership,
while already-admitted calls retain theirs across ledger, event and execution-gate waits.
Turn cleanup joins retired resource views with no active calls. Successfully completed
Turns may leave admitted background cells alive; those calls retain the original binding
until completion or Runtime shutdown. Failure/cancellation interrupts cells and joins
their resource cleanup. Standalone host
manager calls still explicitly resolve the current directory; a bare registry snapshot
does not itself own a Runtime Step. Modern response caching and the additional prompt
RPC contract remain separate work.

Initialized HTTP sessions now own a common GET receiver and a shared pending-response
map. A 202/204 POST acknowledges sending, not RPC completion; responses received on GET
or another POST route by RPC ID. The common stream stays open across responses and can
resume without an event cursor; request-scoped streams still require a cursor to resume.
Initial GET405 is optional unsupported behavior; other initial GET failures are isolated,
and neither initial nor resumed GET failures cause tool POST replay. Individual requests
retain their original timeout/cancellation, while each receiver and its pending responses
belong to their exact connection generation. Close joins receivers and outstanding sends
before DELETE. Unknown/duplicate or cancelled response IDs cannot complete another call;
valid unmatched JSON responses wait for the RPC deadline. Request SSE ends after its first
response, even when that response belongs to a different pending request. This is response
routing, not yet full server-request/notification handling or exact reference scheduling,
queue backpressure, recovery barriers, or background 50 ms connection-reuse drain.

Ordinary HTTP JSON/SSE/GET and stdio messages now dispatch default reverse RPCs:
`ping` returns `{}`, `roots/list` returns empty roots, and unimplemented sampling/custom
methods return MethodNotFound with the exact server request ID. These incoming IDs never
consume identically numbered outbound calls. Reply sends belong to their connection/pipe
owner, are joined during close, and do not trigger session recovery or replay a user tool
when they fail. Remote `notifications/cancelled` uses exact typed request IDs and becomes
an MCP error Observation, not local Turn cancellation. Known progress/resource/list/log
notifications are logged at their source severity without injecting their private `_meta`
into model history. List-change logs do not automatically republish the tool catalog.
Full elicitation policy/widget parity, subscription applicability, exact ordinary serde and
transport queue/backpressure remain open; the explicit host-input path is described below.
The pinned Codex client explicitly disables RMCP response caching and stale-on-error
fallback; those upstream defaults are not missing capabilities to enable in Corki.
Natural stdio EOF now stops incoming admission and drains queued/active reverse replies
for up to five seconds, then closes the write half and waits up to three seconds for
the child before killing/reaping it. Legacy I/O read errors use the same EOF path.
Explicit host close can interrupt this grace; repeated cancellation still joins cleanup.
New outbound calls/notifications are rejected during drain, including writes queued
behind the sink lock; already-owned reverse replies retain their separate write path.
This does not turn ordinary HTTP SSE EOF/reconnect into service shutdown or introduce
a two-second grace before Codex's separate process-first explicit shutdown path.
Default reverse-RPC handling never automatically approves user requests.

MCP now advertises standard form/URL elicitation and routes `elicitation/create` through
an explicit, trusted host channel. Install an async callback with
`runtime.set_mcp_elicitation_handler(handler)` before starting a stream; it receives an
`ElicitationRequest` with `server_name`, a host-generated `request_id`, and `params`.
The host responds using `runtime.respond_mcp_elicitation(server_name, request_id, action,
content=..., meta=...)`, where action is `accept`, `decline`, or `cancel`. The callback
may enqueue the request and return, or await its owned user interaction. No handler
means immediate decline. Unknown, duplicate, stale or wrong-server responses are rejected.
This channel exists before Turn startup, follows reused/replacement MCP connections,
and never treats model output or ordinary steering text as a user response.

Incoming parameters follow RMCP's tagged form/URL attempt and subsequent legacy-form
fallback: an unknown mode or invalid URL branch can still be a valid form when a valid
`requestedSchema` is present. Known duplicate fields reject their candidate; schemas keep
raw property occurrences until every value is checked. A later valid property cannot hide
an earlier invalid one. Only the selected typed fields are delivered to the host.
String schema formats follow the same unit-enum decoding rule and are normalized
to `email`, `uri`, `date` or `date-time` before host interaction.
Ordinary schema records and their nested primitive/item records also accept their complete
positional form; the root order is `$schema`, `type`, `title`, `properties`, `required`,
`description`. They become normal schema objects before host delivery. Property maps and
the surrounding flattened request parameters still require objects.

Human waiting pauses the logical MCP client's counted active-operation budget; it
does not reset timeouts on progress notifications or extend HTTP retry eligibility.
The HTTP startup handshake retains its separate wall-clock budget. Code Mode's
exec/wait hold captured output while any host elicitation remains pending. Host-only
metadata and entered content are not directly added to model history (a server may
itself return user input in a later tool result). URLs are not automatically opened.
Server-origin privileged approval metadata and strict auto-review requests are still
declined; only host-constructed tool approvals use the separate `tool_approval` kind.
Standard form schemas are normalized at the
shared wire boundary: optional null fields are omitted, primitive numeric widths and
types are checked, property order is retained, and enum variants/unknown fields follow
the pinned RMCP selection rules. Invalid schemas receive InvalidParams before host
interaction. This is typed wire decoding, not arbitrary JSON Schema validation; it
does not add builder-only semantic checks. OpenAI form extensions, full request-envelope
parity and approval policy/reviewer behavior remain unfinished.

The CLI binds this host channel before startup/resume. Standard forms suspend and join
the ordinary composer reader, serialize concurrent requests, and restore its unfinished
draft afterwards. Form input uses a separate nonpersistent PromptSession, never normal
steering or input history. Fields accept text, JSON numbers/booleans and JSON arrays;
choices/defaults and required fields are displayed and checked. Enter `/decline` or
`/cancel` at a field to stop, and explicitly accept/decline/cancel before submission.
Ctrl+C clears a nonempty field draft first; on an empty field it cancels the request,
not the whole Turn. URL requests display the address for manual opening and require an
explicit response. Resolved/cancelled requests dismiss their owned prompt before the
composer resumes. This does not recreate Codex's full-screen widgets/keymap, all schema
dialect/format validation, URL completion notifications or privileged approval UI.

Ordinary MCP requests now carry a generation-local `_meta.progressToken`, independently
of their JSON-RPC request ID. Initialization and notifications do not allocate one.
The generated token replaces a caller-supplied token while preserving other metadata
and tool arguments without mutating the caller's input. New RPC attempts allocate new
tokens; SSE GET resumption keeps the same RPC/token, and a replacement connection starts
its own sequence. This does not reset deadlines on progress, add retries, or inject
transport metadata into model-visible tool definitions or observations.

Malformed MCP result envelopes and invalid HTTP JSON become MCP error values, including inside
Code Mode, without automatically replaying the call. A tools/call result needs a non-null known
field (`content`, `structuredContent`, `isError` or `_meta`); an empty/unknown-only object or a bare
completion acknowledgement is not a tool result. A present `resultType` must be `"complete"`:
input-required or future result types cannot silently become successful observations. Non-null
`content` must be an array, `isError` a boolean, and `_meta` an object. When another known field
establishes a result, missing/null `content` becomes `[]`; false/zero/empty structured values
remain intact. Private result metadata stays outside model-visible/nested tool values.
Correlated response IDs may be matching integers or ASCII numeric strings accepted by a
signed 64-bit parse (including `+1`/`0001` for request 1); booleans/floats, whitespace and
non-ASCII digits cannot alias them. HTTP and stdio share this locked-rmcp matching rule.
Null optional `isError` retains compatibility behavior; full ordered ServerResult variant selection,
content-block wire decoding and exact incoming numbers remain separate alignment work.
Other shell lifecycle and Code Mode runtime contracts, full MCP approval/catalog authority, complete media conversion and the
distinction between original handler bytes and prepared media still need further source alignment.
Raw hosted envelopes also use a 32 MB transport/storage guard, and
incoming events still count toward the adapter's full response limit; neither is a content truncation
policy. Full hosted notification producers, media capability conversion/non-WAV audio duration and
hosted discovered-tool registration remain incomplete. No metadata is inferred from untrusted text.

Stage-one source scanning excludes threads without an accepted user preview before its scan limit.
The first nonblank user request is sticky; image-only and audio-only inputs use media placeholders.
Assistant output, user-role context fragments and retained compaction copies cannot seed this preview.
Preview writes share the canonical history transaction; legacy history is backfilled on session-store
initialization without changing raw rows or source timestamps. Eligibility does not require a successful
source Turn.

Session provenance is a typed, host-owned Runtime input, persisted only when creating a Thread.
The CLI supplies Cli; consolidation supplies Internal(memory_consolidation); the generic host and
legacy database default is VSCode. Reopening preserves historical provenance while the current
initiator controls startup: Internal/SubAgent hosts skip background work, whereas root Exec/Mcp
hosts may start it. External labels resembling `internal_*`/`subagent_*` remain Custom root sources.
Historical extraction applies the pinned source-string allowlist before the scan limit. That Codex
revision stores typed Custom sources as JSON but filters with display strings: canonical Custom
atlas/chatgpt rows therefore do not match; legacy bare atlas/chatgpt values do. Corki preserves this
observed distinction instead of silently normalizing it. Unknown stored tags read as Unknown.
Memory layout preparation is owned by the eligible background pass, not its constructor; failure
warns and skips claims without failing the foreground Turn, and cancellation joins started writes.
Single-Thread archive and private volatile execution are described above. Durable-source ephemeral
imports, subtree lifecycle and parent permission inheritance remain unfinished requirements.
Explicit dedicated-memory-tool initialization is a separate path.

Background memory generation does not query Codex/ChatGPT account quota or require account
authentication. It uses the configured ordinary model transport; model failures remain isolated
from the foreground Turn. The former `memories.min_rate_limit_remaining_percent` configuration
is ignored and cannot enable account requests. Generic MCP OAuth is independent and retained.

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
no recorded finish time get a normal workspace check, not an invented cooldown. With the memory
feature and automatic background work enabled, the first empty memory store may make one
background initialization request.

Consolidation renews its owner-token lease while working. Workspace writes and final publication
are fenced by a SQLite write transaction, so an expired/replaced owner cannot overwrite a newer
owner's files. Cancellation joins already-started filesystem writes before releasing ownership;
selected inputs are marked only when their exact source version still matches. Background model
streams stop and close at completion, and failure-recording errors do not mask cancellation or
abandon sibling extraction jobs.

Memory database workers are also joined on cancellation, including citation usage,
claim/failure updates, enqueue and input reads. Background claim acquisition retains
its result until dispatch; cancellation before dispatch waits for that result, then
uses the existing owner-fenced failure path and configured retry delay. It cannot
release a replacement owner's lease. A failed or outcome-unknown database write is
not automatically replayed; warnings and lease expiry remain the recovery boundary.
Joining a worker does not undo a transaction that already committed.
Each short-lived memory connection is explicitly closed after commit or rollback;
transaction context exit alone is not treated as connection cleanup.
Cancelling an extraction batch also releases claims still waiting for concurrency
slots, after all started workers have stopped. Already committed successes remain
successful and do not consume another failure attempt.

The version-3 consolidation baseline stores one private content/executable-mode snapshot plus
input/output fingerprints. It supplies sorted A/M/D paths and before/after unified diff in the
next worker's file-backed evidence (4 MiB UTF-8-safe diff-body cap).
This snapshot contains recoverable file bytes (base64 is not encryption), has mode 0600, and is
replaced after successful owner-fenced publication, not accumulated into an edit history.
Deleted prior bytes disappear from that snapshot after successful consolidation; this does not
erase source conversation archives. Sampled inputs and newly published outputs form the new
baseline, so inputs added during sampling remain pending. Existing user `.git` metadata and
private files are not rewritten; the adapter retains Corki's visible-workspace file policy.

Extension resources remain separate from explicit ad-hoc note files and do not inherit their
user-update authority. The policy directs the worker to read each extension's instructions
before interpreting its sources. Startup creates the built-in ad-hoc instructions only when
absent, preserving user edits; a seed failure warns without disabling consolidation.
The policy requires reconciling deletions against surviving provenance rather than blindly
erasing all related facts. Edits to detail, summary or skills invalidate skipping; skipping also requires valid
artifacts (`v1` summary, regular files, no visible symlinks). Legacy fingerprint-only baselines
trigger a normal rebuild with an explicit warning that prior content/deletions are unavailable.
Consolidation now uses a separate ephemeral Runtime with normal tool execution, observations,
step budgets and cleanup. It can read additional sources, edit artifacts and verify changes;
the full bounded diff is available as `phase2_workspace_diff.md`, read with tools rather than
preloaded into the request. Stage one remains structured no-tools extraction. Stage two can finish with valid
edited files and a plain completion message, or return the existing complete JSON artifact
format for compatibility. The worker borrows the memory model transport and disables recursive
memories, MCP and plugins; its own history/checkpoint now stays in RAM, not merely outside the
parent thread's archive or in a directory deleted on close.

The worker uses ordinary base instructions and context construction; its consolidation task is
the user input. It receives the complete pinned consolidation policy: preference-first evidence,
task-local provenance and keywords, checkout applicability, faithful wording, scope/day routing,
mixed-evidence partial forgetting and incremental minimal-churn rules. Only storage references
are adapted to the actual private workspace, host snapshot diff and real thread IDs rather than
nonexistent JSONL paths. Evidence bodies are not eagerly injected; the worker chooses files to
read through tools. Historical skill mentions therefore do not become explicit user input.
The old evidence-fragment kind remains readable in existing archives. Normal request accounting
still includes the full policy, tools, context and results; the policy is not silently shortened
for a smaller model. These instructions do not prove real-model memory quality or injection resistance.
The child resolves its own model window, preserving explicit window overrides.
User/system and configured project skill roots and enablement rules are retained, but parent
conversation history and plugin capabilities are not. Project rules and cwd-derived skills
are discovered from the original memory workspace (including its applicable ancestors), while
tools/environment cwd point to the staging copy. Catalogs and explicit reads share that
discovery origin. Changes to applicable project rules are refreshed through normal step context.

Tools operate on a private copy of the sampled workspace. Outputs are validated and published
under the existing ownership fence only after worker shutdown; model failure, cancellation,
budget exhaustion or lost ownership does not publish staged edits. Changed source inputs are
rejected, except optional deletion of sampled direct `rollout_summaries/*.md` files. Before any
publication writes, deleted summaries are checked against their sampled content and mode; a
concurrent edit rejects publication. Successful deletions are removed from the next baseline.
Original notes cannot be deleted through this publication path. These checks are not protection
against adversarial filesystem rename races. Skill support files, binary assets and executable
modes are retained; UTF-8 output
text is secret-redacted, but binary assets are not secret-scanned. Temporary state is removed
after confirmed close. Worker shutdown waits at most ten seconds; timeout/failure keeps the
copy, close task and lease instead of treating the worker as stopped. Late successful close
can reclaim only the private copy, never publish results or update the baseline/job. Late
failure remains inspectable through `LongTermMemoryService.retained_workers` and warnings.
Parent service close uses a separate bounded wait for retained cleanup, and cancelling a
waiter does not cancel shared teardown. Failed workers remain strongly owned; this is
in-process tracking, not durable recovery of live processes across application restart.
This is not an OS sandbox: parent permission-profile handling remains incomplete.
Full Codex configuration-layer and model-specific base-prompt equivalence is not
claimed by this adapter. Codex edits its live root, whereas Corki
retains a staged publication adapter; the paths are not claimed to be identical.

Publication is still atomic per file, not across all files and SQLite: model failures before
publication preserve the previous files, but I/O failure or a crash during publication can leave
mixed generations. The inspected Codex local path likewise uses a live workspace; cross-store
atomic publication is not an established Codex contract. These limits and remaining parity work are tracked in
[the source audit](harness-alignment/audit.md); the complete Codex memory contract is not yet verified.

Before consolidation's unchanged-workspace check, the lease-owned input sync expires
direct `extensions/<name>/resources/*.md` files only for extensions with `instructions.md`.
The first 19 filename characters must parse as UTC `YYYY-MM-DDTHH-MM-SS`; timestamps
at least seven days old expire, regardless of file modification time. Recent/invalid names,
other file types, nested resources, instructions and `ad_hoc/notes` are not pruned.
Deletion failures are logged and other eligible resources continue. Expiry changes the
next consolidation diff even without new database inputs. It deletes source files before
sampling and is not rolled back on model failure; existing published memory and the last
successful baseline remain until a later successful consolidation. There is no new trash
or archival backup, and this rule does not forget conversation history. Existing workspace
symlink validation remains in force; pruning does not follow linked resource targets.

Thread source eligibility is a separate host control: `/memory mode enabled` or
`/memory mode disabled` persists the current thread's mode without sampling or steering
the model, including during a realtime turn. Embedders can call
`await runtime.set_thread_memory_mode("disabled")`, or supply `thread_id="<uuid>"`
to update an existing stored thread. The current thread can be materialized before its
first turn; invalid modes/IDs and missing stored threads fail without creating a target.
This works independently of `[memories].enabled` and `.generate`; custom session
repositories need the explicit `set_thread_memory_mode` capability.

At first creation, a thread's source mode is initialized from `[memories].generate`,
independently of the feature and recall/use switches. The mode is written atomically
with the thread, so `generate = false` cannot leave a newly created enabled source
for a different generator to claim. Reopening an existing thread preserves its
stored explicit or polluted mode even if the startup configuration changed; use the
host mode command to change it. Existing legacy rows are not retroactively disabled.
Custom session adapters must accept the `create_thread(..., memory_mode=...)` keyword
and apply it only on creation, without a later mode patch or resume overwrite.

This mode controls future extraction eligibility and consolidation input selection.
It does not change global generation/use settings, delete history/extracted data,
revoke already-owned extraction completion, or erase published/previously injected
memory. Reenabling makes retained eligible data selectable again. Updates do not
advance source timestamps or reset memory job watermarks. Started writes are joined
on cancellation; runtime shutdown rejects queued updates and drains the admitted write
before closing storage. This is a local adapter for Codex's experimental thread metadata
control, not its complete archived/ephemeral lifecycle or live global config system.

Explicit reset is a host operation, not a model tool. `/memory reset` previews the actual
configured directories; `/memory reset confirm` deletes memory extraction outputs/jobs
and the contents of the memory root plus the capability home's `memories_extensions`.
This includes notes, skills and private baselines. Roots remain, child symlinks are unlinked
without clearing their targets, and symlinked roots or broad workspace/home/database-containing
targets are rejected. No backup is created. Embedders can inspect `runtime.memory_reset_targets`
and explicitly call `await runtime.reset_memory()`, including when memory generation is disabled.

Reset preserves conversations, checkpoints and thread memory modes. It is not permanent
forgetting: future generation may extract from retained conversations again. Old job tokens
cannot publish after their jobs are cleared, but reset does not stop all hosts or prohibit newly
claimed work. Calls are serialized within this Runtime; started deletion is joined on cancellation
and before Runtime resource close. Database clearing commits before file clearing, so a file
error can leave a partial reset; `MemoryResetError.database_cleared` exposes that distinction.
Retry is explicit. Existing prompt/history text is not erased; normal next-step context updates
remove the current summary contribution. This is a local adapter for Codex's experimental
host reset, not a new cross-process barrier or a replacement for disabling generation.

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
win. Rules come from admitted user layers and explicit host `SkillRule` overrides;
project config cannot re-enable a user-disabled skill. Relative paths belong to the
declaring user config directory, and unrelated selectors survive higher user layers.
Disabled skills remain in the internal discovery snapshot but cannot be selected
or read through the skill tool. These controls are not filesystem permissions and do
not erase bodies already present in conversation history.

`[skills] include_instructions = false` hides the entire automatic catalog while
keeping explicit skill input, `skill_read`, and requested `skill_list` available.
This differs from `enabled = false`, which disables the skill service and its tools.
The first hidden/empty catalog is silent; changes from an existing catalog append
a distinct hidden or unavailable notice. Comparison-only records survive recovery
but are not model messages. Compaction rebuilds the current state without replaying
old notices. A successful persistent MCP approval reload now refreshes user-layer
skill rules for the next Turn: its catalog, `skill_list`, and `skill_read` use the same
new service, while the already-admitted Turn retains its old view. Explicit host rules
remain above file rules; failed preparation leaves published policy unchanged.
The same reload prepares plugin enablement, manifests, configured directories, MCP
declarations and Python handlers before publication. New Steps use the new plugin
tool generation; already-admitted calls retain their handlers. New Turns receive the
corresponding plugin/skill context. Explicit host plugin selections and MCP catalog
replacements retain authority over user files. Python entrypoints are cached by package and
entrypoint source digest, so unchanged registrations are not replayed; replaced modules remain
session-owned until close to keep old handlers valid. `[features] plugins = false`
prevents startup discovery/registration and filters typed `plugin`/`selected_plugin` MCP
contributions on every catalog generation, including host replacement, reconciliation,
refresh and cold recovery. This feature gate remains session-static across user reloads;
ordinary config/compatibility/extension MCP sources are unaffected. Filtering retains original
source identities and does not create a name-wide veto against unrelated host declarations.
The host `load_plugins=False` prevents local plugin discovery (including the installation cache),
without disabling separately supplied host MCP catalogs. Local plugin sources are the Runtime home
`plugins` directory, explicitly selected user-config/host `plugins.directories` roots, and configured
native installations at `plugins/cache/<marketplace>/<name>/<version>` under that home.
Native installations require a `[plugins."name@marketplace"]` entry; cache presence alone does not
enable a package. The active directory is `local` when present; otherwise selection uses Rust-style
SemVer ordering (including build metadata) when both versions parse, and lexical ordering otherwise.
Version-directory symlinks are excluded. Installation policy identity stays separate from the manifest
namespace. Native manifests do not authorize Corki in-process Python entrypoints.
Project `.corki/plugins` is not scanned automatically, and project-local directory declarations
cannot authorize plugin imports, even in a trusted project. To use a project plugin, select its
directory in the user config or pass `CorkiSettings(plugin_dirs=(... ,))` from trusted host code.
Both startup and reload apply this source boundary; project skill discovery is unchanged.
`[plugins.<package-name>] enabled = false` disables the package's effective Python,
skill and MCP contributions. This package flag can change on user-layer reload, unlike
the session-static `features.plugins` flag. Removing the rule restores default enablement for
directory-loaded compatibility plugins, but removes a native installation from the loading set;
captured project rules and explicit host selections retain their precedence. The legacy
`[plugins] disabled = [...]` selector remains supported. Array-valued `directories` and
`disabled` are legacy selectors; table-valued entries with those names are native package
policies, including opaque selected-root identities. File-based cold startup validates
the complete package policy mapping before constructing Runtime, including nested MCP
filter, approval and output-limit fields. Raw-layer service parsing is a separate path:
an invalid typed mapping is warned about and ignored as a whole by plugin selection and MCP
policy consumers, without discarding explicit legacy directory/disabled selectors. A valid raw
user-layer reload still publishes: selected-root MCP declarations retain their original limits
and managed constraints instead of keeping stale user policies. Cold typed validation remains
strict. Local native installation selection is re-evaluated on user-layer reload and cold startup.
Installed Agent Plugins receive a version-independent, hashed data directory under
`plugins/data/agent-plugins`. It is created only when a valid stdio MCP declaration needs it;
creation failure warns and removes stdio declarations while retaining HTTP siblings.
Complete installed-store semantics remain under alignment: remote installation overlays,
managed marketplace policy and hooks/apps are not yet covered by this local loading path.
Plugin skill roots now carry the installation identity separately from the manifest namespace.
Like native host plugin skills, their scope is `user`; plugin provenance is carried by `plugin_id`.
Agent Plugin skills are loaded only from direct child directories and their resolved files must
remain inside the plugin root. Legacy plugin skill roots remain recursive and may follow external
directory links. Format changes on user-layer reload reach the next Turn's skill view.
Same-name skills at different canonical paths remain available; duplicate canonical paths keep
the first source. Authored plugin skills supersede migrated commands of the same name only within
that plugin. Model-facing `skill_list` includes plugin IDs, and selected context keys include the
canonical file identity so selecting two same-name skills does not overwrite either body.
Skill discovery uses a bounded, sorted breadth-first filesystem walk: recursive roots descend
through at most six directory levels, with 2,000 visited directories, 20,000 examined entries
and a 4 MiB accounted walk-response budget per root. Limits count ordinary entries as well as
skills. File symlinks are ignored; permitted directory symlinks are followed once by canonical
identity. Hidden directories are pruned by their logical names, while visible links into hidden
directories remain eligible. Ordinary `references`, `scripts`, `assets` and `templates` directories
are not excluded just because of their names. Partial scans retain valid siblings and log scan
diagnostics; they are not reported as frontmatter errors. Cache signatures and parsing share the
same bounded inventory, including its error/truncation state.
Selected Python plugins still run trusted code in the host process; tool execution sandboxing
is not an in-process plugin sandbox. Plugin MCP policies now apply enablement, allow/deny lists,
and per-tool output limits as well as approval. Installed-plugin policies override declared
enablement/lists; selected-plugin policies intersect allowlists, union denylists, and cannot
reenable a disabled declaration. Both paths take the smaller declared/configured output cap.
Policy removal reloads the original declarations, not the previous restricted view. Code Mode
receives the complete structured MCP value; nested history and outer exec presentation retain
their separate output budgets.
This is not a general event bus:
hook refresh, invalid-plugin-configuration fallback, installation-store lifecycle, and other
skill controls still need alignment.

Skill headers allow an omitted/blank name (defaulting to the parent directory),
retain full descriptions, and repair specific unquoted prose fields. Header parsing
does not apply the catalog's description budget. Typed YAML fields preserve scalar
text; `no`/`off` are not aliases for a false invocation policy.

Explicit text selection accepts exact `$name` and `[$name](path)` mentions, including `skill://`
paths and discovery symlinks. Distinct same-name paths survive discovery; plain names select the
first enabled exact-name entry in scope/name/path order. Host-typed `InputMention` skill paths
are selected first and block same-label plain-name fallback even when the path is missing.
Text path mentions are processed before plain names, but do not suppress a separate plain mention.
Common environment variables are ignored; a linked label alone does not fall back to name selection.
Hosted-connector name collision checks and remaining skill control surfaces are still under alignment.

An explicit skill at turn start is read once and appended after that user input.
Its body is input-attached history, separate from the changing skill catalog:
later steps do not reread or revoke it, and another explicit turn can load it again.
Preparation/recovery preserves an already-recorded body even if its file changes.
Mid-turn compaction does not automatically reinject old skill bodies; the model can
use `skill_read` again. Mentions added through steering do not rerun turn-start
injection either. They remain available for model-directed discovery and reading.
# corki
