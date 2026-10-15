You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Preserve the original objective and accepted user corrections; a recent "continue" or status question is not a replacement objective. Distinguish completed work, work in progress, and remaining work. Record the next concrete action so the task can resume without restarting discovery.

For completed investigation, preserve the useful findings and their source paths or locations, along with any remaining uncertainty. For rubric-based work, retain the applicable scoring rules, weights, exceptions, and evidence needed for the remaining assessment without copying entire documents. Distinguish observed results from assumptions and planned checks. If an output was truncated or a required section was not read, state exactly what is still missing; do not describe a partial read as complete.

Record any changed or removed artifacts, their current state, and relevant backup paths. Preserve pending tool or process identifiers when needed to continue safely. Do not include secrets. A completed memory lookup with no hits is not evidence that this task has no previous work.

Be concise, structured, and focused on helping the next LLM seamlessly continue the work.
