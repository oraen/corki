"""SQLite document identity, extraction jobs and source-grounded retrieval."""

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from corki.tools.bm25 import BM25Scorer
from corki.tools.tokenizer import tokenize

LEASE_SECONDS = 1500


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def process_alive(pid):
    if not pid:
        return False
    # Windows os.kill is not a portable existence probe; use lease expiry there.
    if sys.platform == "win32":
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def tokens(text):
    # Keep the tool search English stemmer, but do not transliterate Chinese.
    english = tokenize(re.sub(r"[\u3400-\u9fff]+", " ", text))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        english.extend(run if len(run) == 1 else (run[i : i + 2] for i in range(len(run) - 1)))
    return english


class KnowledgeStore:
    def __init__(self, home):
        self.home = Path(home)
        self.path = self.home / "knowledge.sqlite"
        self.root = self.home / "manager/knowledge"

    @contextmanager
    def connect(self):
        self.home.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
                    hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending', summary TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '', job TEXT, started REAL,
                    owner_pid INTEGER NOT NULL DEFAULT 0,
                    model TEXT NOT NULL DEFAULT '', updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS segments (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id)
                    ON DELETE CASCADE, ordinal INTEGER NOT NULL, title TEXT NOT NULL,
                    summary TEXT NOT NULL, keywords TEXT NOT NULL, content TEXT NOT NULL,
                    start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
                    start_line INTEGER NOT NULL, end_line INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS segments_document ON segments(document_id, ordinal);
                CREATE TABLE IF NOT EXISTS batches (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    batch INTEGER NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(document_id,batch)
                );
            """)
            columns = {r[1] for r in db.execute("PRAGMA table_info(documents)")}
            if "owner_pid" not in columns:
                db.execute("ALTER TABLE documents ADD COLUMN owner_pid INTEGER NOT NULL DEFAULT 0")
            self.path.chmod(0o600)
            with db:
                yield db
        finally:
            db.close()

    def source(self, relative):
        path = self.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("知识文档路径无效")
        return path

    def sync(self, path, title, content):
        relative = str(Path(path).resolve().relative_to(self.root.resolve()))
        fingerprint = digest(content)
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE path=?", (relative,)).fetchone()
            if row is None:
                identity = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO documents(id,path,title,hash,updated) VALUES(?,?,?,?,?)",
                    (identity, relative, title, fingerprint, time.time()),
                )
            else:
                identity = row["id"]
                if row["hash"] != fingerprint:
                    db.execute("DELETE FROM segments WHERE document_id=?", (identity,))
                    db.execute("DELETE FROM batches WHERE document_id=?", (identity,))
                    db.execute(
                        """UPDATE documents SET hash=?,title=?,status='pending',
                        summary='',error='',job=NULL,updated=? WHERE id=?""",
                        (fingerprint, title, time.time(), identity),
                    )
                if row["status"] == "processing" and (
                    time.time() - row["started"] > LEASE_SECONDS
                    or not process_alive(row["owner_pid"])
                ):
                    db.execute(
                        """UPDATE documents SET status='failed',job=NULL,
                        error='处理已中断，请重试' WHERE id=?""",
                        (identity,),
                    )
            return dict(db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone())

    def set_enabled(self, identity, enabled):
        with self.connect() as db:
            db.execute(
                "UPDATE documents SET enabled=?,updated=? WHERE id=?",
                (int(enabled), time.time(), identity),
            )
            if not enabled:
                db.execute(
                    """UPDATE documents SET status='pending',job=NULL
                    WHERE id=? AND status='processing'""",
                    (identity,),
                )

    def check_job(self, identity, job):
        with self.connect() as db:
            row = db.execute("SELECT job,enabled FROM documents WHERE id=?", (identity,)).fetchone()
            if row is None or row[0] != job:
                raise ValueError("文档已停用或变更，处理已取消")

    def delete(self, identity):
        with self.connect() as db:
            db.execute("DELETE FROM documents WHERE id=?", (identity,))

    def claim(self, identity, model, *, prepare=False):
        job = uuid.uuid4().hex
        with self.connect() as db:
            changed = db.execute(
                """UPDATE documents SET status='processing',job=?,started=?,
                error='',model=?,owner_pid=?
                WHERE id=? AND (enabled=1 OR ?=1) AND status IN ('pending','failed')""",
                (job, time.time(), model, os.getpid(), identity, int(prepare)),
            ).rowcount
            return job if changed else None

    def cached_batch(self, identity, batch):
        with self.connect() as db:
            row = db.execute(
                "SELECT result FROM batches WHERE document_id=? AND batch=?", (identity, batch)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def save_batch(self, identity, job, batch, rows):
        with self.connect() as db:
            current = db.execute("SELECT job FROM documents WHERE id=?", (identity,)).fetchone()
            if current is None or current[0] != job:
                raise ValueError("文档已变化，本次处理已取消")
            db.execute(
                "INSERT OR REPLACE INTO batches VALUES(?,?,?)",
                (identity, batch, json.dumps(rows, ensure_ascii=False)),
            )

    def finish(self, identity, job, fingerprint, rows):
        with self.connect() as db:
            doc = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
            if doc is None or doc["job"] != job or doc["hash"] != fingerprint:
                return False
            if digest(self.source(doc["path"]).read_text()) != fingerprint:
                raise ValueError("文档已变化，请重新处理")
            db.execute("DELETE FROM segments WHERE document_id=?", (identity,))
            for ordinal, row in enumerate(rows):
                db.execute(
                    "INSERT INTO segments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        identity,
                        ordinal,
                        row["title"],
                        row["summary"],
                        json.dumps(row["keywords"], ensure_ascii=False),
                        row["content"],
                        row["start_char"],
                        row["end_char"],
                        row["start_line"],
                        row["end_line"],
                    ),
                )
            summary = "\n".join(row["title"] + "：" + row["summary"] for row in rows)[:1800]
            db.execute(
                """UPDATE documents SET status='ready',enabled=1,summary=?,error='',job=NULL,
                updated=? WHERE id=?""",
                (summary, time.time(), identity),
            )
            db.execute("DELETE FROM batches WHERE document_id=?", (identity,))
            return True

    def fail(self, identity, job, error):
        with self.connect() as db:
            db.execute(
                "UPDATE documents SET status='failed',error=?,job=NULL WHERE id=? AND job=?",
                (error[:300], identity, job),
            )

    def _live(self, doc):
        try:
            return digest(self.source(doc["path"]).read_text()) == doc["hash"]
        except (OSError, ValueError):
            return False

    def search(self, query="", document_id=None, offset=0, limit=12):
        if not self.path.exists():
            return {"results": [], "total": 0, "next_offset": None}
        with self.connect() as db:
            docs = {
                r["id"]: dict(r)
                for r in db.execute("SELECT * FROM documents WHERE enabled=1 AND status='ready'")
            }
            docs = {key: row for key, row in docs.items() if self._live(row)}
            if document_id or query.strip():
                sql = (
                    "SELECT id,document_id,ordinal,title,summary,keywords,start_char,end_char,"
                    "start_line,end_line FROM segments"
                )
                args = ()
                if document_id:
                    sql += " WHERE document_id=?"
                    args = (document_id,)
                sql += " ORDER BY document_id,ordinal"
                rows = [dict(r) for r in db.execute(sql, args) if r["document_id"] in docs]
                for row in rows:
                    row["keywords"] = json.loads(row["keywords"])
                    row["source"] = docs[row["document_id"]]["path"]
                    row["document_title"] = docs[row["document_id"]]["title"]
            else:
                rows = [
                    {
                        "id": d["id"],
                        "title": d["title"],
                        "summary": d["summary"],
                        "source": d["path"],
                    }
                    for d in docs.values()
                ]
        if query.strip():
            scorer = BM25Scorer(
                tokens(
                    " ".join(
                        (
                            r.get("document_title", ""),
                            r["title"],
                            r["summary"],
                            " ".join(r.get("keywords", [])),
                        )
                    )
                )
                for r in rows
            )
            rows = [
                {**rows[i], "score": score} for i, score in scorer.rank(tokens(query), len(rows))
            ]
        total = len(rows)
        return {
            "results": rows[offset : offset + limit],
            "total": total,
            "next_offset": offset + limit if offset + limit < total else None,
        }

    def read(self, segment_id, offset=0, limit=12000):
        if not self.path.exists():
            raise ValueError("知识段落不存在")
        with self.connect() as db:
            row = db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
            if row is None:
                raise ValueError("知识段落不存在，请重新检索")
            doc = db.execute("SELECT * FROM documents WHERE id=?", (row["document_id"],)).fetchone()
            if not doc["enabled"] or doc["status"] != "ready" or not self._live(doc):
                raise ValueError("知识文档已停用、更新或删除，请重新检索")
            result = dict(row)
            result["content"] = row["content"][offset : offset + limit]
            result["next_offset"] = offset + limit if offset + limit < len(row["content"]) else None
            result["source"] = doc["path"]
            result["keywords"] = json.loads(row["keywords"])
            return result
