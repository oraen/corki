# Chat history and model context

Corki keeps two independent copies of message data in the session SQLite database.
They share session and turn identities, not message bodies or cross-store foreign
keys. A single database permits atomic event admission without distributed commits.

- `chat_history` contains original user messages, assistant messages and tool events,
  including their display content and attachment metadata. Its own per-thread order
  and primary key support bounded forward/reverse navigation. No read joins to model
  messages, compaction markers or retained copies are needed.
- `chat_turns` contains independent turn presentation facts (status, error and time).
- `conversation_items` remains the model history. Context selection, summaries,
  retained instructions, retries and checkpoints keep their existing semantics.
- Usage accounting and execution timing remain separate operational ledgers.

Original events are admitted to both stores in the same transaction. Ingest triggers
also cover still-running older writers. Internal compaction writes only update model
history; the ingest state distinguishes replacement batches, including batches split
by recovery. Display queries never consult that state. Display order is allocated
independently, so deleting model messages cannot reuse or collide with display order.
Deleting or updating a model message does not rewrite the displayed original. There
is intentionally no model-delete cascade into chat history. Explicit physical session
deletion removes both stores, while preserving independent usage accounting.

## Migration and rollback

Initialization backfills original transcript payloads once, using the old replacement
markers to exclude internal copies. DDL, body copies, turn facts, triggers and migration
versions commit together. Invalid legacy replacement metadata aborts migration without
a partially published store. Original model history is neither deleted nor rewritten.
Read-only viewers of an unmigrated database ask for initialization instead of silently
falling back to model history. New display readers require migration before deployment.

Keep a consistent SQLite backup before migrating existing user data. Retain it and the
old data until the user confirms the result. No automatic deletion or VACUUM is part
of this migration. Independent bodies deliberately consume additional disk space;
only normal display keys are maintained, with no full-text index or duplicate search
index. Backups contain private user data and belong outside the source repository.

For deployment, migrate the database first, then refresh desktop bridge and gateway
readers. Restart processes to load the new reader contracts. CLI replay, transcript
browser, desktop message details, attachments, gateway core history and evolution
transcripts use the display store. Forking and model recovery explicitly use model
history; display snapshot APIs no longer expose internal context or summaries.
