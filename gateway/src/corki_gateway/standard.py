"""Standard HTTP connector definitions and durable, ordered delivery state."""

import json
import re
import time
import uuid
from urllib.parse import quote, urlsplit

PARAMETER = re.compile(r"#\{([A-Za-z_][A-Za-z0-9_]{0,63})\}")
MAX_BATCH = 20


def is_standard(row):
    return row.get("manifest", {}).get("transport") == "standard-http"


def endpoint(value, *, template=False, disabled=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 8192 or any(ord(c) < 32 for c in value):
        raise ValueError("请填写有效的 HTTP 接口地址")
    probe = PARAMETER.sub("value", value) if template else value
    # A disabled placeholder is literal URL data, never a fragment or executable template.
    if disabled:
        probe = probe.replace("#", "%23").replace("{", "%7B").replace("}", "%7D")
    try:
        url = urlsplit(probe)
        valid = (
            url.scheme in ("http", "https")
            and url.hostname
            and not url.username
            and not url.password
            and not url.fragment
        )
        _ = url.port
    except ValueError:
        valid = False
    if not valid or "#{" in probe:
        raise ValueError("接口地址需以 http:// 或 https:// 开头，不支持用户信息或 URL 片段")
    if template:
        # Variables are URL-component values, not arbitrary changes to the destination host.
        authority = (
            value.split("://", 1)[1].split("/", 1)[0].split("?", 1)[0] if "://" in value else ""
        )
        if "#{" in authority:
            raise ValueError("参数仅支持路径和查询参数，不能用于协议、主机或端口")
    return probe


def define(store, data):
    name = data.get("name", "")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ValueError("请填写 1–60 字的连接器名称")
    disabled = data.get("disable_parameters", False)
    if type(disabled) is not bool:
        raise ValueError("禁用参数解析选项无效")
    urls = {
        key: data.get(key, "").strip() if isinstance(data.get(key), str) else ""
        for key in ("pull_url", "reply_url")
    }
    parameters = sorted(set(PARAMETER.findall(" ".join(urls.values())))) if not disabled else []
    if set(parameters) & {
        "workdir",
        "session_mode",
        "agent_provider",
        "agent_model",
        "session_validation",
        "validation_first_reply",
        "validation_approved_reply",
        "validation_disabled_reply",
        "http_retry_count",
        "http_disable_parameters",
    }:
        raise ValueError("参数名与接入配置保留字段重复")
    if len(parameters) > 24:
        raise ValueError("最多支持 24 个参数")
    retries = data.get("retry_count", 3)
    if type(retries) is not int or not 0 <= retries <= 20:
        raise ValueError("重试次数应为 0–20 的整数")
    for value in urls.values():
        endpoint(value, template=not disabled, disabled=disabled)
    identity = "http-" + uuid.uuid4().hex
    manifest = {
        "id": identity,
        "protocol": 1,
        "name": name.strip(),
        "transport": "standard-http",
        **urls,
        "disable_parameters": disabled,
        "retry_count": retries,
        "fields": [
            {"key": "param_" + key, "label": key, "secret": True, "required": True}
            for key in parameters
        ],
    }
    # Definition and parameterless account are created atomically.
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
            "SELECT 1 FROM channels WHERE lower(json_extract(manifest,'$.name'))=lower(?)",
            (name.strip(),),
        ).fetchone():
            raise ValueError("已存在同名连接器")
        db.execute(
            "INSERT INTO channels(id,manifest) VALUES(?,?)", (identity, json.dumps(manifest))
        )
        if not parameters:
            connection, binding = "account-" + uuid.uuid4().hex, uuid.uuid4().hex
            db.execute(
                "INSERT INTO conversations VALUES(?,?,?,?)",
                (binding, name.strip(), str(store.home / "main/default"), str(uuid.uuid4())),
            )
            db.execute(
                "INSERT INTO connections(id,type_id,name,state_key,conversation_id) "
                "VALUES(?,?,?,?,?)",
                (connection, identity, name.strip(), connection, binding),
            )
    return identity


def resolved(row):
    manifest = row["manifest"]
    result = {}
    for key in ("pull_url", "reply_url"):
        value = manifest[key]
        if manifest["disable_parameters"] or row["config"].get("http_disable_parameters") == "true":
            result[key] = endpoint(value, disabled=True)
        else:

            def substitute(match):
                raw = row["config"].get("param_" + match[1])
                if not isinstance(raw, str) or not raw:
                    raise ValueError("请填写参数 " + match[1])
                return quote(raw, safe="")

            result[key] = endpoint(PARAMETER.sub(substitute, value))
    return result


