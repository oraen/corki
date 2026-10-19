"""Atomic job + occurrence ledger; unknown executions are never automatically replayed."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .models import validate_model
from .schedule import following, parse

ACTIVE = ("claimed", "running")
MAX_PARALLEL = 2


class CronStore:
    def __init__(self, home: Path):
        self.home = Path(home).expanduser().resolve()
        self.root = self.home / "cron"
        self.path = self.root / "cron.sqlite"

    @contextmanager
    def transaction(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, definition TEXT NOT NULL, version INTEGER NOT NULL,
                    enabled INTEGER NOT NULL, next_run REAL, created REAL NOT NULL,
                    updated REAL NOT NULL, pause_reason TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    definition TEXT NOT NULL, source TEXT NOT NULL, scheduled REAL,
                    status TEXT NOT NULL, created REAL NOT NULL, started REAL, finished REAL,
                    heartbeat REAL NOT NULL, output TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '', session_id TEXT,
                    cancel INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON runs(job_id)
                    WHERE status IN ('claimed','running');
                CREATE UNIQUE INDEX IF NOT EXISTS one_occurrence ON runs(job_id, scheduled)
                    WHERE source='schedule';
                CREATE INDEX IF NOT EXISTS run_history ON runs(job_id, created DESC);
                CREATE TABLE IF NOT EXISTS service (id INTEGER PRIMARY KEY, heartbeat REAL);
            """)
            connection.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
            if "error_log" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN error_log TEXT NOT NULL DEFAULT ''")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def job(row):
        result = dict(row)
        result.update(json.loads(result.pop("definition")))
        result["enabled"] = bool(result["enabled"])
        return result

    def save(self, values, identity=None, version=None, *, now=None):
        now = time.time() if now is None else now
        if not isinstance(values, dict):
            raise ValueError("任务配置无效")
        name, prompt = values.get("name", ""), values.get("prompt", "")
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 100:
            raise ValueError("任务名称须为 1–100 个字符")
        if not isinstance(prompt, str) or not 0 < len(prompt.strip()) <= 32000:
            raise ValueError("请输入任务内容（最多 32000 个字符）")
        description = values.get("description", "")
        if not isinstance(description, str) or len(description) > 500:
            raise ValueError("任务描述最多 500 个字符")
        selection = None
        if "provider" in values or "model" in values:
            selection = validate_model(self.home, values.get("provider"), values.get("model"))
        directory = values.get("workdir", "")
        if not isinstance(directory, str) or len(directory) > 4096 or "\x00" in directory:
            raise ValueError("工作目录无效")
        path = Path(directory).expanduser() if directory.strip() else self.home / "main/default"
        if not path.is_absolute() or (
            path.resolve() != self.home / "main/default" and not path.is_dir()
        ):
            raise ValueError("工作目录必须是已存在的绝对路径")
        enabled = values.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("启用状态必须是布尔值")
        expression, zone = values.get("schedule", ""), values.get("timezone", "Asia/Shanghai")
        with self.transaction() as db:
            old = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
            if identity and (old is None or old["version"] != version):
                raise ValueError("任务已更新或删除，请刷新后重试")
            if (
                old
                and db.execute(
                    "SELECT 1 FROM runs WHERE job_id=? AND status IN ('claimed','running')",
                    (identity,),
                ).fetchone()
            ):
                raise ValueError("任务正在执行，请先停止后再编辑")
            previous = self.job(old) if old else None
            requested_kind = values.get("schedule_kind")
            if requested_kind not in (None, "once", "interval", "cron"):
                raise ValueError("执行方式无效")
            same = (
                previous
                and (requested_kind is None or requested_kind == previous["normalized"]["kind"])
                and (expression, zone) == (previous["schedule"], previous["timezone"])
            )
            normalized = (
                previous["normalized"]
                if same
                else parse(expression, zone, now, kind=requested_kind)
            )
            if requested_kind is not None and normalized["kind"] != requested_kind:
                raise ValueError("时间格式与所选执行方式不符")
            next_run = (
                old["next_run"]
                if same
                else (
                    normalized["at"] if normalized["kind"] == "once" else following(normalized, now)
                )
            )
            if enabled and next_run is None:
                raise ValueError("一次性任务已执行，请设置新的执行时间")
            definition = dict(
                name=name.strip(),
                description=description.strip(),
                prompt=prompt.strip(),
                workdir=str(path.resolve()),
                schedule=expression.strip(),
                timezone=zone,
                normalized=normalized,
            )
            if (
                selection is None
                and previous
                and previous.get("provider")
                and previous.get("model")
            ):
                selection = previous["provider"], previous["model"]
            if selection is not None:
                definition.update(provider=selection[0], model=selection[1])
            identity = identity or uuid.uuid4().hex
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "definition=excluded.definition, version=jobs.version+1, enabled=excluded.enabled, "
                "next_run=excluded.next_run, updated=excluded.updated, pause_reason=''",
                (
                    identity,
                    json.dumps(definition, ensure_ascii=False),
                    1,
                    int(enabled),
                    next_run,
                    now,
                    now,
                    "",
                ),
            )
        return identity

    def list(self):
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created DESC").fetchall()
            result = []
            for row in rows:
                job = self.job(row)
                last = db.execute(
                    "SELECT id,status,created,finished,error FROM runs WHERE job_id=? "
                    "ORDER BY created DESC, rowid DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                job["last_run"] = dict(last) if last else None
                result.append(job)
            service = db.execute("SELECT heartbeat FROM service WHERE id=1").fetchone()
        return {
            "jobs": result,
            "online": bool(service and time.time() - service[0] < 20),
            "max_parallel": MAX_PARALLEL,
        }

    def change(self, identity, action, version=None):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
            if row is None or row["version"] != version:
                raise ValueError("任务已更新或删除，请刷新后重试")
            active = db.execute(
                "SELECT id FROM runs WHERE job_id=? AND status IN ('claimed','running')",
                (identity,),
            ).fetchone()
            if action == "delete":
                if active:
                    raise ValueError("请先停止执行中的任务")
                db.execute("DELETE FROM jobs WHERE id=?", (identity,))
            elif action in ("pause", "resume"):
                if action == "resume" and row["next_run"] is None:
                    raise ValueError("一次性任务已执行，请编辑执行时间或立即运行")
                db.execute(
                    "UPDATE jobs SET enabled=?,version=version+1,updated=?,pause_reason='' "
                    "WHERE id=?",
                    (int(action == "resume"), time.time(), identity),
                )
            elif action == "stop":
                if active:
                    db.execute("UPDATE runs SET cancel=1 WHERE id=?", (active[0],))
            else:
                raise ValueError("不支持的任务操作")

    def records(self, *, offset=0, limit=20, query="", names=None):
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 100
            or not isinstance(query, str)
            or len(query) > 200
        ):
            raise ValueError("运行记录查询参数无效")
        names = [] if names is None else names
        if (
            not isinstance(names, list)
            or len(names) > 200
            or any(not isinstance(name, str) or not 0 < len(name) <= 100 for name in names)
        ):
            raise ValueError("任务名称筛选无效")
        where = "instr(lower(json_extract(definition, '$.name')), lower(?)) > 0"
        params = [query]
        if names:
            where += (
                " AND json_extract(definition, '$.name') IN (" + ",".join("?" for _ in names) + ")"
            )
            params.extend(names)
        with self.transaction() as db:
            total = db.execute(f"SELECT count(*) FROM runs WHERE {where}", params).fetchone()[0]
            rows = db.execute(
                "SELECT id,job_id,json_extract(definition, '$.name') AS name,"
                "status,source,created,started,finished FROM runs WHERE "
                + where
                + " ORDER BY created DESC,rowid DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            task_names = [
                r[0]
                for r in db.execute(
                    "SELECT json_extract(definition, '$.name') AS name FROM runs "
                    "UNION SELECT json_extract(definition, '$.name') FROM jobs ORDER BY name"
                )
            ]
        return {
            "records": [dict(row) for row in rows],
            "task_names": task_names,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def history(self, identity):
        with self.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,status,source,scheduled,created,started,finished,error,session_id "
                    "FROM runs WHERE job_id=? ORDER BY created DESC, rowid DESC LIMIT 50",
                    (identity,),
                )
            ]

    def run(self, identity):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise ValueError("执行记录不存在")
            result = dict(row)
            result["definition"] = json.loads(result["definition"])
            return result

    def claim(self, identity=None, *, now=None):
        """Commit advance + attempt together before dispatch; collapse missed fires to one."""
        now = time.time() if now is None else now
        with self.transaction() as db:
            if (
                db.execute(
                    "SELECT count(*) FROM runs WHERE status IN ('claimed','running')"
                ).fetchone()[0]
                >= MAX_PARALLEL
            ):
                if identity:
                    raise ValueError("已有两个任务执行中，请稍后重试")
                return None
            if identity:
                row = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
                if row is None:
                    raise ValueError("任务不存在")
            else:
                row = db.execute(
                    "SELECT * FROM jobs WHERE enabled=1 AND next_run<=? AND NOT EXISTS "
                    "(SELECT 1 FROM runs WHERE job_id=jobs.id AND status IN ('claimed','running')) "
                    "ORDER BY next_run LIMIT 1",
                    (now,),
                ).fetchone()
                if row is None:
                    return None
            if db.execute(
                "SELECT 1 FROM runs WHERE job_id=? AND status IN ('claimed','running')",
                (row["id"],),
            ).fetchone():
                raise ValueError("该任务已在执行中")
            run_id = uuid.uuid4().hex
            definition = json.loads(row["definition"])
            if not identity:
                next_run = following(definition["normalized"], now, row["next_run"])
                db.execute(
                    "UPDATE jobs SET next_run=?,enabled=?,version=version+1,updated=? WHERE id=?",
                    (next_run, int(next_run is not None), now, row["id"]),
                )
            db.execute(
                "INSERT INTO runs(id,job_id,definition,source,scheduled,status,created,heartbeat) "
                "VALUES(?,?,?,?,?,'claimed',?,?)",
                (
                    run_id,
                    row["id"],
                    row["definition"],
                    "manual" if identity else "schedule",
                    None if identity else row["next_run"],
                    now,
                    now,
                ),
            )
            return run_id

    def start_run(self, identity):
        with self.transaction() as db:
            return bool(
                db.execute(
                    "UPDATE runs SET status='running',started=?,heartbeat=?, "
                    "session_id=? WHERE id=? AND status='claimed'",
                    (time.time(), time.time(), identity, identity),
                ).rowcount
            )

    def heartbeat(self, identity=None):
        with self.transaction() as db:
            if identity:
                db.execute(
                    "UPDATE runs SET heartbeat=? WHERE id=? AND status IN ('claimed','running')",
                    (time.time(), identity),
                )
            else:
                db.execute(
                    "INSERT INTO service VALUES(1,?) ON CONFLICT(id) DO UPDATE SET "
                    "heartbeat=excluded.heartbeat",
                    (time.time(),),
                )

    def active(self):
        with self.transaction() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,heartbeat FROM runs WHERE status IN ('claimed','running')"
                )
            ]

    def finish(self, identity, status, output="", error="", error_log=""):
        if status not in ("completed", "failed", "unknown", "cancelled"):
            raise ValueError("无效执行状态")
        with self.transaction() as db:
            row = db.execute(
                "SELECT job_id,source FROM runs WHERE id=? AND status IN ('claimed','running')",
                (identity,),
            ).fetchone()
            if row is None:
                return
            db.execute(
                "UPDATE runs SET status=?,output=?,error=?,error_log=?,finished=? WHERE id=?",
                (
                    status,
                    output[:2_000_000],
                    error[:2000],
                    error_log[-64000:],
                    time.time(),
                    identity,
                ),
            )
            if status == "unknown":
                db.execute(
                    "UPDATE jobs SET enabled=0,pause_reason=?,version=version+1 WHERE id=?",
                    ("上次执行意外中断，确认结果后可重新开启", row[0]),
                )
            if row["source"] == "schedule":
                job = db.execute("SELECT * FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
                if job["next_run"] is not None and job["next_run"] <= time.time():
                    normalized = json.loads(job["definition"])["normalized"]
                    upcoming = following(normalized, time.time(), job["next_run"])
                    db.execute(
                        "UPDATE jobs SET next_run=?,version=version+1 WHERE id=?",
                        (upcoming, row["job_id"]),
                    )
            # Preserve at most 50 terminal results per job; active attempts are never pruned.
            db.execute(
                "DELETE FROM runs WHERE job_id=? AND status NOT IN ('claimed','running') "
                "AND id NOT IN (SELECT id FROM runs WHERE job_id=? "
                "ORDER BY created DESC,rowid DESC LIMIT 50)",
                (row[0], row[0]),
            )
