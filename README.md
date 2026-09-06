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

Although `provider.api_key` is supported in TOML for local development,
`CORKI_API_KEY` is preferred. If a key is stored in the file, keep
`~/.corki/config.toml` private (mode `0600`) and never commit it.

## On-demand tool discovery

MCP tools now load on demand by default. The model first calls `tool_search`;
the harness ranks deferred metadata with BM25 and returns complete, budgeted
definitions. Ordinary function-calling providers receive only the discovered
schemas in subsequent requests. Built-in tools remain directly available.

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
again. A changed or removed definition does not authorize a replacement tool.
Python plugins can opt into discovery with `register_tool(..., exposure="deferred")`.

The current lexical tokenizer differs from Codex's English stemming/stopword
pipeline; exact ranking parity and the remaining harness work are tracked in
[the source-alignment audit](harness-alignment/audit.md).

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

At startup Corki leases a bounded number of completed, idle threads, extracts durable facts with a
separate model request, and performs singleton global consolidation into `~/.corki/memories`.
Normal turns receive only `memory_summary.md`; the model progressively searches `MEMORY.md` and
rollout summaries when relevant. This deliberately follows Codex's layered Markdown retrieval
instead of introducing an embedding/vector store. Memory generation failures are isolated from the
interactive turn, previous published artifacts remain usable, and cited source thread IDs update
usage ranking.

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
# corki
