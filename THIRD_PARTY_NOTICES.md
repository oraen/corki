# Third-party notices

Corki bundles selected instruction packages so a clean installation has a useful baseline.

`prompts/modes/plan.md` reproduces the local Plan-mode harness instructions from
OpenAI Codex `codex-rs/collaboration-mode-templates/templates/plan.md`, commit
`ddf04ad26789d040f9ef6a96736f76602e35a6cc`, under the Apache-2.0 license and notice
reproduced in `src/corki/skills/CODEX_CATALOG_LICENSE.txt`. This is a local prompt,
not a dependency on an OpenAI account or service.

`src/corki/http_connect.py` adapts the TCP address ordering and Happy Eyeballs
race from AnyIO 4.15.1 with an additional cancellation-safe winning-stream cleanup.
It is a modified implementation, used only by locally owned MCP HTTP transports.
AnyIO's MIT license is reproduced in `src/corki/ANYIO_LICENSE.txt` and included
in the installed package. HTTP framing, proxy handling and TLS remain in separately
installed HTTPX/HTTPcore rather than being bundled or replaced.

`src/corki/_native/mcp_regex.wasm` contains regex-lite 0.1.8 from the Rust regex project,
under its MIT option, reproduced in `src/corki/_native/REGEX_LITE_LICENSE.txt`. The small
Apache-2.0 wrapper and exact Cargo lock are in `native/mcp_regex`; they implement the
full-value matcher used by pinned Codex. Wasmtime 48.0.0 is a separately installed
Python dependency and retains its upstream distribution notices.

The same module now includes the independent lifecycle-hook search engine:
regex 1.12.3, regex-automata 0.4.13, regex-syntax 0.8.8, aho-corasick 1.1.4 and
memchr 2.8.1, under their MIT options. Their notices are reproduced or referenced
in `src/corki/_native/HOOK_REGEX_LICENSES.txt`. The Hook literal/wildcard dispatch
adapts Codex `hooks/src/events/common.rs` under the Apache-2.0 license above.
This computational module has no filesystem, network or service imports; it does
not implement any OpenAI-specific protocol or authentication.

- `skills/imagegen`, `openai-docs`, `plugin-creator`, `review-agent`, `skill-creator`, and
  `skill-installer` are derived from the OpenAI Codex source distribution. License files shipped
  with those packages remain in their respective directories.
- `skills/arxiv`, `grounded-citations`, `pdf`, and `xlsx` are derived from Hermes Agent by Nous
  Research and are distributed under the MIT license reproduced in `skills/HERMES_LICENSE.txt`.

The skill instructions have small Corki compatibility changes to paths, runtime names, and helper
scripts. Those changes do not alter the upstream license terms.

`src/corki/context/environment.py` and the environment fragment renderer adapt the
single-local-environment snapshot/diff and XML escaping behavior from OpenAI Codex
`codex-rs/core/src/context/world_state/environment.rs` and
`codex-rs/core/src/context/environment_context.rs`, commit
`ddf04ad26789d040f9ef6a96736f76602e35a6cc`. These are modified Python implementations
under the Apache-2.0 license and upstream notice reproduced in
`src/corki/skills/CODEX_CATALOG_LICENSE.txt`; this attribution includes mapped tests.

`src/corki/context/local_time.py` follows the pinned Codex Turn timezone lifecycle
and the Linux/OpenWrt/Darwin behavior of its locked `iana-time-zone`0.1.65 dependency.
The dependency's primary source is published at
https://docs.rs/crate/iana-time-zone/0.1.65/source/ and is licensed MIT OR Apache-2.0;
the modified Python adaptation uses the Apache-2.0 option. The Windows PowerShell
WinRT adapter is Corki-specific, not a reproduction of the Rust ABI bindings.

`src/corki/shell.py` and shell invocation/configuration behavior adapt
`codex-rs/shell-command/src/shell_detect.rs`, `codex-rs/core/src/shell.rs`, and the
direct execution path of `core/src/tools/handlers/unified_exec.rs` from the same
pinned Codex Apache-2.0 distribution. The same license/notice above covers mapped
tests. Python Step shell checkpointing is a Corki-specific durability adaptation.

Execution identity and host-owned environment metadata in Runtime, the SQLite session
adapter, `protocol/execution_identity.py` and shell execution adapt the pinned Codex
`core/src/{exec_env,thread_manager}.rs`, `core/src/session/session.rs`,
`core/src/agent/control.rs` and `memories/write/src/runtime.rs`, under the same
Apache-2.0 license. SQLite identity migration and one-time Python process-manager
binding are Corki durability/ownership adaptations. CODEX-prefixed compatibility
metadata contains Corki's identities/version, not OpenAI Codex's release identity.

Memory startup triggering and source-eligibility configuration adapt the pinned Codex
`app-server/src/request_processors/turn_processor.rs`, `memories/write/src/start.rs`,
`config/src/types.rs` and `core/src/session/session.rs` under the same Apache-2.0
license. Python task-set ownership, durable-admission placement and the explicit
`memories.background_enabled` pause are Corki host adaptations, not native Codex APIs.