class Queue:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS http_inbox(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL,
                    id TEXT NOT NULL, payload TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'queued',
                    reply TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', updated REAL NOT NULL,
                    UNIQUE(channel,id));
                CREATE INDEX IF NOT EXISTS http_inbox_queue ON http_inbox(channel,sequence)
                    WHERE phase NOT IN ('sent','skipped');
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(http_inbox)")}
            for name in ("thread", "answer_id"):
                if name not in columns:
                    db.execute(f"ALTER TABLE http_inbox ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            db.execute(
                "CREATE INDEX IF NOT EXISTS http_inbox_pending_v2 ON http_inbox(channel,sequence) "
                "WHERE phase NOT IN ('sent','skipped','delivery_failed')"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS http_inbox_failures "
                "ON http_inbox(channel,thread,phase,answer_id)"
            )

    def bind_answer(self, channel, identity, thread, answer_id=""):
        with self.store.db() as db:
            db.execute(
                "UPDATE http_inbox SET thread=?,answer_id=? WHERE channel=? AND id=?",
                (thread, answer_id or "", channel, identity),
            )

    def failed_answers(self, channel, thread, identities):
        if not identities:
            return set()
        with self.store.db() as db:
            rows = db.execute(
                "SELECT answer_id FROM http_inbox WHERE channel=? AND thread=? "
                "AND phase='delivery_failed' AND answer_id IN ("
                + ",".join("?" for _ in identities)
                + ")",
                (channel, thread, *identities),
            )
            return {r[0] for r in rows}

    def failed_standalone(self, channel, thread, start=None, end=None):
        # Covers generated failure/empty-output replies without a canonical assistant item.
        with self.store.db() as db:
            query = (
                "SELECT * FROM http_inbox WHERE channel=? AND thread=? "
                "AND phase='delivery_failed' AND answer_id=''"
            )
            args = [channel, thread]
            if start is not None:
                query += " AND updated>=?"
                args.append(start)
            if end is not None:
                query += " AND updated<=?"
                args.append(end)
            return [dict(r) for r in db.execute(query + " ORDER BY updated DESC LIMIT 100", args)]

    def enqueue(self, channel, value):
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("messages"), list)
            or len(value["messages"]) > MAX_BATCH
        ):
            raise ValueError("拉取接口需返回 messages 数组，每批最多 20 条")
        cursor = value.get("next_cursor")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 65536):
            raise ValueError("next_cursor 应为文本，最长 65536 字符")
        messages = []
        for item in value["messages"]:
            if not isinstance(item, dict):
                raise ValueError("消息应为 JSON 对象")
            message = {
                "id": item.get("id"),
                "text": item.get("text"),
                "sender": item.get("sender", "remote"),
                "conversation": channel,
                "kind": "direct",
            }
            for key in ("id", "text", "sender"):
                field = message[key]
                if (
                    not isinstance(field, str)
                    or not field.strip()
                    or len(field) > (100000 if key == "text" else 300)
                ):
                    raise ValueError("消息的 id、text、sender 需为非空文本，且不能超过长度限制")
            messages.append(message)
        count = 0
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for message in messages:
                count += db.execute(
                    "INSERT OR IGNORE INTO http_inbox(channel,id,payload,updated) VALUES(?,?,?,?)",
                    (channel, message["id"], json.dumps(message), time.time()),
                ).rowcount
            if cursor is not None:
                db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?)", (channel, cursor))
            # Keep lightweight IDs for 30 days; acknowledged bodies are removed immediately.
            db.execute(
                "DELETE FROM http_inbox WHERE phase IN ('sent','skipped') AND updated<?",
                (time.time() - 30 * 86400,),
            )
        return count

    def first(self, channel):
        with self.store.db() as db:
            row = db.execute(
                "SELECT * FROM http_inbox WHERE channel=? "
                "AND phase NOT IN ('sent','skipped','delivery_failed') "
                "ORDER BY sequence LIMIT 1",
                (channel,),
            ).fetchone()
        return dict(row) if row else None

    def get(self, channel, identity):
        with self.store.db() as db:
            row = db.execute(
                "SELECT * FROM http_inbox WHERE channel=? AND id=?", (channel, identity)
            ).fetchone()
        return dict(row) if row else None

    def update(self, channel, identity, phase, *, error=""):
        with self.store.db() as db:
            db.execute(
                "UPDATE http_inbox SET phase=?,error=?,updated=? WHERE channel=? AND id=?",
                (phase, error, time.time(), channel, identity),
            )
            if phase in ("sent", "skipped"):
                db.execute(
                    "UPDATE http_inbox SET payload='{}',reply=NULL WHERE channel=? AND id=?",
                    (channel, identity),
                )

    def reply(self, channel, identity, text):
        with self.store.db() as db:
            payload = {
                "protocol": 1,
                "type": "reply",
                "connection_id": channel,
                "message_id": identity,
                "reply_id": uuid.uuid4().hex,
                "text": text,
            }
            db.execute(
                "UPDATE http_inbox SET reply=?,phase='ready',updated=? "
                "WHERE channel=? AND id=? AND reply IS NULL",
                (json.dumps(payload), time.time(), channel, identity),
            )
        return self.get(channel, identity)

    def attempt(self, channel, identity):
        with self.store.db() as db:
            db.execute(
                "UPDATE http_inbox SET attempts=attempts+1,updated=? WHERE channel=? AND id=?",
                (time.time(), channel, identity),
            )

    def recover(self, channel):
        with self.store.db() as db:
            legacy = db.execute(
                "SELECT i.id,i.reply,i.updated,r.thread FROM http_inbox i "
                "LEFT JOIN receipts r ON r.channel=i.channel AND r.id=i.id "
                "WHERE i.channel=? AND i.phase='blocked' AND i.reply IS NOT NULL",
                (channel,),
            ).fetchall()
            for item in legacy:
                db.execute(
                    "UPDATE http_inbox SET phase='delivery_failed',thread=?,error=? "
                    "WHERE channel=? AND id=?",
                    (item["thread"] or "", "回复未获确认，已达到重试次数", channel, item["id"]),
                )
            # Never automatically rerun an Agent after a crash with unknown side effects.
            db.execute(
                "UPDATE http_inbox SET phase='blocked',error=? WHERE channel=? AND phase='running'",
                ("上次执行中断，无法确认执行结果，请检查会话后跳过此消息", channel),
            )
        if legacy:
            from .history import bind_legacy_failures

            bind_legacy_failures(self.store, channel, legacy)

    def skip_pending(self, channel):
        with self.store.db() as db:
            db.execute(
                "UPDATE http_inbox SET phase='skipped',payload='{}',reply=NULL,updated=? "
                "WHERE channel=? AND phase NOT IN ('sent','skipped','delivery_failed')",
                (time.time(), channel),
            )

    def remove(self, channel):
        with self.store.db() as db:
            db.execute("DELETE FROM http_inbox WHERE channel=?", (channel,))
