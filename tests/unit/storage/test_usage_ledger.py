"""Consumption survives deletion; migration and insertion share transaction boundaries."""

import asyncio
import json
import sqlite3
from contextlib import closing

from corki.storage.sqlite import SQLiteSessionRepository
from corki.storage.thread_delete import SQLiteThreadDeleteStore
from corki.storage.usage_ledger import VERSION, register_usage_owner


def insert(db, key="step", thread="thread", tokens=123):
    db.execute("INSERT OR IGNORE INTO threads(id,cwd) VALUES (?,?)", (thread, "fixture"))
    db.execute(
        """INSERT INTO model_steps(step_id,thread_id,turn_id,input_tokens,output_tokens,
        cached_tokens,reasoning_tokens,metadata_json,usage_details_json)
        VALUES (?,?,?, ?,7,50,3,?,?)""",
        (
            key,
            thread,
            "turn",
            tokens,
            json.dumps(
                {
                    "_corki_usage": {"provider": "fixture", "model": "model"},
                    "ignored": "SECRET_PAYLOAD_MUST_NOT_ENTER_LEDGER",
                }
            ),
            json.dumps({"cache_write_tokens": 4}),
        ),
    )


def test_atomic_append_rollback_no_duplicates_and_delete(tmp_path):
    path = tmp_path / "sessions/corki.db"
    SQLiteSessionRepository(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("BEGIN")
        insert(db)
        db.rollback()
        assert db.execute("SELECT count(*) FROM usage_events").fetchone()[0] == 0
        insert(db)
        db.commit()
        before = db.execute("SELECT * FROM usage_events").fetchall()
        assert len(before) == 1 and "SECRET_PAYLOAD" not in str(before)
        assert db.execute("SELECT cache_write_tokens FROM usage_events").fetchone()[0] == 4
        register_usage_owner(path, "thread", kind="dingtalk", channel="account", sender="opaque")
        asyncio.run(SQLiteThreadDeleteStore(path).delete("thread"))
        assert db.execute("SELECT count(*) FROM model_steps").fetchone()[0] == 0
        assert db.execute("SELECT * FROM usage_events").fetchall() == before
        assert db.execute("SELECT sender FROM usage_threads").fetchone()[0] == "opaque"
        # A recreated recovery row with the same step identity cannot double-charge.
        insert(db)
        db.commit()
        assert db.execute("SELECT count(*) FROM usage_events").fetchone()[0] == 1


def test_legacy_migration_with_deleted_content_and_ownership(tmp_path):
    path = tmp_path / "sessions/corki.db"
    SQLiteSessionRepository(path)
    with closing(sqlite3.connect(path)) as db, db:
        for table in ["usage_events", "usage_threads"]:
            db.execute("DROP TABLE " + table)
        for trigger in ["usage_event_insert", "usage_thread_insert"]:
            db.execute("DROP TRIGGER " + trigger)
        db.execute("DELETE FROM schema_migrations WHERE name=?", (VERSION,))
        insert(db)
    external = tmp_path / "gateway/gateway.db"
    external.parent.mkdir()
    with closing(sqlite3.connect(external)) as db, db:
        db.executescript(
            "CREATE TABLE user_usage_threads(thread TEXT,channel TEXT,sender TEXT);"
            "CREATE TABLE connections(id TEXT,type_id TEXT);"
            "INSERT INTO connections VALUES ('account','dingtalk');"
            "INSERT INTO user_usage_threads VALUES ('thread','account','opaque');"
        )
    cron = tmp_path / "cron/cron.sqlite"
    cron.parent.mkdir()
    with closing(sqlite3.connect(cron)) as db, db:
        db.executescript(
            "CREATE TABLE runs(id TEXT,job_id TEXT,session_id TEXT);"
            "INSERT INTO runs VALUES ('run','job','scheduled-thread');"
        )
    SQLiteSessionRepository(path)
    SQLiteSessionRepository(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM usage_events").fetchone()[0] == 1
        assert db.execute(
            "SELECT kind,channel,sender FROM usage_threads WHERE thread_id='thread'"
        ).fetchone() == ("dingtalk", "account", "opaque")
        assert db.execute(
            "SELECT kind,job_id,run_id FROM usage_threads WHERE thread_id='scheduled-thread'"
        ).fetchone() == ("cron", "job", "run")
        assert db.execute("PRAGMA foreign_key_list(usage_events)").fetchall() == []
        assert db.execute("PRAGMA foreign_key_list(usage_threads)").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_query_indexes_and_compact_schema(tmp_path):
    path = tmp_path / "sessions/corki.db"
    SQLiteSessionRepository(path)
    with closing(sqlite3.connect(path)) as db, db:
        for i in range(2000):
            insert(db, str(i), str(i // 10))
        for query, index in [
            (
                "SELECT sum(input_tokens) FROM usage_events "
                "WHERE julianday(created_at)>julianday('2020-01-01')",
                "usage_events_time",
            ),
            (
                "SELECT sum(input_tokens+output_tokens) FROM usage_events WHERE thread_id='1'",
                "usage_events_thread",
            ),
            (
                "SELECT thread_id FROM usage_threads WHERE channel='account' AND sender='opaque'",
                "usage_threads_owner",
            ),
            ("SELECT thread_id FROM usage_threads WHERE job_id='job'", "usage_threads_job"),
            ("SELECT thread_id FROM usage_threads WHERE run_id='run'", "usage_threads_run"),
        ]:
            plan = " ".join(str(r) for r in db.execute("EXPLAIN QUERY PLAN " + query))
            assert index in plan, plan
        columns = {r[1] for r in db.execute("PRAGMA table_info(usage_events)")}
        assert not columns & {"items_json", "metadata_json", "prompt", "response", "content"}
        assert db.execute("SELECT sum(input_tokens) FROM usage_events").fetchone()[0] == 2000 * 123


def test_failed_migration_rolls_back_marker_and_can_retry(tmp_path, monkeypatch):
    import pytest

    from corki.storage import usage_ledger

    path = tmp_path / "sessions/corki.db"
    SQLiteSessionRepository(path)
    with closing(sqlite3.connect(path)) as db, db:
        insert(db)
        db.execute("DELETE FROM usage_events")
        db.execute("DELETE FROM schema_migrations WHERE name=?", (VERSION,))
    original = usage_ledger._migrate_owners

    def fail(*args):
        raise RuntimeError("fixture migration failure")

    monkeypatch.setattr(usage_ledger, "_migrate_owners", fail)
    with pytest.raises(RuntimeError, match="fixture migration failure"):
        SQLiteSessionRepository(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM usage_events").fetchone()[0] == 0
        assert (
            db.execute(
                "SELECT count(*) FROM schema_migrations WHERE name=?", (VERSION,)
            ).fetchone()[0]
            == 0
        )
    monkeypatch.setattr(usage_ledger, "_migrate_owners", original)
    SQLiteSessionRepository(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM usage_events").fetchone()[0] == 1


def test_concurrent_bootstrap_and_writes_do_not_duplicate_facts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "sessions/corki.db"

    def writer(worker):
        SQLiteSessionRepository(path)
        with closing(sqlite3.connect(path, timeout=10)) as db, db:
            for number in range(50):
                insert(db, f"{worker}-{number}", f"thread-{worker}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(writer, range(4)))
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*),sum(input_tokens) FROM usage_events").fetchone() == (
            200,
            200 * 123,
        )
        assert db.execute("SELECT count(*) FROM model_steps").fetchone()[0] == 200
