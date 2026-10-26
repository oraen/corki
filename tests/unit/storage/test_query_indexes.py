"""Query plans, migration compatibility and indexed/unindexed result parity."""

import sqlite3
from contextlib import closing

import pytest

from corki.storage.query_indexes import INDEXES
from corki.storage.sqlite import SQLiteSessionRepository


@pytest.mark.parametrize(
    "index,sql,args",
    [
        ("threads_recency", "SELECT id FROM threads ORDER BY updated_at DESC,id LIMIT 40", ()),
        (
            "threads_active_recency",
            "SELECT id FROM threads WHERE archived_at IS NULL "
            "AND source!='custom:cron' ORDER BY updated_at DESC,id LIMIT 41 OFFSET 0",
            (),
        ),
        (
            "threads_active_cwd_recency",
            "SELECT id FROM threads WHERE archived_at IS NULL "
            "AND cwd=? ORDER BY updated_at DESC,id LIMIT 41 OFFSET 0",
            ("/work/1",),
        ),
        (
            "turns_thread_recency",
            "SELECT status FROM turns WHERE thread_id=? ORDER BY updated_at DESC LIMIT 1",
            ("t1",),
        ),
        (
            "tool_executions_recency",
            "SELECT call_id FROM tool_executions ORDER BY started_at DESC LIMIT 80",
            (),
        ),
        (
            "model_steps_created_jd",
            "SELECT date(created_at,'localtime') AS day, "
            "sum(input_tokens),sum(output_tokens) FROM model_steps "
            "WHERE julianday(created_at)>=julianday(?,'utc') "
            "AND julianday(created_at)<julianday(?,'utc') GROUP BY day",
            ("2026-09-26", "2026-10-26"),
        ),
    ],
)
def test_each_query_uses_its_index_and_preserves_results(tmp_path, index, sql, args):
    path = tmp_path / "sessions.db"
    repository = SQLiteSessionRepository(path)
    with repository._connect() as db:
        for i in range(180):
            thread = f"t{i}"
            date = f"2026-10-{i % 25 + 1:02d} 12:00:00"
            db.execute(
                "INSERT INTO threads(id,cwd,updated_at,archived_at) VALUES (?,?,?,?)",
                (thread, f"/work/{i % 3}", date, date if i % 4 == 0 else None),
            )
            db.execute(
                "INSERT INTO turns(id,thread_id,status,user_input,updated_at) VALUES (?,?,?,?,?)",
                (f"r{i}", thread, "completed", "fixture", date),
            )
            db.execute(
                "INSERT INTO tool_executions(call_id,thread_id,turn_id,tool_name,"
                "status,started_at) "
                "VALUES (?,?,?,?,?,?)",
                (f"c{i}", thread, f"r{i}", "fixture", "completed", date),
            )
            db.execute(
                "INSERT INTO model_steps(step_id,thread_id,turn_id,input_tokens,output_tokens,"
                "cached_tokens,reasoning_tokens,metadata_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (f"s{i}", thread, f"r{i}", i, 2, 0, 0, "{}", date),
            )
        plan = [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + sql, args)]
        assert any(index in row for row in plan), plan
        if index == "model_steps_created_jd":
            assert any("SEARCH model_steps USING INDEX" in row for row in plan), plan
        else:
            assert not any("TEMP B-TREE" in row for row in plan), plan
        indexed = [tuple(row) for row in db.execute(sql, args)]
        for name in INDEXES:
            db.execute(f"DROP INDEX {name}")
        baseline = [tuple(row) for row in db.execute(sql, args)]
        # Equal timestamps had no tie rule for tools/turns; compare those sets.
        if index == "tool_executions_recency":
            assert {row[0] for row in indexed} == {row[0] for row in baseline}
        else:
            assert indexed == baseline
    # An older existing database without the new indexes is upgraded on startup.
    SQLiteSessionRepository(path)
    with closing(sqlite3.connect(path)) as db:
        assert set(INDEXES) <= {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert db.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 180
