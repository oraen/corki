"""Execution durations come from the scoped core ledger, not tool output text."""

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from content import execution_timings


def test_execution_timings_are_scoped_durable_and_allow_running_entries(tmp_path):
    database = tmp_path / "ledger.db"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "CREATE TABLE tool_executions(call_id,thread_id,turn_id,started_at,completed_at)"
        )
        db.executemany(
            "INSERT INTO tool_executions VALUES(?,?,?,?,?)",
            [
                ("done", "thread", "turn", "2026-01-01 00:00:00", "2026-01-01 02:03:21"),
                ("running", "thread", "turn", "2026-01-01 00:00:00", None),
                ("foreign", "other", "turn", "2026-01-01 00:00:00", "2026-01-01 00:00:10"),
            ],
        )
    result = execution_timings(database, "thread", {"done", "running", "foreign", "absent"})
    assert result[("turn", "done")]["elapsedSeconds"] == 7401
    assert result[("turn", "running")]["startedAt"] > 0
    assert "elapsedSeconds" not in result[("turn", "running")]
    assert len(result) == 2
    assert execution_timings(database, "thread", set()) == {}
