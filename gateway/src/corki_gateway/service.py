"""Account supervisors, durable admission, bounded per-conversation dispatch."""

import asyncio
import contextlib
import json
import time
from pathlib import Path

from . import package_jobs
from .connector import Connector
from .plugins import (
    cleanup_legacy_dependencies,
    dependencies_ready,
    install,
    installation_status,
    migrate_dependencies,
)
from .standard import define, is_standard
from .standard_connector import StandardConnector
from .store import BUILTINS, Store


class Host:
    def __init__(self, home, *, runner=None, connector_factory=Connector, online=True):
        self.store = Store(home)
        self.online = online
        self.deliveries = {}
        self.runner = runner
        self.connector_factory = connector_factory
        self.connectors, self.monitors, self.tasks, self.statuses = {}, {}, {}, {}
        self.runtimes, self.interactions, self.locks = {}, {}, {}
        self.lock_users = {}
        self.live = {}
        self.slots = asyncio.Semaphore(4)
        self.mutation = asyncio.Lock()
        self.stopping = False
        self.changing = set()

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
            "error",
        ):
            return
        # No arbitrary HTML, URLs or third-party messages enter the management page.
        item = {"state": value["state"]}
        qr = value.get("qr", "")
        if isinstance(qr, str) and qr.startswith("data:image/png;base64,") and len(qr) < 180000:
            item["qr"] = qr
        code = value.get("code")
        messages = {
            "credentials": "登录已失效，请重新连接",
            "network": "网络连接失败，正在重试",
            "verify": "请输入手机上显示的验证码",
            "expired": "二维码已过期，请重新连接",
            "dependencies": "请先安装渠道组件",
            "duplicate": "此平台账号已在其他接入中启用",
            "protocol": "连接器通信异常",
            "interrupted": "上次执行中断，队列已暂停，请进入管理处理",
            "http_pull": "拉取接口连接失败或返回格式无效，正在重试",
            "http": "渠道接口请求失败",
            "reconnect": "连接中断，正在重连",
        }
        if code in messages:
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
        self.statuses[identity] = item

    def snapshot(self):
        return {
            **self.store.snapshot(),
            "running": self.online and not self.stopping,
            "installed": installation_status(self.store),
            "package_jobs": package_jobs.snapshot(self.store),
            "statuses": self.statuses,
            "active": len(self.tasks),
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
        for key in ("id", "sender", "conversation", "text"):
            value = message.get(key)
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > (100000 if key == "text" else 300)
            ):
                raise ValueError("消息字段无效：" + key)
        # First-party adapters currently admit direct text only. Custom adapters must
        # declare this explicitly; no group identity may be silently treated as DM.
        if message.get("kind") != "direct":
            raise ValueError("当前仅支持私聊文本消息")
        if len(self.tasks) >= 100:
            raise ValueError("网关繁忙，请稍后重试")
        workdir = row["config"].get("workdir") or str(self.store.home / "main/default")
        Path(workdir).mkdir(parents=True, exist_ok=True)
        thread = self.store.admit(channel, message, workdir)
        if thread is None:
            return {"accepted": True, "duplicate": True}
        key = (channel, message["id"])
        task = asyncio.create_task(self.execute(channel, message, thread, workdir))
        self.tasks[key] = task

        def done(_):
            self.tasks.pop(key, None)

        task.add_done_callback(done)
        return {"accepted": True, "thread": thread}

    async def execute(self, channel, message, thread, workdir):
        lock = self.locks.setdefault(thread, asyncio.Lock())
        self.lock_users[thread] = self.lock_users.get(thread, 0) + 1
        sending = False
        delivery_context = (channel, message)
        try:
            async with lock, self.slots:
                # Revocation and channel stop take effect before the next Agent turn.
                if not (self.store.connection(channel) or {}).get("enabled"):
                    raise asyncio.CancelledError()
                self.deliveries[thread] = delivery_context
                self.store.finish(channel, message["id"], "running")
                answer = await asyncio.wait_for(
                    self.runner(thread, message["text"], workdir, channel), timeout=1800
                )
                if not (self.store.connection(channel) or {}).get("enabled"):
                    raise asyncio.CancelledError()
                connector = self.connectors.get(channel)
                if connector is None:
                    raise ValueError("渠道已断开，结果保留在 Corki 会话中")
                self.store.finish(channel, message["id"], "sending")
                # A timeout after sending is ambiguous. Do not automatically resend it.
                sending = True
                text = (
                    answer
                    if len(answer) <= 120000
                    else answer[:120000] + "\n（回复较长，完整内容已保存在 Corki 会话中）"
                )
                delivery = await connector.call(
                    "send", {"message": message, "text": text, "final": True}, timeout=120
                )
                if not isinstance(delivery, dict) or delivery.get("sent") is not True:
                    raise ValueError("渠道未确认投递成功，结果保留在 Corki 会话中")
                self.store.finish(channel, message["id"], "completed")
        except asyncio.CancelledError:
            self.store.finish(
                channel, message["id"], "cancelled", "执行已停止，已完成的操作不会撤销"
            )
        except TimeoutError:
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
            failed_delivery = self.store.http_queue.get(channel, message["id"]) if sending else None
            state = (
                "delivery_failed"
                if failed_delivery and failed_delivery["phase"] == "delivery_failed"
                else "failed"
            )
            self.store.finish(channel, message["id"], state, safe)
            if (
                not sending
                and self.deliveries.get(thread) is delivery_context
                and not is_standard(self.store.connection(channel) or {})
            ):
                await self.notify(thread, safe)
        finally:
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
                await connector.call("send", {"message": message, "text": text}, timeout=15)

    async def stop_channel(self, identity):
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

    async def change(self, data):
        action, identity = data.get("action"), data.get("id")
        if action == "conversation":
            from .history import history

            return await asyncio.to_thread(history, self.store, identity, data, self.live.copy())
        async with self.mutation:
            self.changing.add(identity)
            try:
                if action == "create-standard":
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
                    row = self.store.ensure_connection(identity)
                    enabled = data.get("enabled")
                    if enabled:
                        self.require_component(row["type_id"])
                    self.store.enable(identity, enabled)
                    await self.stop_channel(identity)
                    if enabled and self.online:
                        self.monitors[identity] = asyncio.create_task(self.supervise(identity))
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
                            for table in ("peers", "routes", "receipts"):
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
        for identity in list(self.monitors):
            await self.stop_channel(identity)

        remaining = list(self.tasks.values())
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
        await asyncio.to_thread(cleanup_legacy_dependencies, self.store)