The local compaction handoff prefix in `prompts/context/compaction_summary_prefix.md`
is from pinned Codex `prompts/templates/compact/summary_prefix.md` (with a resource
terminal newline). Its user role/classification and mapped tests adapt
`core/src/compact.rs`, `core/src/context/compaction_summary.rs` and
`context-fragments/src/fragment.rs` under the same Apache-2.0 license. Keeping a bare
durable summary and projecting the prefix at readable boundaries is a Corki storage
adaptation, not a different instruction-authority contract.

Shell environment configuration and process derivation in
`src/corki/config/shell_environment.py` and `src/corki/tools/builtin/shell_environment.py`
adapt pinned Codex `config/src/shell_environment_policy.rs`,
`protocol/src/shell_environment.rs`, `core/src/unified_exec/process_manager.rs` and
`core-plugins/src/plugin_metrics_sidecar.rs` under the same Apache-2.0 license.
The wildcard matcher and mapped cases follow locked wildmatch 2.6.1, published at
https://docs.rs/wildmatch/2.6.1/src/wildmatch/lib.rs.html, with a modified Python
implementation. Its MIT license ships in `corki/tools/builtin/WILDMATCH_LICENSE.txt`
and is reproduced below:

MIT License

Copyright (c) 2020 Armin Becher

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

`src/corki/skills/catalog.py` follows the host catalog allocation and alias behavior in
OpenAI Codex (`codex-rs/ext/skills/src/{render,aliases,host_aliases}.rs`, commit
`ddf04ad26789d040f9ef6a96736f76602e35a6cc`). The catalog prompt resources adapt its
host introductions. These files are modified Python/Corki implementations, not upstream
Rust files. The Apache-2.0 license and upstream notice ship in
`src/corki/skills/CODEX_CATALOG_LICENSE.txt`.

`src/corki/skills/frontmatter.py` and `mentions.py` also adapt parsing, limited
scalar repair, and default host text selection from the same pinned Codex
distribution (`codex-rs/skills/src/{parser,mentions,selection}.rs`). The same
Apache-2.0 license and notice above apply to these modified Python implementations.
The skill catalog hidden/unavailable notices and state transitions also adapt
`codex-rs/ext/skills/src/world_state.rs` from that pinned distribution.
`prompts/context/compaction_warning.md` reproduces the local compaction notice
from `codex-rs/core/src/compact.rs` in the same Apache-2.0 distribution.
The stage-one memory claim, retry/watermark, and no-output invalidation behavior in
`src/corki/memory/{extraction,consolidation,sqlite,pipeline}.py` adapts the same pinned distribution's
`codex-rs/state/src/runtime/memories.rs` and `codex-rs/memories/write/src/phase1.rs`.
These are modified Python/SQLite implementations; the same Apache-2.0 license and notice apply.
`prompts/memory/stage_one_system.md` reproduces the complete phase-one generation
instructions from `codex-rs/memories/write/templates/memories/stage_one_system.md`
at that same pinned commit. `prompts/memory/stage_one_input.md` adapts its input
framing to Corki's SQLite thread archive instead of claiming a JSONL rollout file.
The same Apache-2.0 license and upstream notice apply to these prompt resources.
`prompts/memory/read_path.md` adapts the complete read policy in the same pinned
Codex distribution's `codex-rs/ext/memories/templates/memories/read_path.md`.
Changes describe Corki's real Markdown/SQLite storage and existing tool names;
`memory/context.py` and Runtime registration adapt its shared read/use feature gate.
The same Apache-2.0 license and notice cover these modified resources and mapped tests.
Dedicated memory filesystem error classification and non-following enumeration in
`src/corki/memory/{backend,tools}.py` adapt the pinned distribution's
`codex-rs/ext/memories/src/backend.rs`, `src/tools/mod.rs`, and
`src/local/{list,read,search,path,ad_hoc_note}.rs`. Python exceptions preserve the
distinction between semantic errors and actual storage I/O, using Corki's existing
direct versus nested dispatch boundaries. The same Apache-2.0 license and notice apply.
The ad-hoc note contracts in `src/corki/memory/{backend,tools}.py` and
`prompts/memory/ad_hoc_instructions.md` also adapt `codex-rs/ext/memories/src/{local,tools}/ad_hoc_note.rs`
and `codex-rs/memories/write/templates/extensions/ad_hoc/instructions.md` from that pinned
Apache-2.0 distribution. Corki's JSON consolidation path is a modified implementation,
not the native filesystem-editing consolidation agent.

`prompts/memory/consolidation.md` retains the complete pinned
`codex-rs/memories/write/templates/memories/consolidation.md`, adapting product name,
template syntax, snapshot workspace description and actual thread-ID provenance.
The extension prompt blocks and seeding behavior adapt that crate's `src/prompts.rs`,
`src/lib.rs`, `src/start.rs` and `src/extensions/ad_hoc.rs` under Apache-2.0.
`memory/consolidation_prompt.py`, child-Runtime input and staged-summary deletion
integration are modified Python implementations. `consolidation_runtime.md` describes
Corki's staged publication/JSON compatibility boundary; it is not a native sandbox
or a claim of identical filesystem paths and permission handling.
Memory read line boundaries, search comparison and memory summary/read truncation in
`src/corki/memory/{backend,context,inputs}.py` also adapt the pinned distribution's
`codex-rs/ext/memories/src/local/{read,search,path}.rs`, `src/prompts.rs` in that extension,
and `codex-rs/utils/string/src/truncate.rs`, under the same Apache-2.0 license.
The external-context tool-result flag and best-effort memory marking adapt
`codex-rs/tools/src/tool_output.rs`, `codex-rs/core/src/tools/registry.rs` and
`codex-rs/rollout/src/state_db.rs` from that same pinned Apache-2.0 distribution.

