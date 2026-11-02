"""Indexes for bounded recency reads and timezone-independent time ranges."""

INDEXES = {
    "threads_recency": "threads(updated_at DESC, id)",
    "threads_active_recency": "threads(updated_at DESC, id) WHERE archived_at IS NULL",
    "threads_active_cwd_recency": ("threads(cwd, updated_at DESC, id) WHERE archived_at IS NULL"),
    "turns_thread_recency": "turns(thread_id, updated_at DESC)",
    "tool_executions_recency": "tool_executions(started_at DESC)",
    # Unlike date(..., 'localtime'), julianday(timestamp) is deterministic and
    # preserves existing support for SQLite, ISO-8601 and offset timestamps.
    "model_steps_created_jd": "model_steps(julianday(created_at))",
    "model_steps_thread_usage": "model_steps(thread_id,input_tokens,output_tokens)",
}


def install_query_indexes(connection):
    for name, expression in INDEXES.items():
        connection.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {expression}")
