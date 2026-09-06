# Harness demos

These demos use deterministic in-process model adapters. They need no API key and are intended to
prove the Corki harness wiring rather than model quality.

```bash
python examples/core_loop_demo.py
python examples/builtin_tools_demo.py
python examples/extensions_demo.py
```

- `core_loop_demo.py` exercises planning, shell, patch, verification, streamed runtime events,
  project context, and cross-turn memory.
- `builtin_tools_demo.py` invokes every default tool through the graph, including a resumable
  process and a multimodal image attachment.
- `extensions_demo.py` exercises a project skill, a Codex-format plugin, and a real stdio MCP
  process through the complete LangGraph model/tool loop.

Each program creates an isolated temporary workspace, raises an assertion if a recall invariant
fails, and prints one JSON summary on success. Keep examples deterministic and promote every
regression discovered here into `tests/integration` or `tests/e2e`.