MCP host-owned memory policy and pre-call notification in `src/corki/mcp/` follow
`codex-rs/codex-mcp/src/{server,binding}.rs` and `codex-rs/core/src/mcp_tool_call.rs`
from the same pinned Apache-2.0 distribution. Corki's retained-connection lease
is not an implementation of Codex's catalog revision read/write lock.

The external model-item classification and hosted event preservation in
`src/corki/{protocol,models,memory}/` and the Runtime follow
`codex-rs/core/src/stream_events_utils.rs`, `core/src/session/{mod,turn}.rs` and
`protocol/src/models.rs` from that same pinned Apache-2.0 distribution.
The bounded JSON envelope and Chat replay are Corki compatibility implementations;
the byte cap does not implement Codex's per-model truncation policy.

`src/corki/protocol/truncation.py` and `src/corki/context/hosted_output.py` adapt
`protocol/src/protocol.rs`, `core/src/context_manager/history.rs`,
`utils/output-truncation/src/lib.rs` and `utils/string/src/truncate.rs` from that
same pinned Codex distribution. Bundled output policies follow
`models-manager/models.json` and the fallback/override behavior in `model_info.rs`.
The attribution and Apache-2.0 terms above also apply to the mapped golden tests.
`src/corki/context/function_output.py` and the ordinary result/ledger metadata path
also adapt `tools/src/tool_output.rs`, `core/src/tools/{context,registry}.rs` and
`core/src/session/mod.rs` from that pinned distribution. The same license applies;
the explicit legacy character cap and raw transport guard are Corki compatibility policies.
`src/corki/mcp/output.py` and the admitted MCP output-policy path also adapt
`protocol/src/models.rs`, `tools/src/tool_output.rs`, `core/src/tools/context.rs`,
`core/src/tools/handlers/mcp.rs`, `core/src/mcp_tool_call.rs` and
`codex-mcp/src/binding.rs` from the same pinned Apache-2.0 distribution.
The same terms apply to the source-mapped MCP output and media tests.
`src/corki/tools/builtin/shell_output.py`, bounded head/tail collection in `process.py`,
and corresponding shell tests adapt `core/src/tools/context.rs::ExecCommandToolOutput`,
`core/src/unified_exec/{head_tail_buffer,process_manager}.rs` and
`utils/output-truncation/src/lib.rs` from the same pinned Apache-2.0 distribution.
They are modified Python implementations; existing Corki process lifecycle policies remain distinct.
Post-exit pipe draining and owned process-group retirement in `process.py` also follow
`core/src/unified_exec/{process,process_manager}.rs` and `utils/pty/src/{process,pipe}.rs`
from that pinned Apache-2.0 distribution. Asyncio exit polling, transport closing and
shared cancellation-safe cleanup are Corki-specific adaptations, not the Rust I/O implementation.
`process_io.py` and the PTY writer queue in `process.py` adapt the pinned distribution's
`utils/pty/src/unix_io.rs` and `pty.rs` under the same terms. Asyncio readiness registration
and Python task/queue ownership are modified implementations; unexpected I/O exception
classification is explicitly stricter than the source's broad reader EOF handling.
`shell_policy.py` and shell yield configuration/tests adapt the pinned distribution's
`core/src/unified_exec/{mod,process_manager}.rs` and unified-exec tool handlers under
the same Apache-2.0 terms. Python schema admission explicitly represents the handlers'
unsigned-integer contract; the source public schema uses explanatory numeric fields.
`process_retention.py` and resumable lifetime/interaction changes in `process.py` adapt
the pinned distribution's unified-exec process manager/state and interactive handler under
the same terms. The soft cap, recent-session protection and locked-exit policy follow that
source; preserving an explicitly configured host lifetime cap is Corki compatibility behavior.
The closed-pipe stdin and PTY interrupt routes in `process.py`, and child terminal
setup in `pty_spawn.py`/`_pty_exec.py`, follow the pinned unified-exec manager,
exec-server local process, sandboxing spawn and `utils/pty/src/pty.rs` under the
same terms. The isolated Python helper and bounded exec-status handshake are modified
Python ownership adapters, not a copy of Rust's post-fork implementation.
`process_status.py` adapts the pinned distribution's `utils/pty/src/process.rs` and
default `pty.rs` exit-status projection under the same terms. Default PTY behavior
was verified against the pinned `portable-pty` 0.9.0 upstream `src/lib.rs`
`From<std::process::ExitStatus>` implementation; no dependency source is vendored.
Shell parallel-dispatch declarations in `shell.py` follow the pinned distribution's
`core/src/tools/handlers/unified_exec/{exec_command,write_stdin}.rs` under the same terms;
the existing Python schedulers and per-session interaction locks remain in use.
Turn interrupt retention and host terminal metadata/list/termination adapt the pinned
distribution's `core/src/{tasks/mod,session/handlers,codex_thread}.rs` and unified-exec
manager under the same terms. UUID session IDs, immutable Python metadata and joined
asyncio cleanup are modified adapters; live OS process handles are not serialized.
`process_groups.py` also adapts the exact-group macOS permission-denied fallback from
`utils/pty/src/process_group.rs` under the same terms; its libproc binding and bounded
member-list allocation are Python-specific adaptations.
`src/corki/code_mode/output.py` and its cell integration adapt
`core/src/tools/code_mode/mod.rs::handle_runtime_response` and
`utils/output-truncation/src/lib.rs` from that pinned Apache-2.0 distribution,
with source-mapped Code Mode output tests under the same terms.
`src/corki/memory/workspace.py` and baseline/pipeline changes adapt the pinned
distribution's `codex-rs/{git-utils/src/baseline,memories/write/src/workspace}.rs`
and phase-two input/reset contracts under the same Apache-2.0 terms. Single JSON
snapshots, Python difflib and provider-neutral evidence fields are modified adapters,
not Git object storage or the native consolidation agent.
`src/corki/memory/{transcript,sanitizer}.py` also adapt the pinned distribution's
`codex-rs/memories/write/src/phase1.rs` and `codex-rs/secrets/src/sanitizer.rs`.
The same Apache-2.0 license and notice apply to these modified Python implementations.
The model-window resolution and stage-one budgeting/truncation in
`src/corki/protocol/context.py`, `src/corki/config/model_context.py` and
`src/corki/memory/inputs.py` adapt the same pinned Codex distribution's model-info,
memory prompt and `codex-rs/utils/string/src/truncate.rs` behavior under that license.
Static model-name matching also adapts `codex-rs/models-manager/src/manager.rs`
from that distribution (longest-prefix and scoped namespace lookup).
`src/corki/config/bundled_models.py` contains a window-only projection of the same
distribution's `codex-rs/models-manager/models.json`; memory model/effort defaults
adapt `codex-rs/model-provider/src/provider.rs` and `codex-rs/memories/write/src/lib.rs`.
These adaptations use the same Apache-2.0 license and notice described above.

