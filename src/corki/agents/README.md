# Child agents

The default root runtime advertises `spawn_agent`, `wait_agents`,
`send_agent_message`, `list_agents`, `stop_agent`, and `close_agent`.
CLI, desktop and gateway roots use the same implementation. Explicit custom
registries and auxiliary/ephemeral runtimes do not acquire delegation tools.

A child is a separate LangGraphRuntime and durable thread, tagged with its parent
and depth. Its transcript and model usage use the existing session database.
The parent supplies the task context explicitly; there is no implicit transcript
fork. Workers share the working directory, so parallel edits need coordination.
They inherit the parent's model settings, effective execution permissions, live
execution rules, user instructions, request guidance, and MCP requirements.
Children retain normal Skills, tools and context compaction. They do not start
another level of children or independent self-evolution/background memory jobs.

Limits: six resident children per root, 100000 characters per submitted task,
and 60 seconds per wait. Waits use events, not polling. Timeout leaves work alive.
Close releases a slot. Stop closes owned execution resources; follow-up can reopen
the same durable child thread. Parent interruption/failure stops active children;
parent runtime shutdown closes all children. A normal parent turn does not by
itself cancel children. Roots must wait for needed results before completing.

Status and live routing are process-local. Restart does not automatically replay
child tasks; durable transcripts remain available in the ordinary session store.
This version requires explicit wait/list to collect results, rather than injecting
unsolicited completion messages. It does not automatically load third-party agent
frontmatter as host configuration. Specialist instructions can be read as skill
resources. A worker identity developer message explains cross-host shell tool
names without changing permissions.

No automatic approval is granted. Child execution requiring host consent returns
the existing approval-unavailable error; MCP requests without an attached host
are declined. User-input requests stop the worker with `needs_input` and return the
questions to the parent. The parent must handle the user interaction and explicitly
continue. Direct forwarding of child approval dialogs is not implemented.

Reference: local Codex agent/control/{spawn_guard,watch,mailbox,completion,
user_authorization}. Adopted independent sessions, owned startup/cleanup, bounded
admission, explicit authority inheritance and terminal status observation.
Full agent trees, durable routing/resume and automatic mailbox notifications are
outside this initial implementation.
