"""Learning counters and reversible edit evidence, outside the conversation DB."""

import hashlib
import json
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
                CREATE TABLE IF NOT EXISTS skill_usage (
                    path TEXT PRIMARY KEY, name TEXT NOT NULL, created_by TEXT,
                    created_at TEXT NOT NULL, last_activity_at TEXT,
                    view_count INTEGER NOT NULL DEFAULT 0, patch_count INTEGER NOT NULL DEFAULT 0,
                    pinned INTEGER NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'active');
                CREATE TABLE IF NOT EXISTS content_blobs (
                    hash TEXT PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS maintenance (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pending_writes (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, origin TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS skill_archives (
                    id TEXT PRIMARY KEY, path TEXT NOT NULL, archive TEXT NOT NULL,
                    name TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            if "error" not in {row[1] for row in db.execute("PRAGMA table_info(reviews)")}:
                db.execute("ALTER TABLE reviews ADD COLUMN error TEXT")
            if "policy_version" not in {
                row[1] for row in db.execute("PRAGMA table_info(mutations)")
            }:
                db.execute("ALTER TABLE mutations ADD COLUMN policy_version INTEGER DEFAULT 1")
            columns = {row[1] for row in db.execute("PRAGMA table_info(mutations)")}
            for field in ("before_hash", "after_hash"):
                if field not in columns:
                    db.execute(f"ALTER TABLE mutations ADD COLUMN {field} TEXT")
            os.chmod(self.path, 0o600)
            with db:
                yield db

    def completed_turn(self, thread, turn, steps, *, authored=False, interval=10):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "INSERT OR IGNORE INTO completed VALUES (?,?)", (turn, thread)
            ).rowcount:
                return False
            db.execute("INSERT OR IGNORE INTO counters(thread) VALUES (?)", (thread,))
            db.execute(
                "UPDATE counters SET steps=CASE WHEN ? THEN ? ELSE steps+? END, "
                "turns=turns+1 WHERE thread=?",
                (authored, steps, steps, thread),
            )
            row = db.execute(
                "SELECT steps,turns FROM counters WHERE thread=?", (thread,)
            ).fetchone()
            db.execute(
                "DELETE FROM completed WHERE thread=? AND rowid NOT IN "
                "(SELECT rowid FROM completed WHERE thread=? ORDER BY rowid DESC LIMIT 1000)",
                (thread, thread),
            )
            due = interval > 0 and row["steps"] >= interval
            if due:
                db.execute("UPDATE counters SET steps=0 WHERE thread=?", (thread,))
            return due

    def begin_review(self, thread, turn, settings):
        identity = str(uuid.uuid4())
        with self.database() as db:
            # Caller holds review.lock, so older running records lost their owner.
            db.execute(
                "UPDATE reviews SET status='interrupted',ended_at=? "
                "WHERE status='running' AND thread=?",
                (now(), thread),
            )
            db.execute(
                "INSERT INTO reviews(id,thread,turn,model,provider,status,started_at) "
                "VALUES (?,?,?,?,?,'running',?)",
                (identity, thread, turn, settings.model, settings.provider_id, now()),
            )
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
            hashes = []
            for text in (before, after):
                digest = hashlib.sha256(text.encode()).hexdigest() if text is not None else None
                if digest is not None:
                    db.execute(
                        "INSERT OR IGNORE INTO content_blobs VALUES (?,?,?)", (digest, text, now())
                    )
                hashes.append(digest)
            db.execute(
                "INSERT INTO mutations "
                "(id,skill,file,before_hash,after_hash,origin,review,created_at,policy_version) "
                "VALUES (?,?,?,?,?,?,?,?,2)",
                (identity, name, file, *hashes, origin, review, now()),
            )
            self._trim_ledger(db)
        return identity

    def applied(self, identity):
        with self.database() as db:
            db.execute("UPDATE mutations SET status='applied' WHERE id=?", (identity,))

    def _trim_ledger(self, db):
        # Hermes skill_ledger: 5 MiB metadata ledger; content blobs deduplicated separately.
        rows = db.execute(
            "SELECT rowid,length(coalesce(skill,''))+length(coalesce(file,''))+"
            "length(coalesce(origin,''))+400 AS bytes FROM mutations ORDER BY rowid DESC"
        )
        size, cutoff = 0, None
        for row in rows:
            size += row["bytes"]
            if size > 5 * 1024 * 1024:
                cutoff = row["rowid"]
                break
        if cutoff is not None:
            db.execute("DELETE FROM mutations WHERE rowid<=?", (cutoff,))
        db.execute(
            "DELETE FROM content_blobs WHERE julianday(created_at)<julianday('now','-1 hour') "
            "AND hash NOT IN (SELECT before_hash FROM mutations WHERE before_hash IS NOT NULL "
            "UNION SELECT after_hash FROM mutations WHERE after_hash IS NOT NULL)"
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
                "SELECT coalesce(before_text,(SELECT content FROM content_blobs "
                "WHERE hash=mutations.before_hash)) FROM mutations "
                "WHERE id=? AND skill=? AND file=?",
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

    def skill_locked(self, path):
        return any(row["path"] == path for row in self.locked_skills())

    def locked_skills(self):
        if not self.path.exists():
            return []
        with closing(
            sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
        ) as db:
            db.row_factory = sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='skill_usage'").fetchone():
                return []
            return [
                dict(row) for row in db.execute("SELECT name,path FROM skill_usage WHERE locked=1")
            ]

    def record_skill_activity(self, path, name, *, action="view", agent_created=False):
        """Hermes skill_usage: foreground creates belong to the user, review creates to agent."""
        timestamp = now()
        with self.database() as db:
            db.execute(
                "INSERT OR IGNORE INTO skill_usage(path,name,created_by,created_at) "
                "VALUES (?,?,?,?)",
                (path, name, "agent" if agent_created else "user", timestamp),
            )
            db.execute(
                "UPDATE skill_usage SET name=?,last_activity_at=?,state='active',"
                "view_count=view_count+?,patch_count=patch_count+? WHERE path=?",
                (name, timestamp, action == "view", action not in {"view", "create"}, path),
            )

    def ensure_skill_usage(self, path, name):
        with self.database() as db:
            db.execute(
                "INSERT OR IGNORE INTO skill_usage(path,name,created_by,created_at) "
                "VALUES (?,?,'user',?)",
                (path, name, now()),
            )

    def set_skill_flags(self, path, *, locked=None, pinned=None):
        """User maintenance API; never offered to the autonomous model."""
        with self.database() as db:
            for key, value in (("locked", locked), ("pinned", pinned)):
                if value is not None:
                    db.execute(f"UPDATE skill_usage SET {key}=? WHERE path=?", (bool(value), path))

    def usage(self):
        with self.database() as db:
            return [dict(row) for row in db.execute("SELECT * FROM skill_usage")]

    def maintenance_value(self, key, default=None):
        with self.database() as db:
            row = db.execute("SELECT value FROM maintenance WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def save_maintenance(self, key, value):
        with self.database() as db:
            db.execute("INSERT OR REPLACE INTO maintenance VALUES (?,?)", (key, json.dumps(value)))

    def stage(self, payload, *, background=False):
        identity = str(uuid.uuid4())
        with self.database() as db:
            db.execute(
                "INSERT INTO pending_writes VALUES (?,?,?,?)",
                (identity, json.dumps(payload), "review" if background else "foreground", now()),
            )
        return identity

    def pending(self):
        with self.database() as db:
            return [
                dict(row) for row in db.execute("SELECT * FROM pending_writes ORDER BY created_at")
            ]

    def remove_pending(self, identity):
        with self.database() as db:
            db.execute("DELETE FROM pending_writes WHERE id=?", (identity,))
