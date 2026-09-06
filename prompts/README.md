# Corki prompt catalog

Prompt text is organized by when it becomes visible to the model.  The files
are modeled on the corresponding Codex CLI prompt layers, with Corki-specific
names and without Codex's approval, sandbox, multi-agent, plugin, or realtime
instructions.

## Normal coding turn

For a first user turn in Default mode, the harness supplies these layers:

| Order | Template | Model role | Condition |
| --- | --- | --- | --- |
| 1 | `agent/base.md` | API `instructions` | Always |
| 2 | `modes/default.md` | `developer` | Default collaboration mode |
| 3 | `context/agents.md` | `user` context | Applicable `AGENTS.md` exists |
| 4 | `context/environment.md` | `user` context | First turn or environment changed |
| 5 | none | `user` | The user's question is sent unchanged |

Tool descriptions are not Markdown prompts.  They are sent separately as tool
JSON schemas.  Tool results and previous messages are structured conversation
items rather than prompt templates.

## Other templates

- `modes/plan.md` replaces `modes/default.md` only in Plan mode.
- `tasks/compact.md` is used only when conversation history is summarized.

Templates use strict `#{variable_name}` placeholders.  Keep selection,
conditionals, and composition in Python rather than adding logic to Markdown.

## Reserved extension namespaces

The following namespaces are part of the prompt architecture even though their
templates are not implemented in the current milestone:

- `permissions/` for approval policy and sandbox context
- `multi_agent/` for orchestrator, role, and handoff instructions
- `extensions/plugins/` and `extensions/skills/` for discovered capabilities
- `realtime/` for realtime start, end, and delegation instructions

Do not create empty directories for these namespaces.  Add their files when the
owning feature is implemented, then expose them as `PromptContribution` objects
using the corresponding stable `PromptSlot`.
