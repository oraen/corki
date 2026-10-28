"""Gateway-owned configuration and receipts. Agent history stays in Corki's DB."""

import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .connections import Connections, DuplicateAccount

BUILTINS = {
    "whatsapp": {"name": "WhatsApp", "description": "扫码连接 · 私聊文本", "fields": []},
    "telegram": {
        "name": "Telegram",
        "description": "Bot Token 接入 · 私聊文本",
        "fields": [{"key": "token", "label": "Bot Token", "secret": True, "required": True}],
    },
    "feishu": {
        "name": "飞书",
        "description": "企业自建应用 · 长连接私聊文本",
        "fields": [
            {"key": "app_id", "label": "App ID", "required": True},
            {"key": "app_secret", "label": "App Secret", "secret": True, "required": True},
            {"key": "region", "label": "平台", "options": ["飞书", "Lark"]},
        ],
    },
    "dingtalk": {
        "name": "钉钉",
        "description": "",
        "fields": [
            {"key": "client_id", "label": "Client ID（AppKey）", "required": True},
            {
                "key": "client_secret",
                "label": "Client Secret（AppSecret）",
                "secret": True,
                "required": True,
            },
        ],
    },
    "wechat": {"name": "微信", "description": "iLink 扫码连接 · 私聊文本", "fields": []},
}
ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")


def root(home):
    path = Path(home) / "gateway"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


