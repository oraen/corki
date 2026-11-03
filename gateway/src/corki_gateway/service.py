"""Account supervisors, durable admission, bounded per-conversation dispatch."""

import asyncio
import contextlib
import json
import time
from pathlib import Path

from . import package_jobs
from .access_notifier import AccessNotifier
from .connector import Connector
from .messages import manual_send, send_logged
from .plugins import (
    cleanup_legacy_dependencies,
    dependencies_ready,
    install,
    installation_status,
    migrate_dependencies,
)
from .recall import recall_message
from .reply_card import ReplyCard, card_options
from .standard import define, is_standard
from .standard_connector import StandardConnector
from .store import BUILTINS, Store


class Host:
    def __init__(self, home, *, runner=None, connector_factory=Connector, online=True):
        self.store = Store(home)
        self.online = online
        self.deliveries = {}
        self.reply_cards = {}
        self.runner = runner
        self.connector_factory = connector_factory
        self.connectors, self.monitors, self.tasks, self.statuses = {}, {}, {}, {}
        self.runtimes, self.interactions, self.locks = {}, {}, {}
        self.task_senders = {}
        self.access_revocations = set()
        self.admin_stops = set()
        self.access_notifier = AccessNotifier(self)
        self.lock_users = {}
        self.live = {}
        self.slots = asyncio.Semaphore(4)
        self.mutation = asyncio.Lock()
        self.stopping = False
        self.changing = set()
        self.manual_tasks = {}

    def component_busy(self, identity):
        return (
            package_jobs.snapshot(self.store).get(identity, {}).get("status") in package_jobs.ACTIVE
        )

    def require_component(self, identity):
        if self.component_busy(identity):
            raise ValueError("此渠道正在安装或卸载，请稍后重试")
        if identity in BUILTINS and not dependencies_ready(self.store, identity):
            raise ValueError("请先安装此渠道")

    async def start(self):
        await asyncio.to_thread(migrate_dependencies, self.store)
        await asyncio.to_thread(cleanup_legacy_dependencies, self.store)
        self.store.recover()
        self.store.recover_access_notices()
        self.store.messages.recover()
        if self.runner is None:
            from .agent import Agent

            self.runner = Agent(self).run
        for row in self.store.connections():
            if row["enabled"] and not self.component_busy(row["type_id"]):
                self.monitors[row["id"]] = asyncio.create_task(self.supervise(row["id"]))

    async def supervise(self, identity):
        failures = 0
        while not self.stopping and (self.store.connection(identity) or {}).get("enabled"):
            row = self.store.connection(identity)
            factory = StandardConnector if is_standard(row) else self.connector_factory
            connector = factory(self, row)
            started = time.monotonic()
            self.connector_status(identity, {"state": "connecting"})
            self.connectors[identity] = connector
            try:
                await connector.start()
                await asyncio.shield(connector.reader)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                await self.access_notifier.stop(identity)
                self.connectors.pop(identity, None)
                await connector.close()
            if self.stopping or not (self.store.connection(identity) or {}).get("enabled"):
                break
            failures = 0 if time.monotonic() - started > 60 else failures + 1
            self.connector_status(identity, {"state": "error", "code": "reconnect"})
            await asyncio.sleep(min(60, 2 ** min(failures, 6)))

    def connector_status(self, identity, value):
        if not isinstance(value, dict) or value.get("state") not in (
            "connecting",
            "online",
            "qr",
            "verification",
            "login",
            "scanned",
            "error",
        ):
            return
        # No arbitrary HTML, URLs or third-party messages enter the management page.
        item = {"state": value["state"]}
        if value["state"] in ("login", "scanned"):
            row = self.store.connection(identity)
            if not row or row["type_id"] != "wechat":
                return
            item["message"] = {
                "expired": "二维码已过期，请点击重新获取",
                "credentials": "微信登录已失效，请重新扫码登录",
                "login_failed": "获取二维码失败，请点击重试",
                "already_bound": "微信提示此账号已绑定。请在手机微信中检查原有连接，再重新扫码。",
            }.get(value.get("code"), "点击扫码登录后获取二维码")
            if value["state"] == "scanned":
                item["message"] = "已扫码，请在手机微信上确认连接。"
        qr = value.get("qr", "")
        if isinstance(qr, str) and qr.startswith("data:image/png;base64,") and len(qr) < 180000:
            item["qr"] = qr
        code = value.get("code")
        messages = {
            "credentials": "登录已失效，请重新连接",
            "network": "网络连接失败，正在重试",
            "verify": "请输入手机上显示的验证码",
            "verify_retry": "验证尚未通过，请检查手机上的验证码后重新提交。",
            "expired": "二维码已过期，请重新连接",
            "dependencies": "请先安装渠道组件",
            "duplicate": "此平台账号已在其他接入中启用",
            "protocol": "连接器通信异常",
            "interrupted": "上次执行中断，队列已暂停，请进入管理处理",
            "http_pull": "拉取接口连接失败或返回格式无效，正在重试",
            "http": "渠道接口请求失败",
            "reconnect": "连接中断，正在重连",
            "feishu_connection": "飞书长连接异常，正在重试。请检查网络、应用凭据及飞书 / Lark 区域。",
            "feishu_setup": "飞书连接超时。请确认已启用机器人、使用长连接接收事件，并检查应用凭据与网络。",
        }
        if code in messages and value["state"] != "login":
            item["message"] = messages[code]
        detail = self.statuses.get(identity, {}).get("detail")
        incoming = value.get("detail")
        if value["state"] == "error" and isinstance(incoming, dict):
            detail = {"time": time.time()}
            for key, limit in (
                ("url", 2048),
                ("method", 16),
                ("stage", 100),
                ("response", 8192),
                ("error", 300),
                ("content_type", 200),
            ):
                if isinstance(incoming.get(key), str):
                    detail[key] = incoming[key][:limit]
            code = incoming.get("status")
            if type(code) is int and 100 <= code <= 599:
                detail["status"] = code
        if detail:
            item["detail"] = detail
        activity = value.get("activity")
        if isinstance(activity, dict) and activity.get("phase") in (
            "received",
            "accepted",
            "ignored_group",
            "ignored_type",
        ):
            item["activity"] = {
                "time": time.time(),
                "phase": activity["phase"],
                "message_type": activity.get("message_type")
                if activity.get("message_type")
                in ("text", "richText", "picture", "file", "audio", "video")
                else "other",
                "conversation_type": activity.get("conversation_type")
                if activity.get("conversation_type") in ("1", "2")
                else "other",
            }
        elif "activity" in self.statuses.get(identity, {}):
            item["activity"] = self.statuses[identity]["activity"]
        card_status = value.get("card")
        if isinstance(card_status, dict) and card_status.get("state") == "fallback":
            item["card"] = {"state": "fallback"}
            code = card_status.get("code", "")
            if isinstance(code, str) and len(code) <= 100:
                item["card"]["code"] = code
            if type(card_status.get("status")) is int:
                item["card"]["status"] = card_status["status"]
        elif not isinstance(card_status, dict) and "card" in self.statuses.get(identity, {}):
            item["card"] = self.statuses[identity]["card"]
        self.statuses[identity] = item
        if value["state"] == "online":
            self.access_notifier.schedule(identity)

    def snapshot(self):
        return {
            **self.store.snapshot(),
            "running": self.online and not self.stopping,
            "installed": installation_status(self.store),
            "package_jobs": package_jobs.snapshot(self.store),
            "statuses": self.statuses,
            "active": len(self.tasks) + len(self.manual_tasks),
            "interactions": list(self.interactions.values()),
        }

    async def inbound(self, channel, message):
        row = self.store.connection(channel)
        if (
            self.stopping
            or channel in self.changing
            or not row
            or not row["enabled"]
            or self.component_busy(row["type_id"])
        ):
            raise ValueError("渠道已关闭")
        if not isinstance(message, dict):
            raise ValueError("消息格式无效")
        if "unsupported" in message and type(message["unsupported"]) is not bool:
            raise ValueError("消息类型标记无效")
        for key in ("id", "sender", "conversation", "text"):
            value = message.get(key)
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > (100000 if key == "text" else 300)
            ):
                raise ValueError("消息字段无效：" + key)
        # First-party adapters admit direct messages only. Custom adapters must
        # declare this explicitly; no group identity may be silently treated as DM.
        if message.get("kind") != "direct":
            raise ValueError("当前仅支持私聊消息")
        if row["type_id"] == "dingtalk":
            from .images import validate_refs

            refs = validate_refs(message)
            self.store.messages.contact(channel, message)
            self.store.messages.record(
                channel,
                message,
                "inbound:" + message["id"],
                "user",
                "user",
                message["text"],
                images=[
                    {
                        "status": "pending",
                        **(
                            {"position": message["image_positions"][i]}
                            if i < len(message.get("image_positions", []))
                            else {}
                        ),
                    }
                    for i, _ in enumerate(refs)
                ],
            )
        allowed = self.store.allows_message(row, message)
        if row["config"].get("session_validation") == "true":
            self.access_notifier.schedule(channel)
        if not allowed:
            if row["type_id"] == "dingtalk" and message.get("image_refs"):
                self.store.images.update(
                    channel,
                    message,
                    [
                        {"status": "failed", "error": "会话未授权，未下载图片"}
                        for _ in message["image_refs"]
                    ],
                )
            self.store.deny_message(channel, message)
            return {"accepted": True, "access_denied": True}
        if len(self.tasks) >= 100:
            raise ValueError("网关繁忙，请稍后重试")
        if message.get("unsupported"):
            from .fallback import reply, reserve

            if not reserve(self.store, channel, message):
                return {"accepted": True, "duplicate": True}
            if row["type_id"] == "dingtalk" and message.get("image_refs"):
                self.store.images.update(
                    channel,
                    message,
                    [
                        {"status": "failed", "error": "该消息包含不支持的附件类型"}
                        for _ in message["image_refs"]
                    ],
                )
            key = (channel, message["id"])
            task = asyncio.create_task(reply(self, channel, message))
            self.tasks[key] = task
            self.task_senders[key] = message["sender"]

            def done_fallback(_):
                self.tasks.pop(key, None)
                self.task_senders.pop(key, None)

            task.add_done_callback(done_fallback)
            return {"accepted": True, "unsupported": True}
        workdir = row["config"].get("workdir") or str(self.store.home / "main/default")
        Path(workdir).mkdir(parents=True, exist_ok=True)
        thread = self.store.admit(channel, message, workdir)
        if thread is None:
            return {"accepted": True, "duplicate": True}
        key = (channel, message["id"])
        task = asyncio.create_task(self.execute(channel, message, thread, workdir))
        self.tasks[key] = task
        self.task_senders[key] = message["sender"]

        def done(_):
            self.tasks.pop(key, None)
            self.task_senders.pop(key, None)

        task.add_done_callback(done)
        return {"accepted": True, "thread": thread}

    async def execute(self, channel, message, thread, workdir):
        lock = self.locks.setdefault(thread, asyncio.Lock())
        self.lock_users[thread] = self.lock_users.get(thread, 0) + 1
        sending = False
        card = None
        delivery_context = (channel, message)
        try:
            async with lock, self.slots:
                # Revocation and channel stop take effect before the next Agent turn.
                current = self.store.connection(channel)
                if not (current or {}).get("enabled"):
                    raise asyncio.CancelledError()
                if not self.store.allows_message(current, message, remember=False):
                    if current["type_id"] == "dingtalk" and message.get("image_refs"):
                        self.store.images.update(
                            channel,
                            message,
                            [
                                {"status": "failed", "error": "会话未授权，未下载图片"}
                                for _ in message["image_refs"]
                            ],
                        )
                    self.store.finish(channel, message["id"], "access_denied")
                    self.access_notifier.schedule(channel)
                    return
                self.deliveries[thread] = delivery_context
                self.store.finish(channel, message["id"], "running")
                if message.get("media_error"):
                    if message.get("image_refs"):
                        self.store.images.update(
                            channel,
                            message,
                            [
                                {"status": "failed", "error": message["media_error"]}
                                for _ in message["image_refs"]
                            ],
                        )
                    raise ValueError("Agent " + message["media_error"])
                attachments = (
                    await self.store.images.load(channel, message, self.connectors.get(channel))
                    if current["type_id"] == "dingtalk"
                    else ()
                )
                options = card_options(current)
                if options["enabled"] and self.connectors.get(channel):
                    card = ReplyCard(self.connectors[channel], message, options)
                    self.reply_cards[thread] = card
                    card.offer()
                inputs = (
                    {
                        "attachments": attachments,
                        "image_positions": message.get("image_positions", ()),
                    }
                    if attachments
                    else {}
                )
                answer = await asyncio.wait_for(
                    self.runner(thread, message["text"], workdir, channel, **inputs), timeout=1800
                )
                if not (self.store.connection(channel) or {}).get("enabled"):
                    raise asyncio.CancelledError()
                connector = self.connectors.get(channel)
                if connector is None:
                    raise ValueError("渠道已断开，结果保留在 Corki 会话中")
                if card:
                    await card.settle()
                self.store.finish(channel, message["id"], "sending")
                # A timeout after sending is ambiguous. Do not automatically resend it.
                sending = True
                text = (
                    answer
                    if len(answer) <= 120000
                    else answer[:120000] + "\n（回复较长，完整内容已保存在 Corki 会话中）"
                )
                delivery = await send_logged(
                    self,
                    channel,
                    {
                        "message": message,
                        "text": text,
                        "final": True,
                        "card": card.options if card else {"enabled": False},
                    },
                    timeout=120,
                )
                if not isinstance(delivery, dict) or delivery.get("sent") is not True:
                    raise ValueError("渠道未确认投递成功，结果保留在 Corki 会话中")
                self.store.finish(channel, message["id"], "completed")
        except asyncio.CancelledError:
            admin_stop = (channel, message["id"]) in self.admin_stops
            stop_text = "回答已被管理员终止" if admin_stop else "回答已停止"
            card_notified = await card.settle(stop_text, stopped=True) if card else False
            revoked = (channel, message["id"]) in self.access_revocations
            self.store.finish(
                channel,
                message["id"],
                "access_denied" if revoked else "cancelled",
                "会话访问已禁用" if revoked else "执行已停止，已完成的操作不会撤销",
            )
            if admin_stop:
                key = "admin-stop:" + message["id"]
                if card_notified:
                    # The existing AI card already delivered the notice. Retain it
                    # in transport history without sending a second text message.
                    self.store.messages.record(
                        channel, message, key, "assistant", "system", stop_text, "sent"
                    )
                    self.store.messages.finish(channel, key, "sent", delivery_receipt=card.receipt)
                else:
                    with contextlib.suppress(Exception):
                        await send_logged(
                            self, channel,
                            {"message": message, "text": stop_text, "record_key": key,
                             "fallback_proactive": True, "card": {"enabled": False}},
                            timeout=15, kind="system",
                        )
        except TimeoutError:
            if card:
                await card.settle("任务处理超时，请稍后重试。")
            self.store.finish(
                channel, message["id"], "failed", "执行或投递超时，未自动重试；请查看会话记录"
            )
        except Exception as error:
            # Never log model payloads, third-party exceptions or authentication URLs.
            safe = (
                str(error)
                if isinstance(error, ValueError) and str(error).startswith(("Agent", "渠道"))
                else "任务失败，请查看 Corki 会话日志"
            )
            card_notified = await card.settle(safe) if card else False
            failed_delivery = self.store.http_queue.get(channel, message["id"]) if sending else None
            state = (
                "delivery_failed"
                if failed_delivery and failed_delivery["phase"] == "delivery_failed"
                else "failed"
            )
            self.store.finish(channel, message["id"], state, safe)
            if (
                not sending
                and (not card_notified or message.get("image_refs") or message.get("media_error"))
                and self.deliveries.get(thread) is delivery_context
                and not is_standard(self.store.connection(channel) or {})
            ):
                await self.notify(thread, safe)
        finally:
            if card:
                await card.close()
                if self.reply_cards.get(thread) is card:
                    self.reply_cards.pop(thread, None)
            self.access_revocations.discard((channel, message["id"]))
            self.admin_stops.discard((channel, message["id"]))
            if self.deliveries.get(thread) is delivery_context:
                self.deliveries.pop(thread, None)
            # Queued users share the same lock until the final waiter completes.
            self.lock_users[thread] -= 1
            if self.lock_users[thread] == 0:
                self.lock_users.pop(thread, None)
                self.locks.pop(thread, None)

    async def notify(self, thread, text):
        value = self.deliveries.get(thread)
        if not value:
            return
        channel, message = value
        connector = self.connectors.get(channel)
        row = self.store.connection(channel)
        if connector and row and row["enabled"]:
            with contextlib.suppress(Exception):
                import uuid

                await send_logged(
                    self,
                    channel,
                    {"message": message, "text": text, "record_key": "system:" + uuid.uuid4().hex},
                    timeout=15,
                    kind="system",
                )

    async def stop_channel(self, identity):
        manual = [task for (channel, _), task in self.manual_tasks.items() if channel == identity]
        for task in manual:
            task.cancel()
        await asyncio.gather(*manual, return_exceptions=True)
        await self.access_notifier.stop(identity)
        keys = [key for key, task in self.tasks.items() if key[0] == identity and not task.done()]
        work = [self.tasks[key] for key in keys]
        monitor = self.monitors.pop(identity, None)
        # Cancel Agent work immediately, not after connector shutdown timeouts.
        for task in work:
            task.cancel()
        if monitor:
            monitor.cancel()
            work.append(monitor)
        await asyncio.gather(*work, return_exceptions=True)
        for key in keys:
            self.store.finish(key[0], key[1], "cancelled", "执行已停止")
        self.statuses.pop(identity, None)

    def reply_activity(self, channel):
        # Deliveries only contains work that owns a session lock and execution slot.
        # Queued messages and authorization notices are not Agent replies.
        activity = {}
        for owner, message in self.deliveries.values():
            if owner != channel:
                continue
            task = self.tasks.get((owner, message["id"]))
            if task is None or task.done():
                continue
            row = activity.setdefault(message["sender"], {"message_ids": [], "stopping": False})
            row["message_ids"].append(message["id"])
            row["stopping"] |= bool(task.cancelling())
        return activity

    async def change(self, data):
        action, identity = data.get("action"), data.get("id")
        if action == "recall-message":
            return await recall_message(self, data)
        if action == "manual-send":
            return await manual_send(self, data)
        if action == "application-message":
            return self.store.application_message(identity, data.get("sender"))
        if action == "applications":
            result = await asyncio.to_thread(
                self.store.applications,
                identity,
                data.get("before"),
                name=data.get("name", ""),
                status=data.get("status", ""),
            )
            result["reply_activity"] = self.reply_activity(identity)
            return result
        if action == "stop-reply":
            sender, message_ids = data.get("sender"), data.get("message_ids")
            if not isinstance(sender, str) or not 1 <= len(sender) <= 300:
                raise ValueError("用户 ID 无效")
            if (
                not isinstance(message_ids, list) or not 1 <= len(message_ids) <= 4
                or any(not isinstance(value, str) or not value or len(value) > 1000 for value in message_ids)
            ):
                raise ValueError("回复标识无效")
            if not self.store.connection(identity):
                raise ValueError("接入不存在")
            active = self.reply_activity(identity).get(sender, {}).get("message_ids", [])
            requested = 0
            for message_id in set(message_ids).intersection(active):
                task = self.tasks[(identity, message_id)]
                # Never cancel twice during cleanup, or target a newer reply after
                # the confirmation dialog's original reply has already finished.
                if not task.cancelling():
                    self.admin_stops.add((identity, message_id))
                    task.cancel()
                requested += 1
            return {"requested": requested, "reply_activity": self.reply_activity(identity)}
        if action == "conversation":
            from .history import history

            return await asyncio.to_thread(history, self.store, identity, data, self.live.copy())
        async with self.mutation:
            self.changing.add(identity)
            try:
                if action == "retry-access-notice":
                    if identity not in self.connectors:
                        raise ValueError("请先开启网关并启用此接入")
                    self.store.retry_access_notice(
                        identity, data.get("sender"), data.get("notice_id")
                    )
                    self.access_notifier.schedule(identity)
                elif action == "application-status":
                    sender, status = data.get("sender"), data.get("status")
                    self.store.set_application_status(identity, sender, status)
                    if status == "disabled":
                        keys = [
                            key
                            for key in self.tasks
                            if key[0] == identity
                            and self.task_senders.get(key) == sender
                            and not self.tasks[key].done()
                        ]
                        tasks = [self.tasks[key] for key in keys]
                        self.access_revocations.update(keys)
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        # A task cancelled before its coroutine starts has no finally block.
                        for key in keys:
                            self.store.finish(*key, "access_denied", "会话访问已禁用")
                            self.access_revocations.discard(key)
                    self.access_notifier.schedule(identity)
                elif action == "create-standard":
                    define(self.store, data)
                elif action == "skip-http-message":
                    row = self.store.connection(identity)
                    if not row or not is_standard(row):
                        raise ValueError("请选择标准 HTTP 接入")
                    pending = self.store.http_queue.first(identity)
                    if not pending or pending["phase"] != "blocked":
                        raise ValueError("没有暂停的消息")
                    self.store.http_queue.update(identity, pending["id"], "skipped")
                    connector = self.connectors.get(identity)
                    if connector:
                        connector.wake.set()
                elif action == "save-connection":
                    old = self.store.connection(identity) if identity else None
                    type_id = old["type_id"] if old else data.get("channel")
                    if old:
                        if self.component_busy(type_id):
                            raise ValueError("此渠道正在安装或卸载，请稍后重试")
                    else:
                        self.require_component(type_id)
                    # Validate before stopping a healthy connection; mutations are serialized.
                    if old:
                        self.store.save_connection(data, validate_only=True)
                        updated = self.store.validated_config(old, data.get("values", {}))
                        settings_only = self.store.execution_config(
                            old, updated
                        ) == self.store.execution_config(old, old["config"])
                        if settings_only:
                            self.store.save_connection(data)
                            self.access_notifier.schedule(identity)
                            return self.snapshot()
                        await self.stop_channel(identity)
                    try:
                        identity = self.store.save_connection(data)
                    except Exception:
                        if old and self.online and old["enabled"]:
                            self.monitors[identity] = asyncio.create_task(self.supervise(identity))
                        raise
                    if self.online and self.store.connection(identity)["enabled"]:
                        self.monitors[identity] = asyncio.create_task(self.supervise(identity))
                elif action == "configure":
                    # Original API remains usable for the migrated default account.
                    row = self.store.ensure_connection(identity)
                    if self.component_busy(row["type_id"]):
                        raise ValueError("此渠道正在安装或卸载，请稍后重试")
                    self.store.configure(identity, data.get("values"))
                    await self.stop_channel(identity)
                    if self.online and self.store.connection(identity)["enabled"]:
                        self.monitors[identity] = asyncio.create_task(self.supervise(identity))
                elif action == "channel":
                    if self.stopping:
                        raise ValueError("网关正在关闭，请稍后重试")
                    row = self.store.ensure_connection(identity)
                    enabled = data.get("enabled")
                    if enabled:
                        self.require_component(row["type_id"])
                    self.store.enable(identity, enabled)
                    await self.stop_channel(identity)
                    if enabled and self.online and not self.stopping:
                        self.monitors[identity] = asyncio.create_task(self.supervise(identity))
                elif action == "reset-current-conversation":
                    _, sender, _ = self.store.current_session_target(identity, data.get('session'))
                    keys = [key for key, task in self.tasks.items()
                            if key[0] == identity and not task.done()
                            and self.task_senders.get(key) == sender]
                    tasks = [self.tasks[key] for key in keys]
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    for key in keys:
                        self.store.finish(*key, 'cancelled', '会话已重置')
                    self.store.http_queue.skip_pending(identity, sender)
                    session = self.store.reset_current_session(identity, data.get('session'))
                    connector = self.connectors.get(identity)
                    if connector and hasattr(connector, 'wake'):
                        connector.wake.set()
                    return {**self.snapshot(), 'reset_session': session}
                elif action == "reset-conversation":
                    row = self.store.connection(identity)
                    if not row:
                        raise ValueError("接入不存在")
                    await self.stop_channel(identity)
                    self.store.http_queue.skip_pending(identity)
                    self.store.reset_conversation(identity)
                    if self.online and row["enabled"]:
                        self.monitors[identity] = asyncio.create_task(self.supervise(identity))
                elif action == "install":
                    await asyncio.to_thread(install, self.store, data.get("zip", ""))
                elif action in ("dependencies", "uninstall-dependencies"):
                    await asyncio.to_thread(package_jobs.start, self.store, identity, action)
                elif action == "prepare-uninstall":
                    if identity not in BUILTINS:
                        raise ValueError("请选择内置渠道")
                    for row in self.store.connections(identity):
                        self.store.enable(row["id"], False)
                        await self.stop_channel(row["id"])
                elif action == "connection-reconnect":
                    row = self.store.connection(identity)
                    if not row or row["type_id"] not in ("wechat", "feishu"):
                        raise ValueError("此操作仅支持微信和飞书接入")
                    if not self.online or not row["enabled"] or self.stopping:
                        raise ValueError("请先开启网关并启用此接入")
                    await self.stop_channel(identity)
                    self.connector_status(identity, {"state": "connecting"})
                    self.monitors[identity] = asyncio.create_task(self.supervise(identity))
                elif action == "wechat-login":
                    row = self.store.connection(identity)
                    if not row or row["type_id"] != "wechat":
                        raise ValueError("此操作仅支持微信接入")
                    if not self.online or not row["enabled"] or self.stopping:
                        raise ValueError("请先开启网关并启用此微信接入")
                    connector = self.connectors.get(identity)
                    if (
                        not connector
                        or self.statuses.get(identity, {}).get("state") == "connecting"
                    ):
                        raise ValueError("微信连接器正在启动，请稍后重试")
                    await connector.call("login", {"refresh": data.get("refresh") is True}, timeout=15)
                elif action == "verification":
                    code = data.get("code", "")
                    if not isinstance(code, str) or not code.isdigit() or len(code) > 12:
                        raise ValueError("请输入手机显示的数字验证码")
                    connector = self.connectors.get(identity)
                    if not connector:
                        raise ValueError("渠道未连接")
                    await connector.call("verify", {"code": code})
                elif action in ("delete-connection", "reset"):
                    row = (
                        self.store.ensure_connection(identity)
                        if action == "reset"
                        else self.store.connection(identity)
                    )
                    if not row:
                        raise ValueError("接入不存在")
                    if self.component_busy(row["type_id"]):
                        raise ValueError("此渠道正在安装或卸载，请稍后重试")
                    self.store.enable(identity, False)
                    await self.stop_channel(identity)
                    import shutil

                    directory = self.store.root / "accounts" / row["state_key"]
                    await asyncio.to_thread(shutil.rmtree, directory, True)
                    if action == "delete-connection":
                        self.store.http_queue.remove(identity)
                        self.store.remove_connection(identity)
                    else:
                        self.store.http_queue.remove(identity)
                        self.store.cursor(identity, "")
                        with self.store.db() as db:
                            db.execute(
                                "UPDATE connections SET config=?,account='' WHERE id=?",
                                (
                                    json.dumps({"workdir": row["config"].get("workdir", "")}),
                                    identity,
                                ),
                            )
                            for table in (
                                "peers",
                                "routes",
                                "receipts",
                                "access_requests",
                                "access_delivery",
                                "access_notice_queue",
                                "dingtalk_contacts",
                                "dingtalk_messages",
                                "dingtalk_assets",
                            ):
                                db.execute(f"DELETE FROM {table} WHERE channel=?", (identity,))
                elif action == "delete":
                    if identity in BUILTINS or not any(
                        r["id"] == identity for r in self.store.channels()
                    ):
                        raise ValueError("仅能删除自定义连接器")
                    if self.store.connections(identity):
                        raise ValueError("请先删除此连接器下的所有接入")
                    import shutil

                    await asyncio.to_thread(
                        shutil.rmtree, self.store.root / "plugins" / identity, True
                    )
                    with self.store.db() as db:
                        db.execute("DELETE FROM channels WHERE id=?", (identity,))
                elif action == "interaction":
                    self.respond(data)
                else:
                    raise ValueError("未知的网关操作")
            finally:
                self.changing.discard(data.get("id"))
        return self.snapshot()

    def respond(self, data):
        identity = data.get("request_id")
        request = self.interactions.get(identity)
        if not request or request["thread"] not in self.runtimes:
            raise ValueError("请求已结束")
        runtime = self.runtimes[request["thread"]]
        if request["kind"] == "question":
            if not runtime.respond_user_input(
                request["turn_id"], request["call_id"], data.get("response")
            ):
                raise ValueError("问题已失效")
        else:
            action = data.get("decision")
            if action not in ("accept", "decline", "cancel"):
                raise ValueError("无效的审批操作")
            if request["kind"] in ("shell_approval", "patch_approval"):
                runtime.respond_execution_approval(identity, action)
            else:
                runtime.respond_mcp_elicitation(
                    request["server_name"], identity, action, content=data.get("content", {})
                )
        self.interactions.pop(identity, None)

    async def close(self):
        self.stopping = True
        for task in list(self.manual_tasks.values()):
            task.cancel()
        await asyncio.gather(*list(self.manual_tasks.values()), return_exceptions=True)
        await self.access_notifier.stop()
        for identity in list(self.monitors):
            await self.stop_channel(identity)

        remaining = list(self.tasks.values())
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
        await asyncio.to_thread(cleanup_legacy_dependencies, self.store)
