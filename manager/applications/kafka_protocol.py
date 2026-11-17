"""Small JSON envelope for dedicated and global application subscriptions."""

import json
import re

from .models import AppError

MAX_MESSAGE = 1024 * 1024


def decode_message(applications, connection, value):
    envelope = dict(appid=None, app=None, output_topic=connection["output_topic"])
    try:
        if value is None or len(value) > MAX_MESSAGE:
            raise AppError("消息为空或超过 1 MiB")
        data = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(data, dict):
            raise AppError("消息必须是 JSON 对象")
        if "id" in data:
            if len(json.dumps(data["id"], ensure_ascii=False).encode()) > 4096:
                raise AppError("请求标识 id 过长")
            envelope["id"] = data["id"]
        owner = connection["owner"]
        if owner:
            with applications.store.db() as db:
                row = db.execute("SELECT id,public_id FROM apps WHERE id=?", (owner,)).fetchone()
            if not row:
                raise AppError("应用不存在", 404)
            appid = row["public_id"]
            envelope["appid"] = appid
            if "appid" in data and str(data["appid"]) != str(appid):
                raise AppError("消息 appid 与独立 Kafka 接入所属应用不一致", 403)
        else:
            appid = data.get("appid")
            envelope["appid"] = (
                appid if isinstance(appid, (str, int)) and not isinstance(appid, bool) else None
            )
            if (
                isinstance(appid, bool)
                or not isinstance(appid, (str, int))
                or not re.fullmatch(r"[0-9]{1,18}", str(appid))
            ):
                raise AppError("全局 Kafka 消息必须包含数字 appid")
        envelope["appid"] = int(appid)
        with applications.store.db() as db:
            row = db.execute(
                "SELECT id,enabled FROM apps WHERE public_id=?", (int(appid),)
            ).fetchone()
        if not row:
            raise AppError("应用不存在", 404)
        envelope["app"] = row["id"]
        if not owner and not applications.access.app(row["id"])["global_kafka"]:
            envelope["ignore"] = True
            return envelope
        if not row["enabled"]:
            raise AppError("应用已关闭", 409)
        action = data.get("action", "invoke")
        if action not in ("invoke", "cancel", "respond"):
            raise AppError("不支持的 Kafka action")
        allowed = (
            {"appid", "id", "action", "input", "session_id"}
            if action == "invoke"
            else {"appid", "id", "action", "run_id", "interaction_id", "decision", "response"}
        )
        if set(data) - allowed:
            raise AppError("Kafka 消息包含不支持的字段")
        if action == "invoke":
            text = data.get("input")
            if not isinstance(text, str) or not text.strip() or len(text) > 262144:
                raise AppError("input 必须是非空文本，最长 256K 字符")
            session = data.get("session_id")
            if session is not None and (
                not isinstance(session, str) or not re.fullmatch(r"[a-f0-9]{32}", session)
            ):
                raise AppError("session_id 无效")
        else:
            if not isinstance(data.get("run_id"), str) or not re.fullmatch(
                r"[a-f0-9]{32}", data["run_id"]
            ):
                raise AppError("run_id 无效")
            if action == "respond" and (
                data.get("decision") not in ("accept", "decline")
                or not isinstance(data.get("interaction_id"), str)
            ):
                raise AppError("请提供有效的 interaction_id 和 decision")
        envelope["data"] = {k: v for k, v in data.items() if k not in ("id", "appid")}
        envelope["data"]["action"] = action
    except (ValueError, TypeError, UnicodeError, OverflowError) as error:
        envelope["error"] = (
            str(error) if isinstance(error, AppError) else "消息不是有效的 UTF-8 JSON"
        )
    return envelope
