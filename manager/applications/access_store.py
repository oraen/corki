"""Transport configuration and durable Kafka inbox/outbox, independent of prompts."""

import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .models import AppError, canonical, now

MAX_QUEUE_ROWS = 1000
MAX_QUEUE_BYTES = 64 * 1024 * 1024
SECRET_FIELDS = {"sasl_password", "ssl_key_password"}
CONNECTION_FIELDS = {
    "name",
    "brokers",
    "input_topics",
    "output_topic",
    "group_id",
    "enabled",
    "security_protocol",
    "sasl_mechanism",
    "sasl_username",
    "sasl_password",
    "ssl_ca_location",
    "ssl_certificate_location",
    "ssl_key_location",
    "ssl_key_password",
}


class IngressDisabled(AppError):
    pass


class AccessStore:
    def __init__(self, root):
        self.path = root / "access.db"
        with self.db() as db:
            db.executescript("""
                PRAGMA auto_vacuum=INCREMENTAL;
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS app_access(
                    app TEXT PRIMARY KEY,revision INTEGER NOT NULL,http_enabled INTEGER NOT NULL,
                    global_kafka INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS global_access(
                    id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,enabled INTEGER
                    NOT NULL);
                INSERT OR IGNORE INTO global_access VALUES(1,0,0);
                CREATE TABLE IF NOT EXISTS kafka_connections(
                    id TEXT PRIMARY KEY,owner TEXT NOT NULL,name_key TEXT NOT NULL,
                    revision INTEGER NOT NULL,stream TEXT NOT NULL,definition TEXT NOT NULL,
                    created TEXT NOT NULL,updated TEXT NOT NULL,UNIQUE(owner,name_key));
                CREATE INDEX IF NOT EXISTS kafka_owner ON kafka_connections(owner,created);
                CREATE TABLE IF NOT EXISTS kafka_offsets(
                    connection TEXT NOT NULL REFERENCES kafka_connections(id) ON DELETE
                    CASCADE,stream TEXT NOT NULL,topic TEXT NOT NULL,
                    partition INTEGER NOT NULL,next_offset INTEGER NOT NULL,
                    PRIMARY KEY(connection,stream,topic,partition));
                CREATE TABLE IF NOT EXISTS kafka_inbox(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,connection TEXT NOT NULL REFERENCES
                    kafka_connections(id) ON DELETE CASCADE,stream TEXT NOT NULL,
                    topic TEXT NOT NULL,partition INTEGER NOT NULL,offset INTEGER NOT NULL,
                    app TEXT,appid TEXT,has_id INTEGER NOT NULL,id_json TEXT,
                    payload TEXT NOT NULL,payload_bytes INTEGER NOT NULL DEFAULT 0,output_topic
                    TEXT NOT NULL,request TEXT NOT NULL UNIQUE,state TEXT NOT NULL,
                    run TEXT,session TEXT,interaction TEXT,created TEXT NOT NULL,updated TEXT
                    NOT NULL,retry_at REAL NOT NULL DEFAULT 0,
                    UNIQUE(connection,stream,topic,partition,offset));
                CREATE INDEX IF NOT EXISTS kafka_inbox_state ON kafka_inbox(state,retry_at,id);
                CREATE INDEX IF NOT EXISTS kafka_inbox_connection ON
                    kafka_inbox(connection,state,id);
                CREATE TABLE IF NOT EXISTS kafka_outbox(
                    id TEXT PRIMARY KEY,inbox INTEGER NOT NULL REFERENCES kafka_inbox(id) ON
                    DELETE CASCADE,connection TEXT NOT NULL,event TEXT NOT NULL,
                    topic TEXT NOT NULL,payload TEXT NOT NULL,payload_bytes INTEGER NOT NULL
                    DEFAULT 0,state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,retry_at REAL NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',created TEXT NOT NULL,updated TEXT NOT NULL,
                    UNIQUE(inbox,event));
                CREATE INDEX IF NOT EXISTS kafka_outbox_size ON kafka_outbox(state,payload_bytes);
                CREATE INDEX IF NOT EXISTS kafka_delivery ON
                    kafka_outbox(connection,state,retry_at,created);
                CREATE INDEX IF NOT EXISTS kafka_last_error
                    ON kafka_outbox(connection,state,updated DESC);
            """)
        self.path.chmod(0o600)

    def recover(self):
        # Only the process holding the Kafka kernel lock reconciles interrupted work.
        with self.db() as db:
            db.execute("UPDATE kafka_inbox SET state='queued' WHERE state='admitting'")
            db.execute("UPDATE kafka_outbox SET state='ready' WHERE state='sending'")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def app(self, app):
        with self.db() as db:
            row = db.execute("SELECT * FROM app_access WHERE app=?", (app,)).fetchone()
        return dict(
            app=app,
            revision=row["revision"] if row else 0,
            http_enabled=bool(row["http_enabled"]) if row else True,
            global_kafka=bool(row["global_kafka"]) if row else False,
        )

    def save_app(self, app, data):
        if type(data.get("http_enabled")) is not bool or type(data.get("global_kafka")) is not bool:
            raise AppError("接入开关必须是布尔值")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM app_access WHERE app=?", (app,)).fetchone()
            revision = row["revision"] if row else 0
            if type(data.get("expected")) is not int or data["expected"] != revision:
                raise AppError("接入配置已被其他页面更新，请刷新后重试", 409)
            if (
                row
                and bool(row["http_enabled"]) == data["http_enabled"]
                and bool(row["global_kafka"]) == data["global_kafka"]
            ):
                return self.app(app)
            db.execute(
                "INSERT INTO app_access VALUES(?,?,?,?) ON CONFLICT(app) DO UPDATE SET "
                "revision=excluded.revision,http_enabled=excluded.http_enabled,global_kafka=excluded.global_kafka",
                (app, revision + 1, data["http_enabled"], data["global_kafka"]),
            )
        return self.app(app)

    def global_state(self):
        with self.db() as db:
            row = db.execute("SELECT revision,enabled FROM global_access WHERE id=1").fetchone()
        return dict(revision=row["revision"], enabled=bool(row["enabled"]))

    def save_global(self, enabled, expected):
        if type(enabled) is not bool:
            raise AppError("Kafka 开关参数无效")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM global_access WHERE id=1").fetchone()
            if type(expected) is not int or expected != row["revision"]:
                raise AppError("Kafka 配置已更新，请刷新后重试", 409)
            if bool(row["enabled"]) != enabled:
                db.execute(
                    "UPDATE global_access SET enabled=?,revision=revision+1 WHERE id=1", (enabled,)
                )
        return self.global_state()

    def connections(self, owner=None, *, secrets=False):
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM kafka_connections WHERE (? IS NULL OR owner=?) ORDER BY created",
                (owner, owner),
            ).fetchall()
        return [self.project(row, secrets=secrets) for row in rows]

    def project(self, row, *, secrets=False):
        value = json.loads(row["definition"])
        if not secrets:
            for key in SECRET_FIELDS:
                value["has_" + key] = bool(value.pop(key, ""))
        return dict(
            value,
            id=row["id"],
            owner=row["owner"],
            revision=row["revision"],
            stream=row["stream"],
            created=row["created"],
            updated=row["updated"],
        )

    def connection(self, identity, owner=None, *, secrets=False):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM kafka_connections WHERE id=? AND (? IS NULL OR owner=?)",
                (identity, owner, owner),
            ).fetchone()
        if not row:
            raise AppError("Kafka 接入不存在", 404)
        return self.project(row, secrets=secrets)

    def validate(self, data, old, identity):
        if not isinstance(data, dict) or set(data) - CONNECTION_FIELDS:
            raise AppError("Kafka 配置包含无效字段")
        default = dict(
            name="",
            brokers=[],
            input_topics=[],
            output_topic="",
            group_id="corki-app-" + identity,
            enabled=False,
            security_protocol="PLAINTEXT",
            sasl_mechanism="PLAIN",
            sasl_username="",
            sasl_password="",
            ssl_ca_location="",
            ssl_certificate_location="",
            ssl_key_location="",
            ssl_key_password="",
        )
        value = {**default, **({k: old[k] for k in CONNECTION_FIELDS} if old else {}), **data}
        if type(value["enabled"]) is not bool:
            raise AppError("Kafka 启用状态无效")
        for field in CONNECTION_FIELDS - {"enabled", "brokers", "input_topics"}:
            if (
                not isinstance(value[field], str)
                or len(value[field]) > 4096
                or "\x00" in value[field]
            ):
                raise AppError("Kafka 字段格式或长度无效：" + field)
            if field not in SECRET_FIELDS:
                value[field] = value[field].strip()
        if not value["name"] or len(value["name"]) > 80:
            raise AppError("请填写 1–80 字的接入名称")
        for key in ("brokers", "input_topics"):
            entries = value[key]
            if isinstance(entries, str):
                entries = re.split(r"[,，\n]+", entries)
            if (
                not isinstance(entries, list)
                or not entries
                or len(entries) > 128
                or any(not isinstance(v, str) for v in entries)
            ):
                raise AppError("请填写 Kafka 地址和输入 Topic")
            value[key] = list(dict.fromkeys(v.strip() for v in entries if v.strip()))
            if not value[key]:
                raise AppError("请填写 Kafka 地址和输入 Topic")
        for broker in value["brokers"]:
            match = re.fullmatch(r"(?:\[[0-9A-Fa-f:]+\]|[^\s/:@]+):([0-9]{1,5})", broker)
            if not match or not 1 <= int(match[1]) <= 65535:
                raise AppError("Kafka 地址格式为 主机:端口，多个地址用逗号分隔")
        for topic in [*value["input_topics"], value["output_topic"]]:
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,249}", topic) or topic in (".", ".."):
                raise AppError("Topic 仅允许字母、数字、点、横线和下划线，最长 249 个字符")
        if value["output_topic"] in value["input_topics"]:
            raise AppError("输出 Topic 不能同时作为输入 Topic，避免循环调用")
        if not value["group_id"] or len(value["group_id"]) > 255:
            raise AppError("请填写有效的消费组，最长 255 个字符")
        if value["security_protocol"] not in ("PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"):
            raise AppError("安全协议无效")
        if value["sasl_mechanism"] not in ("PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"):
            raise AppError("SASL 认证方式无效")
        if value["security_protocol"].startswith("SASL") and (
            not value["sasl_username"] or not value["sasl_password"]
        ):
            raise AppError("请填写 SASL 用户名和密码")
        for key in ("ssl_ca_location", "ssl_certificate_location", "ssl_key_location"):
            if value[key] and not Path(value[key]).expanduser().is_file():
                raise AppError("证书文件不存在：" + key)
        return value

    def save_connection(self, owner, data, identity=None, expected=None):
        identity = identity or uuid.uuid4().hex
        try:
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM kafka_connections WHERE id=?", (identity,)
                ).fetchone()
                if row and row["owner"] != owner:
                    raise AppError("Kafka 接入不属于当前应用", 404)
                if (row and (type(expected) is not int or expected != row["revision"])) or (
                    not row and expected not in (None, 0)
                ):
                    raise AppError("Kafka 接入已被其他页面更新，请刷新后重试", 409)
                if (
                    not row
                    and db.execute("SELECT count(*) FROM kafka_connections").fetchone()[0] >= 32
                ):
                    raise AppError("最多同时配置 32 条 Kafka 接入")
                old = self.project(row, secrets=True) if row else None
                value = self.validate(data, old, identity)
                changed = old and (
                    sorted(old["brokers"]) != sorted(value["brokers"])
                    or old["group_id"] != value["group_id"]
                )
                if changed and self.pending_in(db, identity):
                    raise AppError(
                        "此接入仍有未完成或未发送的消息，请处理后再更换集群或消费组", 409
                    )
                encoded = canonical(value)
                if row and encoded == row["definition"]:
                    return self.connection(identity, owner)
                stamp = now()
                stream = uuid.uuid4().hex if not row or changed else row["stream"]
                db.execute(
                    "INSERT INTO kafka_connections VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO "
                    "UPDATE SET "
                    "name_key=excluded.name_key,revision=excluded.revision,stream=excluded.stream,"
                    "definition=excluded.definition,updated=excluded.updated",
                    (
                        identity,
                        owner,
                        value["name"].casefold(),
                        row["revision"] + 1 if row else 1,
                        stream,
                        encoded,
                        row["created"] if row else stamp,
                        stamp,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise AppError("当前范围已有同名 Kafka 接入", 409) from error
        return self.connection(identity, owner)

    def pending_in(self, db, connection):
        return (
            db.execute(
                "SELECT 1 FROM kafka_inbox WHERE connection=? AND state!='done' LIMIT 1",
                (connection,),
            ).fetchone()
            or db.execute(
                "SELECT 1 FROM kafka_outbox WHERE connection=? AND state!='sent' LIMIT 1",
                (connection,),
            ).fetchone()
        )

    def delete_connection(self, identity, owner, expected):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM kafka_connections WHERE id=? AND owner=?", (identity, owner)
            ).fetchone()
            if not row:
                raise AppError("Kafka 接入不存在", 404)
            if type(expected) is not int or expected != row["revision"]:
                raise AppError("Kafka 接入已更新，请刷新后重试", 409)
            db.execute("DELETE FROM kafka_outbox WHERE connection=?", (identity,))
            db.execute("DELETE FROM kafka_inbox WHERE connection=?", (identity,))
            db.execute("DELETE FROM kafka_offsets WHERE connection=?", (identity,))
            db.execute("DELETE FROM kafka_connections WHERE id=?", (identity,))

    def authorize(self, app, transport):
        if transport is None:
            return
        config = self.app(app["id"])
        if transport == "http" and not config["http_enabled"]:
            raise IngressDisabled(f"appid {app['public_id']} 不支持http调用", 403)
        if isinstance(transport, dict) and transport.get("kind") == "kafka":
            connection = self.connection(transport["connection"], secrets=True)
            if not connection["enabled"]:
                raise IngressDisabled("Kafka 接入已关闭", 403)
            if connection["owner"] and connection["owner"] != app["id"]:
                raise IngressDisabled("Kafka 接入不属于当前应用", 403)
            if not connection["owner"] and (
                not self.global_state()["enabled"] or not config["global_kafka"]
            ):
                raise IngressDisabled("应用未启用全局 Kafka 接入", 403)

    def remove_app(self, app):
        with self.db() as db:
            db.execute("DELETE FROM kafka_connections WHERE owner=?", (app,))
            db.execute("DELETE FROM app_access WHERE app=?", (app,))

    def metrics(self):
        with self.db() as db:
            result = {
                r["connection"]: dict(queued=r["queued"], failed=0)
                for r in db.execute(
                    "SELECT connection,count(*) AS queued FROM kafka_inbox WHERE state!='done' "
                    "GROUP BY connection"
                )
            }
            for row in db.execute(
                "SELECT connection,count(*) AS failed FROM kafka_outbox WHERE state='failed' "
                "GROUP BY connection"
            ):
                result.setdefault(row["connection"], dict(queued=0, failed=0))["failed"] = row[
                    "failed"
                ]
        with self.db() as db:
            for connection, entry in result.items():
                if entry["failed"]:
                    row = db.execute(
                        "SELECT error FROM kafka_outbox WHERE connection=? AND state='failed' "
                        "ORDER BY updated DESC LIMIT 1",
                        (connection,),
                    ).fetchone()
                    if row:
                        entry["last_error"] = row["error"]
        return result

    def capacity_in(self, db):
        count, size = db.execute(
            "SELECT count(*),coalesce(sum(payload_bytes),0) FROM kafka_inbox WHERE state!='done'"
        ).fetchone()
        size += db.execute(
            "SELECT coalesce(sum(payload_bytes),0) FROM kafka_outbox WHERE state!='sent'"
        ).fetchone()[0]
        return count < MAX_QUEUE_ROWS and size < MAX_QUEUE_BYTES

    def capacity(self):
        with self.db() as db:
            return self.capacity_in(db)

    def dispatch_capacity(self):
        with self.db() as db:
            size = db.execute(
                "SELECT coalesce(sum(payload_bytes),0) FROM kafka_outbox WHERE state!='sent'"
            ).fetchone()[0]
        return size < MAX_QUEUE_BYTES

    def ingest(self, connection, message, envelope):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            where = (connection["id"], connection["stream"], message.topic(), message.partition())
            row = db.execute(
                "SELECT next_offset FROM kafka_offsets WHERE connection=? AND stream=? AND "
                "topic=? AND partition=?",
                where,
            ).fetchone()
            if row and message.offset() < row[0]:
                return None
            if not self.capacity_in(db):
                return False
            origin = [*where, message.offset()]
            request = "kafka:" + hashlib.sha256(canonical(origin).encode()).hexdigest()
            stamp = now()
            encoded = canonical(envelope)
            cursor = db.execute(
                "INSERT OR IGNORE INTO "
                "kafka_inbox(connection,stream,topic,partition,offset,app,appid,has_id,id_json,payload,payload_bytes,output_topic,request,state,created,updated)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    *origin,
                    envelope.get("app"),
                    canonical(envelope.get("appid")),
                    "id" in envelope,
                    canonical(envelope["id"]) if "id" in envelope else None,
                    encoded,
                    len(encoded.encode()),
                    envelope["output_topic"],
                    request,
                    "queued",
                    stamp,
                    stamp,
                ),
            )
            db.execute(
                "INSERT INTO kafka_offsets VALUES(?,?,?,?,?) ON "
                "CONFLICT(connection,stream,topic,partition) DO UPDATE SET "
                "next_offset=max(next_offset,excluded.next_offset)",
                (*where, message.offset() + 1),
            )
            return cursor.lastrowid

    def jobs(self, states, connections=None, limit=32):
        if connections is not None and not connections:
            return []
        sql = (
            "SELECT * FROM kafka_inbox WHERE retry_at<=? AND state IN ("
            + ",".join("?" for _ in states)
            + ")"
        )
        args = [time.time(), *states]
        if connections is not None:
            sql += " AND connection IN (" + ",".join("?" for _ in connections) + ")"
            args.extend(connections)
        with self.db() as db:
            return [dict(r) for r in db.execute(sql + " ORDER BY id LIMIT ?", (*args, limit))]

    def bind(self, job, receipt):
        with self.db() as db:
            db.execute(
                "UPDATE kafka_inbox SET "
                "state='running',run=?,session=?,payload='',payload_bytes=0,retry_at=0,updated=?"
                " WHERE id=?",
                (receipt["run"], receipt["session"], now(), job["id"]),
            )

    def mark(self, identity, state):
        with self.db() as db:
            db.execute(
                "UPDATE kafka_inbox SET state=?,payload=CASE WHEN ?='done' THEN '' ELSE payload"
                " END,payload_bytes=CASE WHEN ?='done' THEN 0 ELSE payload_bytes "
                "END,retry_at=?,updated=? WHERE id=?",
                (state, state, state, time.time() + 1 if state == "queued" else 0, now(), identity),
            )

    def emit(self, job, event, body, *, finish=False):
        with self.db() as db:
            if not db.execute("SELECT 1 FROM kafka_inbox WHERE id=?", (job["id"],)).fetchone():
                return
            if db.execute(
                "SELECT 1 FROM kafka_outbox WHERE inbox=? AND event=?", (job["id"], event)
            ).fetchone():
                if finish:
                    db.execute(
                        "UPDATE kafka_inbox SET "
                        "state='done',payload='',payload_bytes=0,id_json=NULL,updated=? WHERE "
                        "id=?",
                        (now(), job["id"]),
                    )
                return
            stamp = now()
            identity = uuid.uuid4().hex
            payload = {
                **body,
                "appid": json.loads(job["appid"]),
                "event_id": identity,
                "event": event.split(":", 1)[0],
            }
            if job["has_id"]:
                payload["id"] = json.loads(job["id_json"])
            topic = job["output_topic"]
            encoded = canonical(payload)
            db.execute(
                "INSERT OR IGNORE INTO "
                "kafka_outbox(id,inbox,connection,event,topic,payload,payload_bytes,state,created,updated)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    job["id"],
                    job["connection"],
                    event,
                    topic,
                    encoded,
                    len(encoded.encode()),
                    "ready",
                    stamp,
                    stamp,
                ),
            )
            if finish:
                db.execute(
                    "UPDATE kafka_inbox SET "
                    "state='done',payload='',payload_bytes=0,id_json=NULL,updated=? WHERE id=?",
                    (stamp, job["id"]),
                )

    def next_delivery(self, connection):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM kafka_outbox WHERE connection=? AND state='ready' AND "
                "retry_at<=? ORDER BY created LIMIT 1",
                (connection, time.time()),
            ).fetchone()
        return dict(row) if row else None

    def delivered(self, identity, error=""):
        with self.db() as db:
            if not error:
                db.execute(
                    "UPDATE kafka_outbox SET "
                    "state='sent',payload='',payload_bytes=0,error='',updated=? WHERE id=?",
                    (now(), identity),
                )
            else:
                db.execute(
                    "UPDATE kafka_outbox SET attempts=attempts+1,state=CASE WHEN attempts>=2 "
                    "THEN 'failed' ELSE 'ready' END,retry_at=?,error=?,updated=? WHERE id=?",
                    (time.time() + 2, error[:1000], now(), identity),
                )

    def retry(self, connection):
        with self.db() as db:
            db.execute(
                "UPDATE kafka_outbox SET state='ready',attempts=0,retry_at=0,error='' WHERE "
                "connection=? AND state='failed'",
                (connection,),
            )

    def prune(self):
        with self.db() as db:
            # Only content-free receipts are removed. Per-partition watermarks keep deduplication.
            db.execute(
                "DELETE FROM kafka_outbox WHERE state='sent' AND updated<datetime('now','-7 days')"
            )
            db.execute(
                "DELETE FROM kafka_inbox WHERE state='done' AND NOT EXISTS(SELECT 1 FROM "
                "kafka_outbox WHERE inbox=kafka_inbox.id)"
            )
        with self.db() as db:
            db.execute("PRAGMA incremental_vacuum(128)")