class Store(Connections):
    def __init__(self, home):
        self.home = Path(home).resolve()
        self.root = root(home)
        self.path = self.root / "gateway.db"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY, manifest TEXT NOT NULL,
                    config TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 0,
                    account TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS peers(channel TEXT, sender TEXT, name TEXT, status TEXT,
                    updated REAL, PRIMARY KEY(channel,sender));
                CREATE TABLE IF NOT EXISTS routes(key TEXT PRIMARY KEY,
                    thread TEXT NOT NULL, channel TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts(channel TEXT, id TEXT, route TEXT, thread TEXT,
                    status TEXT, updated REAL, error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(channel,id));
                CREATE INDEX IF NOT EXISTS receipt_time ON receipts(updated DESC);
                CREATE TABLE IF NOT EXISTS package_jobs(channel TEXT PRIMARY KEY, token TEXT,
                    action TEXT, status TEXT, stage TEXT, updated REAL);
                CREATE TABLE IF NOT EXISTS cursors(channel TEXT PRIMARY KEY, value TEXT);
            """)
            for key, value in BUILTINS.items():
                db.execute(
                    "INSERT INTO channels(id,manifest) VALUES(?,?) "
                    "ON CONFLICT(id) DO UPDATE SET manifest=excluded.manifest "
                    "WHERE channels.manifest != excluded.manifest",
                    (key, json.dumps({"id": key, "protocol": 1, **value})),
                )
        self.path.chmod(0o600)
        self.migrate_connections()
        self.ensure_conversations()
        from .standard import Queue

        self.http_queue = Queue(self)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def channels(self):
        with self.db() as db:
            return [
                {
                    **dict(row),
                    "manifest": json.loads(row["manifest"]),
                    "config": json.loads(row["config"]),
                }
                for row in db.execute("SELECT * FROM channels ORDER BY rowid")
            ]

    def channel(self, identity):
        return self.connection(identity) or next(
            (r for r in self.channels() if r["id"] == identity), None
        )

    def snapshot(self):
        channels = [
            {
                "id": r["id"],
                "name": r["manifest"]["name"],
                "description": r["manifest"].get("description", ""),
                "fields": r["manifest"].get("fields", []),
                "builtin": r["id"] in BUILTINS,
                "standard": r["manifest"].get("transport") == "standard-http",
                "retry_count": r["manifest"].get("retry_count", 3),
                "disable_parameters": r["manifest"].get("disable_parameters", False),
            }
            for r in self.channels()
        ]
        connections = []
        for row in self.connections():
            pending = (
                self.http_queue.first(row["id"])
                if row["manifest"].get("transport") == "standard-http"
                else None
            )
            fields = row["manifest"].get("fields", [])
            secrets = {f["key"] for f in fields if f.get("secret")}
            connections.append(
                {
                    "id": row["id"],
                    "channel": row["type_id"],
                    "name": row["name"],
                    "enabled": bool(row["enabled"]),
                    "conversation_id": row["conversation_id"],
                    "values": {k: v for k, v in row["config"].items() if k not in secrets},
                    "queue": {
                        "phase": pending["phase"],
                        "attempts": pending["attempts"],
                        "error": pending["error"],
                        "can_retry": bool(pending["reply"]),
                    }
                    if pending
                    else None,
                    "secrets": {k: bool(row["config"].get(k)) for k in secrets},
                }
            )
        with self.db() as db:
            receipts = [
                dict(r)
                for r in db.execute(
                    "SELECT channel,id,thread,status,updated,error FROM receipts "
                    "ORDER BY updated DESC LIMIT 30"
                )
            ]
        return {
            "channels": channels,
            "connections": connections,
            "conversations": self.conversation_list(),
            "receipts": receipts,
        }

    def validated_config(self, row, values):
        if not row or not isinstance(values, dict):
            raise ValueError("接入不存在或配置无效")
        allowed = {f["key"]: f for f in row["manifest"].get("fields", [])}
        allowed["workdir"] = {"label": "工作目录"}
        standard = row["manifest"].get("transport") == "standard-http"
        if standard:
            allowed.update({"http_disable_parameters": {}, "http_retry_count": {}})
        if values.keys() - allowed.keys():
            raise ValueError("未知的渠道配置字段")
        config = row["config"].copy()
        for key, value in values.items():
            if not isinstance(value, str) or len(value) > 8192:
                raise ValueError("配置应为文本，最长 8192 字符")
            field = allowed[key]
            if field.get("secret") and not value:
                continue  # Empty secret means keep; clearing uses explicit reset.
            if field.get("options") and value not in field["options"]:
                raise ValueError("配置选项无效")
            config[key] = value.strip()
        if config.get("workdir"):
            directory = Path(config["workdir"]).expanduser().resolve()
            default = (self.home / "main/default").resolve()
            if not directory.is_dir() and (directory != default or directory.exists()):
                raise ValueError("工作目录不存在")
            config["workdir"] = str(directory)
        if standard:
            if config.get("http_disable_parameters", "false") not in ("true", "false"):
                raise ValueError("参数解析选项无效")
            count = config.get("http_retry_count", str(row["manifest"]["retry_count"]))
            if not count.isdigit() or not 0 <= int(count) <= 20:
                raise ValueError("重试次数应为 0–20 的整数")
            from .standard import resolved

            resolved({**row, "config": config})
        return config

    def configure(self, identity, values):
        row = self.ensure_connection(identity)
        self.save_connection({"id": identity, "name": row["name"], "values": values})

    def enable(self, identity, enabled):
        row = self.ensure_connection(identity)
        if not row or type(enabled) is not bool:
            raise ValueError("渠道或开关无效")
        if enabled:
            for field in row["manifest"].get("fields", []):
                if (
                    row["manifest"].get("transport") == "standard-http"
                    and row["config"].get("http_disable_parameters") == "true"
                ):
                    continue
                if field.get("required") and not row["config"].get(field["key"]):
                    raise ValueError("请先填写 " + field["label"])
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                enabled
                and row["account"]
                and not row["account"].startswith("config-")
                and db.execute(
                    "SELECT 1 FROM connections WHERE type_id=? AND account=? AND enabled=1 "
                    "AND id<>?",
                    (row["type_id"], row["account"], identity),
                ).fetchone()
            ):
                raise DuplicateAccount("此平台账号已在其他接入中启用")
            db.execute("UPDATE connections SET enabled=? WHERE id=?", (enabled, identity))

    def admit(self, channel, message, workdir):
        # Include account/channel, conversation, sender and cwd; never merge principals.
        connection = self.connection(channel)
        parts = [channel, message["conversation"], message["sender"], workdir]
        if connection and connection["conversation_id"]:
            parts.append(connection["conversation_id"])
        key = hashlib.sha256(json.dumps(parts).encode()).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM receipts WHERE channel=? AND id=?", (channel, message["id"])
            ).fetchone()
            if prior:
                return None
            db.execute(
                "INSERT OR IGNORE INTO routes VALUES(?,?,?)", (key, str(uuid.uuid4()), channel)
            )
            row = db.execute(
                "SELECT conversation_id FROM connections WHERE id=?", (channel,)
            ).fetchone()
            group = (
                db.execute("SELECT * FROM conversations WHERE id=?", (row[0],)).fetchone()
                if row and row[0]
                else None
            )
            if group:
                if group["workdir"] != workdir:
                    raise ValueError("共享对话的工作目录不一致")
                db.execute("UPDATE routes SET thread=? WHERE key=?", (group["thread"], key))
            thread = db.execute("SELECT thread FROM routes WHERE key=?", (key,)).fetchone()[0]
            db.execute(
                "INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",
                (channel, message["id"], key, thread, "queued", time.time(), ""),
            )
            # Receipts are transport dedup metadata, not the canonical history.
            db.execute(
                "DELETE FROM receipts WHERE updated < ? AND status NOT IN ('queued','running')",
                (time.time() - 30 * 86400,),
            )
        return thread

    def finish(self, channel, identity, status, error=""):
        with self.db() as db:
            db.execute(
                "UPDATE receipts SET status=?,error=?,updated=? WHERE channel=? AND id=?",
                (status, error[:200], time.time(), channel, identity),
            )

    def recover(self):
        with self.db() as db:
            db.execute(
                "UPDATE receipts SET status='interrupted',error='网关退出，未自动重放任务' "
                "WHERE status IN ('queued','running','sending')"
            )

    def cursor(self, channel, value=None):
        with self.db() as db:
            if value is not None:
                if not isinstance(value, str) or len(value) > 65536:
                    raise ValueError("无效的游标")
                db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?)", (channel, value))
            row = db.execute("SELECT value FROM cursors WHERE channel=?", (channel,)).fetchone()
            return row[0] if row else ""

    def account(self, channel, identity):
        if not isinstance(identity, str) or not 1 <= len(identity) <= 300:
            raise ValueError("账号标识无效")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT account,type_id FROM connections WHERE id=?", (channel,)
            ).fetchone()
            if not row:
                raise ValueError("渠道不存在")
            if (
                not identity.startswith("config-")
                and db.execute(
                    "SELECT 1 FROM connections WHERE type_id=? AND account=? "
                    "AND enabled=1 AND id<>?",
                    (row["type_id"], identity, channel),
                ).fetchone()
            ):
                raise DuplicateAccount("此平台账号已在其他接入中启用")
            if row[0] and row[0] != identity:
                db.execute("DELETE FROM peers WHERE channel=?", (channel,))
                db.execute("DELETE FROM routes WHERE channel=?", (channel,))
                db.execute("DELETE FROM receipts WHERE channel=?", (channel,))
                db.execute("DELETE FROM cursors WHERE channel=?", (channel,))
            db.execute("UPDATE connections SET account=? WHERE id=?", (identity, channel))
        if row[0] and row[0] != identity:
            self.reset_conversation(channel)
