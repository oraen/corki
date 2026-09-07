# Third-party notices

Corki bundles selected instruction packages so a clean installation has a useful baseline.

- `skills/imagegen`, `openai-docs`, `plugin-creator`, `review-agent`, `skill-creator`, and
  `skill-installer` are derived from the OpenAI Codex source distribution. License files shipped
  with those packages remain in their respective directories.
- `skills/arxiv`, `grounded-citations`, `pdf`, and `xlsx` are derived from Hermes Agent by Nous
  Research and are distributed under the MIT license reproduced in `skills/HERMES_LICENSE.txt`.

The skill instructions have small Corki compatibility changes to paths, runtime names, and helper
scripts. Those changes do not alter the upstream license terms.

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
The ad-hoc note contracts in `src/corki/memory/{backend,tools}.py` and
`prompts/memory/consolidation.md` also adapt `codex-rs/ext/memories/src/{local,tools}/ad_hoc_note.rs`
and `codex-rs/memories/write/templates/extensions/ad_hoc/instructions.md` from that pinned
Apache-2.0 distribution. Corki's JSON consolidation path is a modified implementation,
not the native filesystem-editing consolidation agent.
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
