"""Small application metadata store; model context is never snapshotted here."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager

from .models import AppError, canonical, definition, now

ACTIVE = ("submitted", "running", "waiting", "stopping")


class ApplicationStore:
    def __init__(self, home):
        self.home = home
        self.root = home / "applications"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "applications.db"
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS apps(
                    id TEXT PRIMARY KEY,name_key TEXT UNIQUE NOT NULL,
                    definition TEXT NOT NULL,version INTEGER NOT NULL,
                    created TEXT NOT NULL,updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(
                    app TEXT NOT NULL,version INTEGER NOT NULL,definition TEXT NOT NULL,
                    created TEXT NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(app,version));
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY,app TEXT NOT NULL,title TEXT NOT NULL,
                    directory TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS session_app ON sessions(app,updated DESC);
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY,session TEXT NOT NULL,request_id TEXT NOT NULL,
                    version INTEGER NOT NULL,status TEXT NOT NULL,provider TEXT,model TEXT,
                    core_turn TEXT,input TEXT NOT NULL DEFAULT '',answer TEXT NOT NULL DEFAULT '',
                    error TEXT,created TEXT NOT NULL,updated TEXT NOT NULL,
                    UNIQUE(session,request_id));
                CREATE INDEX IF NOT EXISTS run_session ON runs(session,created DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS run_active ON runs(session)
                    WHERE status IN ('submitted','running','waiting','stopping');
                CREATE INDEX IF NOT EXISTS app_updated ON apps(updated DESC);
            """)
            db.execute("BEGIN IMMEDIATE")
            app_columns = {r[1] for r in db.execute("PRAGMA table_info(apps)")}
            if "public_id" not in app_columns:
                db.execute("ALTER TABLE apps ADD COLUMN public_id INTEGER")
            if "enabled" not in app_columns:
                db.execute("ALTER TABLE apps ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
            db.execute("CREATE TABLE IF NOT EXISTS app_sequence("
                       "id INTEGER PRIMARY KEY CHECK(id=1),next_id INTEGER NOT NULL)")
            db.execute("INSERT OR IGNORE INTO app_sequence VALUES(1,100000)")
            next_id = max(100000, db.execute(
                "SELECT next_id FROM app_sequence WHERE id=1").fetchone()[0], db.execute(
                "SELECT coalesce(max(public_id)+1,100000) FROM apps").fetchone()[0])
            for row in db.execute(
                "SELECT id FROM apps WHERE public_id IS NULL ORDER BY created,id"
            ).fetchall():
                db.execute("UPDATE apps SET public_id=? WHERE id=?", (next_id, row[0]))
                next_id += 1
            db.execute("UPDATE app_sequence SET next_id=? WHERE id=1", (next_id,))
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS app_public_id ON apps(public_id)")
            summaries = ("name", "description", "type", "provider", "model")
            missing = [key for key in summaries if key not in app_columns]
            for key in missing:
                db.execute(f"ALTER TABLE apps ADD COLUMN {key} TEXT NOT NULL DEFAULT ''")
            if missing:
                db.execute(
                    "UPDATE apps SET "
                    + ",".join(
                        f"{key}=coalesce(json_extract(definition,'$.{key}'),'')" for key in missing
                    )
                )
            db.execute("CREATE INDEX IF NOT EXISTS app_kind_updated ON apps(type,updated DESC)")
            # Versions own executable definitions. The old column remains empty
            # for compatibility; list rows contain only cheap indexed summaries.
            db.execute("UPDATE apps SET definition='' WHERE definition!=''")
            columns = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
            if "input_sha" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN input_sha TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE INDEX IF NOT EXISTS run_request ON runs(request_id)")
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def app(self, identity, version=None):
        with self.db() as db:
            row = db.execute(
                "SELECT a.*,v.definition AS current_definition FROM apps a "
                "JOIN versions v ON v.app=a.id AND v.version=a.version WHERE a.id=?",
                (identity,),
            ).fetchone()
            if row is None:
                raise AppError("应用不存在", 404)
            result = {
                "id": identity,
                "public_id": row["public_id"],
                "enabled": bool(row["enabled"]),
                "version": row["version"],
                "created": row["created"],
                "updated": row["updated"],
                **json.loads(row["current_definition"]),
            }
            if version is not None:
                past = db.execute(
                    "SELECT * FROM versions WHERE app=? AND version=?", (identity, version)
                ).fetchone()
                if past is None:
                    raise AppError("应用版本不存在", 404)
                result.update(json.loads(past["definition"]), version=past["version"])
            result["versions"] = [
                dict(v)
                for v in db.execute(
                    (
                        "SELECT version,created,reason FROM versions WHERE app=? ORDER BY "
                        "version DESC LIMIT 20"
                    ),
                    (identity,),
                )
            ]
            return result

    def prompt_history(self, app):
        """UI-only projection; runtime/status reads keep their cheap app lookup."""
        with self.db() as db:
            rows = db.execute(
                "SELECT version,created,reason,"
                "CASE WHEN row_number() OVER (ORDER BY version)=1 OR "
                "json_extract(definition,'$.prompt') IS NOT "
                "lag(json_extract(definition,'$.prompt')) OVER (ORDER BY version) "
                "THEN 1 ELSE 0 END AS prompt_changed "
                "FROM versions WHERE app=? AND version<=? ORDER BY version DESC LIMIT 20",
                (app["id"], app["version"]),
            ).fetchall()
        versions = [{**dict(row), "prompt_changed": bool(row["prompt_changed"])} for row in rows]
        return {**app, "versions": versions, "prompt_version": next(
            (entry["version"] for entry in versions if entry["prompt_changed"]), app["version"]
        )}

    def listing(self, query="", kind="", offset=0):
        if type(offset) is not int or offset < 0:
            raise AppError("分页参数无效")
        with self.db() as db:
            rows = db.execute(
                "SELECT id,public_id,enabled,version,created,updated,name,description,type,provider,model FROM apps "
                "WHERE (instr(lower(name),lower(?))>0 OR instr(lower(description),lower(?))>0 "
                "OR instr(cast(public_id AS TEXT),?)>0) "
                "AND (?='' OR type=?) ORDER BY updated DESC LIMIT 41 OFFSET ?",
                (query, query, query, kind, kind, offset),
            ).fetchall()
            return {"items": [{**dict(r), "enabled": bool(r["enabled"])} for r in rows[:40]],
                    "has_more": len(rows) > 40}

    def set_enabled(self, identity, enabled):
        if type(enabled) is not bool:
            raise AppError("应用开关参数无效")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,public_id,enabled,updated FROM apps WHERE id=?",
                             (identity,)).fetchone()
            if row is None:
                raise AppError("应用不存在", 404)
            stamp = now() if bool(row["enabled"]) != enabled else row["updated"]
            db.execute("UPDATE apps SET enabled=?,updated=? WHERE id=?",
                       (int(enabled), stamp, identity))
            return {"id": identity, "public_id": row["public_id"], "enabled": enabled,
                    "updated": stamp}

    def save(self, values, identity=None, expected=None, reason="编辑应用"):
        stamp = now()
        try:
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = (
                    db.execute(
                        "SELECT a.*,v.definition AS current_definition FROM apps a "
                        "JOIN versions v ON v.app=a.id AND v.version=a.version WHERE a.id=?",
                        (identity,),
                    ).fetchone()
                    if identity
                    else None
                )
                if identity and row is None:
                    raise AppError("应用不存在", 404)
                if row and (type(expected) is not int or expected != row["version"]):
                    raise AppError("应用已被其他页面更新，请刷新后重试", 409)
                value = definition(values, json.loads(row["current_definition"]) if row else None)
                encoded = canonical(value)
                if row and encoded == row["current_definition"]:
                    return self.app(identity)
                version = row["version"] + 1 if row else 1
                identity = identity or uuid.uuid4().hex
                public_id = row["public_id"] if row else db.execute(
                    "SELECT next_id FROM app_sequence WHERE id=1").fetchone()[0]
                if not row:
                    db.execute("UPDATE app_sequence SET next_id=next_id+1 WHERE id=1")
                db.execute(
                    "INSERT INTO apps(id,name_key,definition,version,created,updated,"
                    "name,description,type,provider,model,public_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name_key=excluded.name_key,"
                    "definition=excluded.definition,version=excluded.version,updated=excluded.updated,"
                    "name=excluded.name,description=excluded.description,type=excluded.type,"
                    "provider=excluded.provider,model=excluded.model",
                    (
                        identity,
                        value["name"].casefold(),
                        "",
                        version,
                        row["created"] if row else stamp,
                        stamp,
                        *(
                            value[key]
                            for key in ("name", "description", "type", "provider", "model")
                        ),
                        public_id,
                    ),
                )
                db.execute(
                    "INSERT INTO versions VALUES(?,?,?,?,?)",
                    (identity, version, encoded, stamp, reason if row else "创建应用"),
                )
                db.execute(
                    (
                        "DELETE FROM versions WHERE app=? AND version NOT IN (SELECT "
                        "version FROM versions WHERE app=? ORDER BY version DESC LIMIT 20)"
                    ),
                    (identity, identity),
                )
        except sqlite3.IntegrityError as error:
            raise AppError("应用名称已存在", 409) from error
        return self.app(identity)

    def sessions(self, app, offset=0):
        self.app(app)
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM sessions WHERE app=? ORDER BY updated DESC LIMIT 41 OFFSET ?",
                (app, offset),
            ).fetchall()
            return {"items": [dict(r) for r in rows[:40]], "has_more": len(rows) > 40}

    def session(self, app, identity):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE id=? AND app=?", (identity, app)
            ).fetchone()
        if row is None:
            raise AppError("会话不存在或不属于当前应用", 404)
        return dict(row)

    def new_session(self, app, directory, text):
        identity, stamp = uuid.uuid4().hex, now()
        with self.db() as db:
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?)",
                (identity, app, text.strip()[:40], directory, stamp, stamp),
            )
        return self.session(app, identity)

    def accept(self, session, request_id, app, text):
        stamp, identity = now(), uuid.uuid4().hex
        with self.db() as db:
            old = db.execute(
                "SELECT * FROM runs WHERE session=? AND request_id=?", (session, request_id)
            ).fetchone()
            if old:
                return dict(old), False
            try:
                db.execute(
                    (
                        "INSERT INTO runs(id,session,request_id,version,status,input,creat"
                        "ed,updated,input_sha) VALUES(?,?,?,?,?,?,?,?,?)"
                    ),
                    (
                        identity,
                        session,
                        request_id,
                        app["version"],
                        "submitted",
                        text,
                        stamp,
                        stamp,
                        hashlib.sha256(text.encode()).hexdigest(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise AppError("当前会话正在运行，请先停止", 409) from error
            db.execute("UPDATE sessions SET updated=? WHERE id=?", (stamp, session))
        return self.run(identity), True

    def update_run(self, identity, **values):
        allowed = {"status", "provider", "model", "core_turn", "answer", "error", "input"}
        if not set(values) <= allowed:
            raise ValueError("invalid run update")
        values["updated"] = now()
        with self.db() as db:
            db.execute(
                "UPDATE runs SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
                (*values.values(), identity),
            )

    def run(self, identity):
        with self.db() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise AppError("运行任务不存在", 404)
        return dict(row)

    def runs(self, session, before=None):
        with self.db() as db:
            rows = db.execute(
                (
                    "SELECT * FROM runs WHERE session=? AND (? IS NULL OR created<?) "
                    "ORDER BY created DESC LIMIT 21"
                ),
                (session, before, before),
            ).fetchall()
            return [dict(r) for r in reversed(rows[:20])], len(rows) > 20