The interrupted-turn guidance in `src/corki/context/interruptions.py` comes from
`codex-rs/core/src/context/turn_aborted.rs` in that pinned Codex distribution.
Its non-V2 history/compaction contract is adapted for Corki's item protocol under
the same Apache-2.0 license and notice.

The provider-usage/local-tail accounting in `src/corki/context/usage.py` and
encrypted-reasoning length estimate in `src/corki/context/tokens.py` adapt
`codex-rs/core/src/context_manager/history.rs` from the same pinned distribution,
under the Apache-2.0 license and notice above.
The body-after-prefix window state in `src/corki/context/usage.py` also adapts
`codex-rs/core/src/state/auto_compact_window.rs` and the scope/hard-limit decisions
in `codex-rs/core/src/session/context_window.rs` under the same license.
The default TokenBudget reset and context-window identity in
`src/corki/context/token_budget.py`, `src/corki/context/window.py` and the tools in
`src/corki/tools/builtin/context.py` adapt `compact_token_budget.rs`,
`session/mod.rs::start_new_context_window`, `context/token_budget_context.rs` and
the `new_context_window`/`get_context_remaining` handlers in that distribution,
under the same Apache-2.0 license and notice.

The reminder template and custom TokenBudget validation in
`src/corki/config/token_budget.py`, and the post-sampling reminders/fallback
buffer decisions in `src/corki/context/window.py` and `src/corki/core/graph.py`,
adapt `config/mod.rs::TokenBudgetConfig`, `session/token_budget.rs::maybe_record`,
`session/turn.rs`, and `session/context_window.rs` from the same Codex distribution
under the same Apache-2.0 license and notice. Persisted notice IDs are Corki's
recovery-compatible implementation, not a claim of identical upstream storage.

Opaque encrypted tool-output preservation and estimation in
`src/corki/protocol/tools.py`, `src/corki/context/tokens.py`,
`src/corki/context/tool_output.py` and the provider/Code Mode projections adapt
`protocol/src/models.rs::FunctionCallOutputContentItem`,
`core/src/context_manager/history.rs::estimate_encrypted_function_output_length`,
`utils/output-truncation/src/lib.rs` and `tools/src/tool_output.rs` from the same
Codex distribution under the same Apache-2.0 license and notice. Provider opt-in
and unsupported-protocol notices are Corki's compatibility boundary.

Namespace grouping, default descriptions and namespace-aware tool identity in
`src/corki/models/namespaces.py`, `src/corki/protocol/tool_names.py` and the tool
adapters follow `protocol/src/tool_name.rs`, `tools/src/responses_api.rs` and
`core/src/tools/spec_plan.rs::merge_into_namespaces` from the same pinned Codex
distribution under the same Apache-2.0 license and notice. Hashed flat-protocol
aliases and explicit alias-collision rejection are Corki compatibility choices.

