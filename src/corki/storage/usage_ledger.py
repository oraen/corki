"""Content-free, append-only consumption facts with independent retention.

Shares the session SQLite transaction, but has no foreign keys to disposable
history. Recovery still needs its own counters; analytics never reads that copy.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

VERSION = "usage-ledger-v1"


def install_usage_ledger(db, home):
    statements = [
        """CREATE TABLE IF NOT EXISTS usage_threads (
            thread_id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'session',
            channel TEXT, sender TEXT, job_id TEXT, run_id TEXT) WITHOUT ROWID""",
        """CREATE TABLE IF NOT EXISTS usage_events (
            step_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
            created_at TEXT NOT NULL, provider TEXT, model TEXT,
            input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
            cached_tokens INTEGER NOT NULL, reasoning_tokens INTEGER NOT NULL,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            rollout_budget_units REAL) WITHOUT ROWID""",
        "CREATE INDEX IF NOT EXISTS usage_events_time ON usage_events(julianday(created_at))",
        "CREATE INDEX IF NOT EXISTS usage_events_thread "
        "ON usage_events(thread_id,input_tokens,output_tokens)",
        "CREATE INDEX IF NOT EXISTS usage_threads_owner ON usage_threads(channel,sender,thread_id)",
        "CREATE INDEX IF NOT EXISTS usage_threads_run ON usage_threads(run_id,thread_id) "
        "WHERE run_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS usage_threads_job ON usage_threads(job_id,thread_id) "
        "WHERE job_id IS NOT NULL",
    ]
    for statement in statements:
        db.execute(statement)
    # A trigger also covers older live processes and SQL-based inserts. Rollback
    # of the model result rolls back its fact; retries cannot count twice.
    projection = """SELECT step_id,thread_id,turn_id,created_at,
        CASE WHEN json_valid(metadata_json) THEN
            json_extract(metadata_json,'$._corki_usage.provider') END,
        CASE WHEN json_valid(metadata_json) THEN
            coalesce(json_extract(metadata_json,'$._corki_usage.model'),
                     json_extract(metadata_json,'$.model')) END,
        input_tokens,output_tokens,cached_tokens,reasoning_tokens,
        CASE WHEN json_valid(usage_details_json) THEN
            coalesce(json_extract(usage_details_json,'$.cache_write_tokens'),0) ELSE 0 END,
        CASE WHEN json_valid(usage_details_json) THEN
            json_extract(usage_details_json,'$.codex_rollout_budget_units') END
        FROM model_steps"""
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS usage_event_insert AFTER INSERT ON model_steps
        BEGIN INSERT OR IGNORE INTO usage_events """
        + projection
        + " WHERE step_id=NEW.step_id; END"
    )
    source = """CASE
        WHEN source='cron' OR (json_valid(source) AND json_extract(source,'$.custom')='cron')
            THEN 'cron'
        WHEN source='gateway' OR (json_valid(source) AND json_extract(source,'$.custom')='gateway')
            THEN 'gateway' ELSE 'session' END"""
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS usage_thread_insert AFTER INSERT ON threads
        BEGIN INSERT OR IGNORE INTO usage_threads(thread_id,kind) SELECT id,"""
        + source
        + " FROM threads WHERE id=NEW.id; END"
    )
    if db.execute("SELECT 1 FROM schema_migrations WHERE name=?", (VERSION,)).fetchone():
        return
    # Bounded-memory SQL migration. The marker and copied rows commit together.
    db.execute(
        "INSERT OR IGNORE INTO usage_threads(thread_id,kind) SELECT id," + source + " FROM threads"
    )
    db.execute("INSERT OR IGNORE INTO usage_events " + projection)
    if home is not None:
        _migrate_owners(db, Path(home))
    for name in ("model_steps_created_jd", "model_steps_thread_usage"):
        db.execute("DROP INDEX IF EXISTS " + name)
    db.execute("INSERT INTO schema_migrations(name) VALUES (?)", (VERSION,))


def _migrate_owners(db, home):
    for alias, path, tables in [
        (
            "usage_gateway_migration",
            home / "gateway/gateway.db",
            ("user_usage_threads", "connections"),
        ),
        ("usage_cron_migration", home / "cron/cron.sqlite", ("runs",)),
    ]:
        if not path.is_file():
            continue
        db.execute(f"ATTACH DATABASE ? AS {alias}", (path.resolve().as_uri() + "?mode=ro",))
        available = {
            r[0] for r in db.execute(f"SELECT name FROM {alias}.sqlite_master WHERE type='table'")
        }
        if not set(tables) <= available:
            continue
        if "gateway" in alias:
            db.execute(f"""INSERT INTO usage_threads(thread_id,kind,channel,sender)
                SELECT u.thread,c.type_id,u.channel,u.sender FROM {alias}.user_usage_threads u
                JOIN {alias}.connections c ON c.id=u.channel WHERE 1
                ON CONFLICT(thread_id) DO UPDATE SET kind=excluded.kind,
                    channel=excluded.channel,sender=excluded.sender""")
        else:
            # One dimension row per thread, even with duplicate legacy references.
            db.execute(f"""INSERT INTO usage_threads(thread_id,kind,job_id,run_id)
                SELECT coalesce(nullif(session_id,''),id),'cron',min(job_id),min(id)
                FROM {alias}.runs GROUP BY coalesce(nullif(session_id,''),id)
                ON CONFLICT(thread_id) DO UPDATE SET kind='cron',
                    job_id=excluded.job_id,run_id=excluded.run_id""")


def register_usage_owner(
    database, thread, *, kind, channel=None, sender=None, job_id=None, run_id=None
):
    """Host-supplied scalar attribution, never prompts, names, credentials or paths."""
    if not Path(database).exists():
        return
    with closing(sqlite3.connect(database, timeout=10)) as db, db:
        db.execute(
            """INSERT INTO usage_threads(thread_id,kind,channel,sender,job_id,run_id)
            VALUES (?,?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET
            kind=excluded.kind,channel=coalesce(excluded.channel,usage_threads.channel),
            sender=coalesce(excluded.sender,usage_threads.sender),
            job_id=coalesce(excluded.job_id,usage_threads.job_id),run_id=coalesce(excluded.run_id,usage_threads.run_id)""",
            (str(thread), kind, channel, sender, job_id, run_id),
        )
