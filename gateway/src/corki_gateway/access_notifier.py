"""Event-driven channel notices; no Agent work or perpetual polling thread."""

import asyncio
import json

from .errors import DeliveryError
from .messages import send_logged


class AccessNotifier:
    def __init__(self, host):
        self.host = host
        self.tasks = {}

    def schedule(self, channel):
        host = self.host
        row = host.store.connection(channel)
        if (
            host.stopping
            or channel in self.tasks
            or channel not in host.connectors
            or not row
            or not row["enabled"]
        ):
            return
        # Fake/in-process connectors need no readiness event. Real adapters report online.
        if host.statuses.get(channel, {}).get("state") not in (None, "online"):
            return
        self.tasks[channel] = asyncio.create_task(self.send(channel))

    async def send(self, channel):
        host = self.host
        try:
            while not host.stopping:
                row = host.store.connection(channel)
                connector = host.connectors.get(channel)
                if (
                    not row
                    or not row["enabled"]
                    or not connector
                    or row["config"].get("session_validation") != "true"
                ):
                    return
                notice = host.store.claim_access_notice(channel)
                if not notice:
                    return
                try:
                    message = json.loads(notice["message"])
                    proactive = message.pop("_access_notice_mode", None) == "proactive"
                    # A notice is not the Agent's final reply and must not acknowledge
                    # a standard HTTP inbox item or overwrite its saved answer.
                    async with asyncio.timeout(15):
                        result = await send_logged(
                            host,
                            channel,
                            {
                                "message": message,
                                "text": notice["text"],
                                "notice_id": notice["notice_id"],
                                **({"proactive": True} if proactive else {}),
                                **(
                                    {"fallback_proactive": True}
                                    if not proactive
                                    and row["type_id"] == "dingtalk"
                                    and notice["kind"] in ("approved", "disabled")
                                    else {}
                                ),
                            },
                            timeout=15,
                            kind="notice",
                        )
                    if not isinstance(result, dict) or result.get("sent") is not True:
                        raise DeliveryError("通知未获发送成功回执")
                    host.store.finish_access_notice(notice["notice_id"], "sent")
                except asyncio.CancelledError:
                    host.store.finish_access_notice(
                        notice["notice_id"], "failed", "通知发送中断，送达情况未知，未自动重发"
                    )
                    raise
                except Exception as error:
                    # Connector exceptions may contain expiring tokens/webhook URLs.
                    reason = "通知未获发送确认，请检查渠道连接或回复凭据是否过期"
                    if isinstance(error, DeliveryError):
                        reason = str(error)
                    elif isinstance(error, TimeoutError):
                        reason = "通知发送超时，送达情况未知"
                    host.store.finish_access_notice(
                        notice["notice_id"],
                        "failed",
                        reason,
                    )
        finally:
            self.tasks.pop(channel, None)

    async def stop(self, channel=None):
        tasks = [task for key, task in self.tasks.items() if channel is None or key == channel]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Tasks cancelled before starting do not execute send()'s finally block.
        for key, task in list(self.tasks.items()):
            if task.done():
                self.tasks.pop(key, None)