The native client, tool contracts and private recovery descriptions in
`src/corki/history_notes/` adapt `ext/history-notes/src/{backend,extension,tools}.rs`
from the same pinned Codex distribution under the same Apache-2.0 license and
notice. Window ingestion metadata follows `core/src/responses_metadata.rs`.
Explicit backend credential configuration and the single-root identity mapping
are Corki compatibility choices, not an implementation of Codex OAuth or server storage.

The local compatibility backend in `src/corki/history_notes/{archive,local,notes_store,output}.py`
implements those public action contracts using Corki's durable archive and SQLite.
Its readable result shapes, virtual-path validation and immediate local consistency
are Corki implementations, not copied Codex backend storage or response schemas.

`src/corki/tools/bm25.py` adapts scoring behavior from Michael Barlow's `bm25` Rust
crate version 2.3.2 under the MIT license. The copyright and permission notice is
distributed in `src/corki/tools/BM25_LICENSE.txt`, including installed wheels.

`src/corki/tools/tokenizer.py` follows that crate's fixed English path, using
the exact `deunicode` 1.6.2 transliteration data and `stop-words` 0.9.0 NLTK
English list in `tokenizer_data.json`. Its ASCII word boundaries follow
`unicode-segmentation` 1.12.0 and its u32 term identity follows `fxhash` 0.2.1.
Source and permission notices ship in `src/corki/tools/TOKENIZER_LICENSES.txt`.
`snowballstemmer` 2.2.0 is a pinned BSD-licensed dependency; the pure-Python
English stemmer is selected explicitly. Upstream rust-stemmers test corpora
used for external verification are not redistributed with Corki.

`src/corki/context/deferred_tools.py`, its Markdown fragments and mapped golden
tests adapt `codex-rs/core/src/context/world_state/{tools,tools_tests}.rs` and
`core/src/tools/registry.rs::deferred_tool_namespaces` from the pinned Codex
distribution above, under the same Apache-2.0 license and notice. Corki uses
versioned full-map comparison records instead of RFC 7386 section patches;
its Markdown resource newline is included in the model-visible byte budget.

`src/corki/mcp/names.py` and its mapped naming tests adapt the pinned Codex
`codex-rs/codex-mcp/src/tools.rs`, `mcp/mod.rs` sanitizer and
`connection_manager_tests.rs` under the same Apache-2.0 license and notice.
Regular MCP namespace construction/search metadata follows `rmcp_client.rs` and
`core/src/tools/handlers/mcp.rs`; Corki's `namespace::leaf` storage and hashed
compatibility wire aliases are implementation-specific representations.

`src/corki/mcp/tool_filter.py` and mapped policy tests adapt `ToolFilter` in
the same pinned `codex-rs/codex-mcp/src/tools.rs`, under the same Apache-2.0
license and notice. Generation-owned filters and explicit refresh admission
integrate with Corki's existing connection lifecycle.

MCP reconciliation in `src/corki/mcp/{reconciliation,connection,manager}.py`
follows `codex-rs/codex-mcp/src/{runtime,connection_manager,server,binding}.rs`
from that pinned Apache-2.0 distribution. Explicit view references and per-client
task-local timeout scopes implement the corresponding ownership and request-policy
boundaries in Python; they are not the Rust Arc or OAuth identity implementation.

`src/corki/config/mcp_environment.py`, `src/corki/mcp/environment.py` and their
mapped tests adapt the pinned Codex `config/src/mcp_types.rs`,
`rmcp-client/src/utils.rs`, `protocol/src/shell_environment.rs` and
`network-proxy/src/certs.rs` environment selection contracts and key lists,
under the same Apache-2.0 license and notice. Named-reference reconciliation
follows `codex-mcp/src/server.rs`; remote execution is not implemented here.

`src/corki/mcp/process_group.py` and stdio client lifecycle tests adapt
`rmcp-client/src/{stdio_server_launcher,local_stdio_transport,local_child}.rs`
and `rmcp-client/tests/process_group_cleanup.rs` from the same pinned Apache-2.0
distribution. Python weak-reference finalizers, independent timers and owned asyncio
tasks implement local POSIX cleanup boundaries; Windows jobs and remote cleanup
are not implemented by this adaptation.

`src/corki/config/mcp_headers.py`, `src/corki/mcp/http_headers.py` and mapped
HTTP configuration/request tests adapt the pinned Codex `config/src/mcp_types.rs`,
`rmcp-client/src/{utils,http_client_adapter,rmcp_client}.rs` and
`codex-mcp/src/{rmcp_client,server}.rs` under the same Apache-2.0 license and notice.
The default User-Agent identifies Corki, not Codex. HTTPX-specific safe error handling
and immutable Python header snapshots are implementation-specific; OAuth and remote
header resolution are not implemented by this adaptation.

`src/corki/mcp/{header_helper,header_helper_process,auth_challenge}.py` and mapped
tests adapt `rmcp-client/src/{http_headers,http_headers_tests,www_authenticate}.rs`
and helper application in `codex-mcp/src/runtime.rs` from the pinned Apache-2.0
distribution. Python task ownership/weak-reference cleanup implements the local POSIX
helper lifecycle; this is not Windows Job containment or a general OAuth client.

