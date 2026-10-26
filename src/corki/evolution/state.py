"""Learning counters and reversible edit evidence, outside the conversation DB."""

import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


class EvolutionState:
    def __init__(self, home: Path):
        self.root = home / "evolution"
        self.path = self.root / "evolution.sqlite"

    @contextmanager
    def database(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.path, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            db.executescript("""
                CREATE TABLE IF NOT EXISTS counters (
                    thread TEXT PRIMARY KEY, steps INTEGER NOT NULL DEFAULT 0,
                    turns INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS completed (
                    turn TEXT PRIMARY KEY, thread TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY, thread TEXT, turn TEXT, model TEXT,
                    provider TEXT, status TEXT, started_at TEXT, ended_at TEXT,
                    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, error TEXT);
                CREATE TABLE IF NOT EXISTS mutations (
                    id TEXT PRIMARY KEY, skill TEXT, file TEXT, before_text TEXT,
                    after_text TEXT, origin TEXT, review TEXT, created_at TEXT,
                    status TEXT NOT NULL DEFAULT 'prepared');
            """)
            if "error" not in {row[1] for row in db.execute("PRAGMA table_info(reviews)")}:
                db.execute("ALTER TABLE reviews ADD COLUMN error TEXT")
            os.chmod(self.path, 0o600)
            with db:
                yield db

    def completed_turn(self, thread, turn, steps, *, authored=False):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "INSERT OR IGNORE INTO completed VALUES (?,?)", (turn, thread)
            ).rowcount:
                return False
            db.execute("INSERT OR IGNORE INTO counters(thread) VALUES (?)", (thread,))
            db.execute(
                "UPDATE counters SET steps=CASE WHEN ? THEN 0 ELSE steps+? END, "
                "turns=CASE WHEN ? THEN 0 ELSE turns+1 END WHERE thread=?",
                (authored, steps, authored, thread),
            )
            row = db.execute(
                "SELECT steps,turns FROM counters WHERE thread=?", (thread,)
            ).fetchone()
            db.execute(
                "DELETE FROM completed WHERE thread=? AND rowid NOT IN "
                "(SELECT rowid FROM completed WHERE thread=? ORDER BY rowid DESC LIMIT 1000)",
                (thread, thread),
            )
            return row["steps"] >= 10 or row["turns"] >= 10

    def begin_review(self, thread, turn, settings):
        identity = str(uuid.uuid4())
        with self.database() as db:
            # Caller holds review.lock, so older running records lost their owner.
            db.execute(
                "UPDATE reviews SET status='interrupted',ended_at=? WHERE status='running'",
                (now(),),
            )
            db.execute(
                "INSERT INTO reviews(id,thread,turn,model,provider,status,started_at) "
                "VALUES (?,?,?,?,?,'running',?)",
                (identity, thread, turn, settings.model, settings.provider_id, now()),
            )
            db.execute("UPDATE counters SET steps=0,turns=0 WHERE thread=?", (thread,))
            db.execute(
                "DELETE FROM reviews WHERE rowid NOT IN "
                "(SELECT rowid FROM reviews ORDER BY rowid DESC LIMIT 200)"
            )
        return identity

    def finish_review(self, identity, status, input_tokens=0, output_tokens=0, error=None):
        with self.database() as db:
            db.execute(
                "UPDATE reviews SET status=?,ended_at=?,input_tokens=?,output_tokens=?,error=? "
                "WHERE id=?",
                (status, now(), input_tokens, output_tokens, error, identity),
            )

    def prepare_edit(self, name, file, before, after, origin, review):
        identity = str(uuid.uuid4())
        with self.database() as db:
            db.execute(
                "INSERT INTO mutations "
                "(id,skill,file,before_text,after_text,origin,review,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (identity, name, file, before, after, origin, review, now()),
            )
            # Failed/cancelled publications also leave useful evidence. Bound it
            # here, rather than retaining unlimited 'prepared' rows on failures.
            db.execute(
                "DELETE FROM mutations WHERE skill=? AND file=? AND rowid NOT IN "
                "(SELECT rowid FROM mutations WHERE skill=? AND file=? "
                "ORDER BY rowid DESC LIMIT 20)",
                (name, file, name, file),
            )
        return identity

    def applied(self, identity):
        with self.database() as db:
            db.execute("UPDATE mutations SET status='applied' WHERE id=?", (identity,))
            row = db.execute("SELECT skill,file FROM mutations WHERE id=?", (identity,)).fetchone()
            db.execute(
                "DELETE FROM mutations WHERE skill=? AND file=? AND rowid NOT IN "
                "(SELECT rowid FROM mutations WHERE skill=? AND file=? "
                "ORDER BY rowid DESC LIMIT 20)",
                (*row, *row),
            )

    def history(self, name, file):
        with self.database() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,created_at,origin,status FROM mutations WHERE skill=? AND file=? "
                    "ORDER BY rowid DESC LIMIT 20",
                    (name, file),
                )
            ]

    def prior_content(self, identity, name, file):
        with self.database() as db:
            row = db.execute(
                "SELECT before_text FROM mutations WHERE id=? AND skill=? AND file=?",
                (identity, name, file),
            ).fetchone()
        if row is None or row[0] is None:
            raise ValueError("此版本没有可恢复的旧内容")
        return row[0]

    def review_edit_count(self, identity):
        with self.database() as db:
            return db.execute(
                "SELECT COUNT(*) FROM mutations WHERE review=? AND status='applied'", (identity,)
            ).fetchone()[0]

    def status(self):
        if not self.path.exists():
            return {"reviews": [], "edits": 0}
        with self.database() as db:
            reviews = [
                dict(row)
                for row in db.execute("SELECT * FROM reviews ORDER BY rowid DESC LIMIT 20")
            ]
            edits = db.execute("SELECT COUNT(*) FROM mutations WHERE status='applied'").fetchone()[
                0
            ]
        return {"reviews": reviews, "edits": edits}
