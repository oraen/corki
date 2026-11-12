"""Reply wall durations are durable, scoped and independent of model/tool output."""

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from state import DesktopState


def test_reply_timing_backfill_terminal_states_cache_and_delete(tmp_path):
    database = tmp_path / "core.db"
    state = DesktopState(tmp_path)
    with closing(sqlite3.connect(database)) as db, db:
        db.executescript("""
          CREATE TABLE chat_turns(id,thread_id,status,updated_at);
          CREATE TABLE chat_history(thread_id,turn_id,kind,payload_json,sequence);
        """)
        for i, status in enumerate(("completed", "cancelled", "failed", "running")):
            turn = str(i)
            db.execute(
                "INSERT INTO chat_turns VALUES(?,?,?,?)", (turn, "thread", status, "2026-10-29 02:33:13")
            )
            for sequence, stamp in (
                (1, "2026-10-29T00:00:00+00:00"),
                (2, "2026-10-29T01:00:00+00:00"),
            ):
                db.execute(
                    "INSERT INTO chat_history VALUES(?,?,?,?,?)",
                    ("thread", turn, "user_message", json.dumps({"created_at": stamp}), sequence),
                )
        db.execute("INSERT INTO chat_turns VALUES('unknown','thread','completed','invalid')")
    timings = state.reply_timings(database, "thread", {"0", "1", "2", "3", "unknown"})
    assert set(timings) == {"0", "1", "2"}
    assert all(t["replyElapsedSeconds"] == 9193 for t in timings.values())
    assert state.reply_timings(database, "other", {"0"}) == {}
    # A persisted timing does not re-query a changed core timestamp on every page read.
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE chat_turns SET updated_at='2026-10-30 00:00:00'")
    assert state.reply_timings(database, "thread", {"0", "1", "2"}) == timings
    assert DesktopState(tmp_path).reply_timings(database, "thread", {"0", "1", "2"}) == timings
    state.delete_thread("thread")
    with closing(state.connect()) as db:
        assert db.execute("SELECT count(*) FROM reply_timings").fetchone()[0] == 0