`src/corki/mcp/http_response.py` and mapped HTTP response tests adapt classification
and chunk-limit checks in the pinned `rmcp-client/src/http_client_adapter.rs`
(`post_message`, `retryable_post_response_status`, `parse_json_rpc_error`,
`collect_body`) under Apache-2.0. HTTPX response ownership/caching and Corki's legacy
16 MiB collection policy are Python implementation choices, not full RMCP parity.

MCP authentication result and bounded host-event projection in `mcp/client.py`,
`mcp/event_result.py`, `protocol/mcp.py` and mapped Runtime tests adapt the pinned
`rmcp-client/src/rmcp_client.rs::call_tool`, `core/src/mcp_tool_call.rs::
truncate_mcp_tool_result_for_event`, and `core/tests/suite/mcp_oauth_refresh_tests.rs`
under Apache-2.0. Corki's immutable JSON event fields and optional ledger fields are
Python contracts; private metadata remains separate from model/Code Mode projections.

`src/corki/mcp/http_recovery.py`, HTTP client generation ownership and mapped lifecycle
tests adapt the pinned `rmcp-client/src/rmcp_client.rs::{run_service_operation,
reinitialize_after_session_expiry}`, `streamable_http_retry.rs` and
`rmcp-client/tests/streamable_http_recovery.rs` under Apache-2.0. Python-owned generation
leases, shared HTTP carrier borrowing and cancellation joins implement the local HTTP
path; this does not implement the reference's complete OAuth/remote/SSE transport.

`src/corki/mcp/sse.py`, `src/corki/mcp/sse_resume.py`, request-scoped HTTP SSE consumption,
GET continuation and response-ID matching adapt
RMCP 3.2.0 (`src/transport/{streamable_http_client,common/client_side_sse}.rs`,
`src/{model,service}.rs`, Apache-2.0) and sse-stream 0.2.5 (`src/stream.rs`, dual
MIT OR Apache-2.0; used here under Apache-2.0). These exact crates are pinned by Codex's
Cargo.lock; archive checksums and primary source links are recorded in the alignment audit.
This Python adaptation changes implementation details and retains an explicit bounded
event policy. Ordinary request GET continuation is implemented; shared unsolicited
streams and server-message dispatch remain separate.

`src/corki/mcp/initialization.py` and the initialization-specific HTTP/stdio routing
adapt RMCP 3.2.0 `model.rs`, `model/capabilities.rs`, `service/client.rs`,
`transport/streamable_http_client.rs` and `transport/async_rw.rs` under Apache-2.0.
The Python validator and ownership wiring are modified implementations, not a Rust
runtime or a complete port of the SDK's all-message serde and compatibility behavior.

`src/corki/mcp/http_routing.py`, common GET reception and cross-POST response routing
adapt RMCP 3.2.0 `transport/streamable_http_client.rs` and `service.rs` under Apache-2.0.
Python futures and generation-owned tasks replace the reference's worker channels;
this is not a complete port of its scheduling, server handlers or recovery barriers.

`src/corki/mcp/inbound.py` and HTTP/stdio incoming-message dispatch adapt RMCP 3.2.0
`service.rs`, `handler/client.rs`, `model.rs`, `model/serde_impl.rs`, and the pinned
Codex `rmcp-client/src/logging_client_handler.rs` under Apache-2.0. Modified Python
task ownership and error classification implement the default local handler path;
elicitation policy, subscriptions/caches and exact transport scheduling are not included.

Natural stdio EOF reply drain and write-half/child cleanup in `mcp/inbound.py` and
`mcp/client.py` adapt RMCP 3.2.0 `service.rs` / `transport/async_rw.rs` and the pinned
Codex `rmcp-client/src/local_stdio_transport.rs` under Apache-2.0. Python shielded
close ownership preserves cleanup during repeated cancellation. Natural EOF handling
is separate from Codex's process-first explicit shutdown and HTTP SSE resumption.

Ordinary-request progress token allocation/metadata preparation in `mcp/client.py`
adapts RMCP 3.2.0 `service.rs` (Peer request options and AtomicU32Provider backed by
AtomicU64) and `model/meta.rs` under Apache-2.0. The Python implementation preserves
caller input and follows the pinned Codex wrapper's no-progress-timeout-reset path;
modern association/client metadata and elicitation timeout pauses remain separate.

MCP host elicitation routing, response normalization and active-time budgets in
`mcp/elicitation.py`, `mcp/active_time.py`, `mcp/inbound.py` and their Runtime/HTTP
integration adapt pinned Codex `codex-mcp/src/elicitation.rs`,
`rmcp-client/src/{elicitation_client_service,rmcp_client}.rs`,
`core/src/session/handlers.rs` and `core/src/tools/code_mode/{execute,wait}_handler.rs`
under Apache-2.0. Python host callback routing and counted asyncio deadlines replace
Rust channels/guards. Standard request envelopes follow locked RMCP 3.2.0 `model.rs`;
exact primitive-schema serde, OpenAI extensions and Codex approval policy remain gaps.

CLI elicitation flow in `cli/elicitation.py`, `cli/input_owner.py` and
`cli/{application,terminal}.py`, plus host-response dismissal in `mcp/elicitation.py`,
adapts the pinned Codex `tui/src/bottom_pane/{mod,mcp_server_elicitation}.rs` and
RMCP 3.2.0 `model/elicitation_schema.rs` under Apache-2.0. Python prompt ownership,
nonpersistent input and typed field prompts replace Rust modal widgets/queues; full
widget/keymap, schema serde and privileged approval-policy behavior are not reproduced.

