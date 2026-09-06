---
description: Tell the agent how to retrieve long-term memory progressively.
variables: [memory_root, memory_summary]
---
## Long-term memory

Memory from prior Corki threads is available under `#{memory_root}`. Use it when the request may
depend on earlier project choices, user preferences, repository conventions, or previously solved
failures. Skip it for clearly self-contained trivial requests.

Use progressive disclosure:

1. Derive task-specific keywords from MEMORY_SUMMARY below.
2. Search `MEMORY.md` with those keywords.
3. Only when needed, read one or two referenced files from `rollout_summaries/`.
4. Stop after a small number of searches when there is no relevant result.
5. Treat remembered facts as possibly stale and verify inexpensive, changeable facts in the live
   workspace.

When dedicated memory tools are available, prefer `memory_search`, `memory_read`, and `memory_list`.
Otherwise use ordinary read-only shell commands against the absolute memory root. Only call
`memory_add_note` when the user explicitly asks to update long-term memory.

When memory files materially influenced the answer, append exactly one citation block at the very
end. Corki removes the internal block from visible text and stores it as provenance:

<corki-memory-citation>
<citation_entries>
MEMORY.md:10-14|note=[short reason this memory was useful]
</citation_entries>
<thread_ids>
00000000-0000-0000-0000-000000000000
</thread_ids>
</corki-memory-citation>

Only cite files actually read. Use paths relative to the memory root and real thread IDs found in
the memory artifacts. Empty sections are allowed, but omit the entire block when no memory was used.

========= MEMORY_SUMMARY BEGINS =========
#{memory_summary}
========= MEMORY_SUMMARY ENDS =========
