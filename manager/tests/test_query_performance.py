"""Exercise production manager SQL against isolated, indexed session databases."""

import importlib.util
import os
import sqlite3
import time
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from corki.storage.sqlite import SQLiteSessionRepository

spec = importlib.util.spec_from_file_location(
    "query_manager", Path(__file__).parents[1] / "server.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_store(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.json").write_text("{}")
    store = module.Store(home, tmp_path / "work", tmp_path / "user")
    SQLiteSessionRepository(store.database)
    return store


@pytest.mark.parametrize("zone", ["UTC", "Asia/Shanghai", "America/New_York"])
def test_monthly_query_uses_range_index_and_keeps_local_date_semantics(tmp_path, monkeypatch, zone):
    if not hasattr(time, "tzset"):
        pytest.skip("Changing the process timezone requires tzset")
    previous = os.environ.get("TZ")
    monkeypatch.setenv("TZ", zone)
    time.tzset()
    try:
        store = make_store(tmp_path)
        # Includes the November DST transition and different persisted timestamp formats.
        dates = [
            "2026-10-02 23:59:59",
            "2026-10-03 00:00:00",
            "2026-10-03T04:00:00Z",
            "2026-10-03T08:00:00+08:00",
            "2026-11-01 00:00:00",
            "2026-11-02T04:59:59Z",
            "2026-11-02T05:00:00Z",
            "2026-11-02 23:59:59",
        ]
        with closing(sqlite3.connect(store.database)) as db, db:
            db.execute("INSERT INTO threads(id,cwd) VALUES ('t','work')")
            for i, timestamp in enumerate(dates):
                db.execute(
                    "INSERT INTO model_steps(step_id,thread_id,turn_id,input_tokens,output_tokens,"
                    "cached_tokens,reasoning_tokens,metadata_json,created_at) "
                    "VALUES (?,'t','turn',?,2,0,0,'{}',?)",
                    (str(i), i + 1, timestamp),
                )
        captured = []
        query = store.query

        def record(sql, args=()):
            captured.append((sql, args))
            return query(sql, args)

        store.query = record
        rows = store.monthly_usage(date(2026, 11, 1))
        sql, args = captured[-1]
        expected = query(
            "SELECT date(created_at,'localtime') AS day,sum(input_tokens) AS input,"
            "sum(output_tokens) AS output,count(*) AS steps FROM model_steps "
            "WHERE date(created_at,'localtime') BETWEEN ? AND ? GROUP BY day",
            ("2026-10-03", "2026-11-01"),
        )
        assert [row for row in rows if row["steps"]] == expected
        assert len(rows) == 30
        plan = query("EXPLAIN QUERY PLAN " + sql, args)
        assert any(
            "SEARCH usage_events USING INDEX usage_events_time" in row["detail"] for row in plan
        ), plan
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def test_snapshot_production_recency_queries_use_indexes(tmp_path):
    store = make_store(tmp_path)
    captured = []
    query = store.query

    def record(sql, args=()):
        captured.append((sql, args))
        return query(sql, args)

    store.query = record
    store.snapshot()
    matched = set()
    for sql, args in captured:
        expected = (
            {"threads_recency", "turns_thread_recency"}
            if "FROM threads t ORDER BY" in sql
            else {"tool_executions_recency"}
            if "FROM tool_executions ORDER BY" in sql
            else set()
        )
        if expected:
            plan = query("EXPLAIN QUERY PLAN " + sql, args)
            detail = "\n".join(row["detail"] for row in plan)
            assert all(name in detail for name in expected), detail
            assert "TEMP B-TREE" not in detail
            matched.update(expected)
    assert matched == {"threads_recency", "turns_thread_recency", "tool_executions_recency"}