Shared standard form schema normalization in `mcp/elicitation_schema.py` adapts locked
RMCP 3.2.0 `model/elicitation_schema.rs` under Apache-2.0: optional-field normalization,
ordered wire properties, primitive widths, enum variant ordering and per-variant field
selection. Python dictionaries/validators replace Rust serde types. Builder semantic
checks, full request-envelope/extension handling and a Rust-executable oracle are not
included in this adaptation.

Remote compaction V2 in `context/remote_compaction.py`, `models/compaction_stream.py`,
`protocol/compaction.py` and related window/Responses integration adapts pinned Codex
`core/src/compact_remote{,_v2,_v2_attempt,_v2_images}.rs`, `protocol/src/models.rs` and
`model-provider/src/provider.rs` under Apache-2.0. Python request controls, opaque JSON
checkpoint persistence, bounded retention and stream collection replace Rust response
envelopes and client sessions. Legacy remote compaction, WS/model-switch fallback and
full agent/client-authored metadata contracts are not included in this adaptation.

Legacy compaction in `models/legacy_compaction.py`, `context/legacy_compaction.py`,
`protocol/remote_history.py` and associated Runtime/window/media integration adapts
pinned Codex `core/src/{compact_remote,compact_remote_request,compact,event_mapping}.rs`,
`core/src/context/contextual_user_message.rs`, its fragment classifiers,
`codex-api/src/endpoint/{compact,session}.rs`, `http-client/src/transport.rs` and
`protocol/src/models.rs` under Apache-2.0. Normalized immutable replacement items are
distinct from the append-only window marker and original memory evidence. Python unary
HTTP attempts and bounded JSON parsing replace Rust transport/serde; full variant serde,
adjacent resize notices and model-switch fallback remain incomplete.

Turn-scoped HTTP routing in `models/turn_routing.py`, transport collectors and
GraphRunContext/window request integration adapts pinned Codex `core/src/client.rs`,
`core/src/session/turn.rs`, `core/src/compact*.rs` and
`codex-api/src/{sse/responses,endpoint/compact}.rs` under Apache-2.0. An explicit
in-process owner replaces the Rust client session's OnceLock for Corki's async HTTP
requests. No routing token is persisted or shared between logical Turns. WebSocket
reuse, prewarmed model sessions and model-switch fallback are not implemented here.

Remote-compaction item projection in `protocol/response_items.py` and its wire/
durable adapters adapts pinned Codex `protocol/src/models.rs`,
`protocol/src/models/{item_metadata,configuration_update}.rs`, `openai_models.rs`,
`codex-api/src/sse/responses.rs` and `core/src/compact_remote_v2.rs` under Apache-2.0.
Python validates known variants and projects their fields, including optional/null
serialization and host-owned metadata exclusions. This is not a complete Rust serde
implementation or a replacement for ordinary response collection or tool admission.

Checkpoint bootstrap contention handling in `core/checkpoint_lifecycle.py` is a
Corki/LangGraph-specific adapter repair informed by Codex
`state/src/sqlite.rs` and `state/src/runtime/recovery.rs` (Apache-2.0). It preserves
bounded busy handling, failure cleanup and the distinction between contention and
corruption; Codex does not use LangGraph or this Python bootstrap retry loop.

`protocol/wire_json.py`, `models/response_wire.py` and typed duplicate-field checks
adapt the Responses decoding contracts from Codex `codex-api/src/sse/responses.rs`
and `protocol/src/models.rs` (Apache-2.0), with behavior audited against serde_json
1.0.149 and serde/serde_derive 1.0.228 (MIT OR Apache-2.0). The upstream packages were
checksum-verified against Codex's Cargo.lock; they are not bundled Python dependencies.
The CLI's transitive arbitrary_precision feature is accounted for when distinguishing
typed i64 fields from JSON values; exact decimal lexeme roundtrip is not implemented.

Dedicated memory contracts in `memory/tool_contracts.py`, `memory/tools.py` and their
tool-executor/Code Mode integration adapt pinned Codex `ext/memories/src/tools/{mod,
read,list,search,ad_hoc_note}.rs`, `ext/memories/src/backend.rs`, and shared
`tools/src/{tool_executor,responses_api,code_mode,json_schema/types}.rs` under Apache-2.0.
Python typed parsing, namespace adapters and structured nested results replace Rust
serde/executor objects; this is not a complete TypeScript declaration renderer port.
The JSON-backed oversized-integer argument mapping in `protocol/tools.py` and the finite
checkpoint allowlist are Corki/LangGraph-specific storage adaptations, not Codex storage code.

Active-Step publication in `core/step_settings.py` and the model authority equality
projection in `protocol/model_authority.py` adapt pinned Codex
`core/src/session/{step_settings,step_activation}.rs`,
`protocol/src/openai_models/{guardian,guardian_v2}.rs`, and the associated tests.
Bundled authority values and policy SHA256 identities derive from `models-manager/models.json`
and `core/assets/guardian/{policy,policy_template,node_repl_policy}.md` at the same
ddf04ad26789d040f9ef6a96736f76602e35a6cc commit under Apache-2.0.
Digests compare authority metadata; Corki does not thereby implement Guardian review.
LangGraph synchronous checkpoint capture and async task ownership are Python adaptations.

