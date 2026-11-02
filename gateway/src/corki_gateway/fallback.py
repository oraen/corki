"""Configured unsupported-format replies, without Agent turns or automatic replay."""

import asyncio
import time

from .errors import DeliveryError
from .messages import send_logged

DEFAULT_REPLY = "暂不支持该格式的信息"


def reserve(store, channel, message):
    with store.db() as db:
        return bool(
            db.execute(
                "INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,'sending',?,'')",
                (channel, message["id"], "", "", time.time()),
            ).rowcount
        )


async def reply(host, channel, message):
    try:
        row = host.store.connection(channel)
        if not row or not row["enabled"] or host.stopping:
            raise asyncio.CancelledError()
        if not host.store.allows_message(row, message, remember=False):
            host.store.finish(channel, message["id"], "access_denied")
            host.access_notifier.schedule(channel)
            return
        text = row["config"].get("unsupported_reply") or DEFAULT_REPLY
        await send_logged(
            host,
            channel,
            {
                "message": message,
                "text": text,
                "record_key": "fallback:" + message["id"],
                **({"fallback_proactive": True} if row["type_id"] == "dingtalk" else {}),
            },
            timeout=45,
            kind="fallback",
        )
        host.store.finish(channel, message["id"], "completed")
    except asyncio.CancelledError:
        host.store.finish(channel, message["id"], "cancelled", "兜底回复发送已停止，送达情况未知")
        raise
    except Exception as error:
        reason = (
            str(error)
            if isinstance(error, DeliveryError)
            else "兜底回复未获发送确认，请检查渠道连接"
        )
        host.store.finish(channel, message["id"], "failed", reason)