Agent Plugin source parsing in `plugins/agent_*.py` and `plugins/manifest_path.py`
adapts pinned Codex `utils/plugins/src/plugin_namespace.rs`,
`core-plugins/src/{agent_plugin_manifest,agent_plugin_mcp_overlay,manifest,loader}.rs`
and `codex-mcp/src/agent_plugin_config.rs` under Apache-2.0. These are directory
discovery adapters, not marketplace installation or hook execution implementations.

The bundled `src/corki/_native/mcp_url.wasm` executes `url 2.5.8` with the dependency
versions/checksums recorded in `native/mcp_url/Cargo.lock`, matching pinned Codex.
License notices are reproduced in `_native/MCP_URL_LICENSES.txt`; the Rust 1.95.0
standard-library copyright notice is `_native/MCP_URL_RUST_COPYRIGHT.html`.
The no-import bridge and rebuild instructions are in `native/mcp_url` (Apache-2.0).
Agent endpoint policy follows Codex's parsed-host rules; Python keeps raw configuration
identity and passes canonical URLs only to the corresponding transport/redirect path.

`native/sandbox/patches/workspace-metadata.patch` modifies the exported copies of
OpenAI Codex `codex-rs/protocol/src/permissions.rs` and
`codex-rs/linux-sandbox/src/bwrap.rs` at commit
`ddf04ad26789d040f9ef6a96736f76602e35a6cc` to add Corki's `.corki` metadata name.
These are modified Apache-2.0 implementations, not unchanged upstream files; the
same upstream license and notice in `src/corki/skills/CODEX_CATALOG_LICENSE.txt`
apply. The reference checkout is not modified. Complete native dependency
redistribution notices and supported public distribution remain separate work;
the platform artifacts described here are local verification builds.

`native/sandbox/src/fs_helper.rs` adapts helper-only runtime permission derivation
and top-level alias normalization from pinned Codex
`codex-rs/exec-server/src/fs_sandbox.rs`, and nonblocking regular-file opening from
`codex-rs/exec-server/src/regular_file.rs`, under the same Apache-2.0 license.
The Python helper environment filter follows that executor's release allowlist.
The fixed read-only aggregate protocol, bounded reads and project-instruction
discovery adapter are Corki integration code, not the full native executor protocol.

The effective environment metadata bridge also compiles
`codex-rs/core/src/context/environment_context.rs` from the same pinned Codex
source under Apache-2.0. Its filesystem/network renderer is used locally; it does
not call a model provider or an official account service.

The configured patch helper in `native/sandbox/src/fs_patch.rs` invokes pinned
`codex-apply-patch` and `codex-exec-server::LOCAL_FS`, and the bridge directly compiles
`codex-rs/core/src/safety.rs` from the same reference export (Apache-2.0). Its host
policy-context capture adapts `protocol/src/permissions.rs` local home/tmp resolution.
The aggregate helper response and bounded committed-delta diagnostic are Corki
integration code; they are not the complete core approval or TurnDiffTracker flow.

Initial patch approval preparation, environment/file session cache and host routing
adapt pinned `core/src/tools/handlers/apply_patch.rs`, `tools/approvals.rs`,
`tools/sandboxing.rs` and `tools/orchestrator.rs` under Apache-2.0. The local
`patch_approval` transport kind, strict review decoder and two-process preparation/
execution boundary are Corki adapters; sandbox-denial upgrade and full committed
delta publication are not supplied by that initial-consent integration.

The committed patch record adapter maps the pinned `apply-patch/src/lib.rs`
`AppliedPatchDelta` and `AppliedPatchFileChange` contracts into ordered host JSON.
Its result/ledger/completion-event transport and unknown-output handling are Corki
integration code. The per-Turn accumulator and transient diff event semantics in
`core/src/turn_diff_tracker.rs`, `core/src/tools/events.rs` and `rollout/src/policy.rs`
inform the Turn-owned state adapter in `src/corki/core/turn_diff.py`. Its native
renderer in `native/sandbox/src/patch_diff.rs` includes the pinned tracker source
directly. Corki's ledger reconstruction, call-ID deduplication and bounded helper
transport are integration code, not a claim that native Codex persists TurnDiff.

## Local CLI file candidate search

`native/file_search` uses `ignore` 0.4.25 and `nucleo-matcher` 0.3.1 at commit
`4253de9faabb4e5c6d81d946a5e35a90f87347ee`, matching the local Codex file-search
dependency versions. The local JSON transport, bounded candidate storage and
Python CLI adapter are Corki implementation code; this does not import account,
cloud or model tool-search services.

The locked build collects dependency license/copyright texts into the generated
bundle's `THIRD_PARTY.txt` and the matching compiler distribution's standard
library notices into `RUST_COPYRIGHT.html`. Both files are checksummed and included
beside the executable in platform wheels. Source, build instructions and exact
transitive dependency versions are under `native/file_search`.
