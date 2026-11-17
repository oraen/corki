"""Independent async Kafka host with durable admission and acknowledged outbox delivery."""

import asyncio
import contextlib
import json
import threading
import time
from pathlib import Path

from .access_store import IngressDisabled
from .kafka_protocol import decode_message
from .models import AppError
from .run_control import cancel_run, respond_run
from .run_view import run_view
from .store import ACTIVE


class KafkaService:
    def __init__(self, applications, *, client_factory=None):
        self.apps = applications
        self.store = applications.access
        self.client_factory = client_factory
        self.guard = threading.RLock()
        self.lock = None
        self.loop = self.thread = None
        self.closed = False
        self.live = {}
        self.runners = {}
        self.active = {}
        self.suspended = set()
        self.recovered = False
        self.wake()

    def ensure_owner(self):
        with self.guard:
            if self.closed:
                raise AppError("Kafka 服务正在关闭", 503)
            if self.lock:
                return
            from corki.storage.file_lock import open_lock, try_lock

            lock = open_lock(self.apps.store.root / "kafka.lock")
            if not try_lock(lock):
                lock.close()
                raise AppError("Kafka 接入由另一个管理服务运行，请使用原管理页面", 409)
            self.lock = lock
            if not self.recovered:
                self.store.recover()
                self.recovered = True

    def effective(self):
        global_enabled = self.store.global_state()["enabled"]
        with self.apps.store.db() as db:
            enabled = {r["id"] for r in db.execute("SELECT id FROM apps")}
        with self.guard:
            suspended = set(self.suspended)
        return {
            c["id"]: c
            for c in self.store.connections(secrets=True)
            if c["enabled"]
            and (
                (not c["owner"] and global_enabled)
                or (c["owner"] in enabled and c["owner"] not in suspended)
            )
        }

    def wake(self):
        with self.guard:
            if self.closed:
                return
            if self.loop is None:
                if not self.effective():
                    return
                try:
                    self.ensure_owner()
                except AppError as error:
                    self.live["service"] = {"state": "failed", "error": str(error)}
                    return
                self.loop = asyncio.new_event_loop()
                self.changed = asyncio.Event()
                self.dispatch_wake = asyncio.Event()
                self.thread = threading.Thread(
                    target=self.loop.run_forever, name="corki-app-kafka", daemon=True
                )
                self.thread.start()
                self.main = asyncio.run_coroutine_threadsafe(self.run(), self.loop)
            self.loop.call_soon_threadsafe(self.changed.set)

    def status(self):
        with self.guard:
            return {key: dict(value) for key, value in self.live.items()}

    def set_status(self, connection, state, error=""):
        for field in ("sasl_password", "ssl_key_password"):
            secret = connection.get(field)
            if secret:
                error = error.replace(secret, "[已隐藏]")
        with self.guard:
            self.live[connection["id"]] = {"state": state, "error": error[:1000]}

    async def run(self):
        self.run_task = asyncio.current_task()
        dispatcher = asyncio.create_task(self.dispatch())
        try:
            while True:
                await self.changed.wait()
                self.changed.clear()
                desired = await asyncio.to_thread(self.effective)
                self.active = desired
                for identity, (revision, task) in list(self.runners.items()):
                    if identity not in desired or desired[identity]["revision"] != revision:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
                        self.runners.pop(identity, None)
                        with self.guard:
                            self.live.pop(identity, None)
                for identity, connection in desired.items():
                    if identity not in self.runners:
                        task = asyncio.create_task(self.connection_loop(connection))
                        self.runners[identity] = (connection["revision"], task)
                self.dispatch_wake.set()
        finally:
            dispatcher.cancel()
            for _, task in self.runners.values():
                task.cancel()
            await asyncio.gather(
                dispatcher, *(task for _, task in self.runners.values()), return_exceptions=True
            )
            self.runners.clear()
            self.active.clear()

    def configuration(self, connection):
        config = {
            "bootstrap.servers": ",".join(connection["brokers"]),
            "security.protocol": connection["security_protocol"],
            "socket.timeout.ms": 5000,
            "reconnect.backoff.max.ms": 5000,
            "log_level": 0,
            "error_cb": lambda error: self.set_status(connection, "failed", str(error)),
        }
        if connection["security_protocol"].startswith("SASL"):
            config.update(
                {
                    "sasl.mechanism": connection["sasl_mechanism"],
                    "sasl.username": connection["sasl_username"],
                    "sasl.password": connection["sasl_password"],
                }
            )
        if "SSL" in connection["security_protocol"]:
            for field in (
                "ssl_ca_location",
                "ssl_certificate_location",
                "ssl_key_location",
                "ssl_key_password",
            ):
                if connection[field]:
                    value = (
                        connection[field]
                        if field == "ssl_key_password"
                        else str(Path(connection[field]).expanduser())
                    )
                    config[field.replace("_", ".")] = value
        return config

    async def connection_loop(self, connection):
        backoff = 1
        self.set_status(connection, "connecting")
        while True:
            consumer = producer = sender = None
            try:
                if self.client_factory:
                    consumer, producer = self.client_factory(connection)
                else:
                    from confluent_kafka.aio import AIOConsumer, AIOProducer

                    config = self.configuration(connection)
                    consumer = AIOConsumer(
                        {
                            **config,
                            "group.id": connection["group_id"],
                            "enable.auto.commit": False,
                            "enable.auto.offset.store": False,
                            "auto.offset.reset": "earliest",
                            "max.poll.interval.ms": 300000,
                            "session.timeout.ms": 10000,
                            "queued.max.messages.kbytes": 2048,
                        }
                    )
                    producer = AIOProducer(
                        {
                            **config,
                            "enable.idempotence": True,
                            "acks": "all",
                            "delivery.timeout.ms": 10000,
                            "message.max.bytes": 8 * 1024 * 1024,
                        }
                    )
                await consumer.subscribe(connection["input_topics"])
                await consumer.list_topics(timeout=3)
                self.set_status(connection, "connected")
                sender = asyncio.create_task(self.deliver(connection, producer))
                backoff = 1
                pending = None
                paused = False
                last_health = 0
                while True:
                    if sender.done():
                        await sender
                        raise AppError("Kafka 结果发送通道已结束")
                    if not await asyncio.to_thread(self.store.capacity):
                        if not paused:
                            assignment = await consumer.assignment()
                            if assignment:
                                await consumer.pause(assignment)
                            paused = True
                        if pending is None:
                            buffered = await consumer.poll(timeout=0.2)
                            if buffered is not None and not buffered.error():
                                pending = buffered
                        await asyncio.sleep(0.3)
                        continue
                    if paused:
                        assignment = await consumer.assignment()
                        if assignment:
                            await consumer.resume(assignment)
                        paused = False
                    message = pending or await consumer.poll(timeout=0.2)
                    if message is None:
                        state = self.status().get(connection["id"], {})
                        if state.get("state") == "failed" and time.monotonic() - last_health > 5:
                            await consumer.list_topics(timeout=2)
                            self.set_status(connection, "connected")
                            last_health = time.monotonic()
                        continue
                    if message.error():
                        self.set_status(connection, "failed", str(message.error()))
                        await asyncio.sleep(0.2)
                        continue
                    envelope = await asyncio.to_thread(
                        decode_message, self.apps, connection, message.value()
                    )
                    identity = await asyncio.to_thread(
                        self.store.ingest, connection, message, envelope
                    )
                    if identity is False:
                        pending = message
                        await asyncio.sleep(0.3)
                        continue
                    pending = None
                    # Commit only after the complete message and its source watermark
                    # are durable. Retried/rebalanced records are safe to acknowledge.
                    await consumer.commit(message=message, asynchronous=False)
                    self.dispatch_wake.set()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.set_status(connection, "failed", str(error))
            finally:
                if sender:
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)
                for client in (consumer, producer):
                    if client:
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(client.close(), 8)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10)

    async def deliver(self, connection, producer):
        while True:
            row = await asyncio.to_thread(self.store.next_delivery, connection["id"])
            if not row:
                await asyncio.sleep(0.3)
                continue
            try:
                # First await queues the produce, the returned future resolves only
                # after broker acknowledgement. A queued send is never called delivered.
                delivery = await producer.produce(
                    row["topic"], value=row["payload"].encode(), key=str(row["inbox"]).encode()
                )
                # The producer already bounds delivery time. Cancel the local future
                # on timeout so a late error cannot become an unobserved exception.
                await asyncio.wait_for(delivery, 12)
                await asyncio.to_thread(self.store.delivered, row["id"])
            except asyncio.CancelledError:
                raise
            except Exception as error:
                message = str(error)
                for field in ("sasl_password", "ssl_key_password"):
                    secret = connection.get(field)
                    if secret:
                        message = message.replace(secret, "[已隐藏]")
                await asyncio.to_thread(self.store.delivered, row["id"], message)
                self.set_status(connection, "failed", message)

    async def dispatch(self):
        last_prune = 0
        while True:
            if not self.active:
                self.dispatch_wake.clear()
                await self.dispatch_wake.wait()
                continue
            try:
                running = await asyncio.to_thread(self.store.jobs, ("running",))
                for job in running:
                    await self.monitor(job)
                if await asyncio.to_thread(self.store.dispatch_capacity):
                    queued = await asyncio.to_thread(
                        self.store.jobs, ("queued",), tuple(self.active)
                    )
                    for job in queued:
                        envelope = json.loads(job["payload"])
                        if (envelope.get("data") or {}).get("action") == "invoke" and len(
                            self.apps.execution.tasks
                        ) >= 4:
                            continue
                        await self.admit(job, envelope)
                if time.monotonic() - last_prune > 3600:
                    await asyncio.to_thread(self.store.prune)
                    last_prune = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                with self.guard:
                    self.live["service"] = {
                        "state": "failed",
                        "error": "Kafka 队列处理失败，将自动重试",
                    }
            await asyncio.sleep(0.2)

    async def admit(self, job, envelope):
        if envelope.get("ignore"):
            await asyncio.to_thread(self.store.mark, job["id"], "done")
            return
        if envelope.get("error"):
            await self.finish(job, {"status": "rejected", "error": envelope["error"]})
            return
        app = {"id": job["app"], "public_id": json.loads(job["appid"])}
        data = envelope["data"]
        transport = {"kind": "kafka", "connection": job["connection"]}
        try:
            if data["action"] == "invoke":
                # An interrupted admission is retried with exactly the same internal
                # request key, so an already accepted Agent is recovered, not rerun.
                await asyncio.to_thread(self.store.mark, job["id"], "admitting")
                receipt = await asyncio.to_thread(
                    self.apps.submit,
                    dict(
                        app=app["id"],
                        text=data["input"],
                        session=data.get("session_id"),
                        request=job["request"],
                    ),
                    transport=transport,
                )
                await asyncio.to_thread(self.store.bind, job, receipt)
            else:
                await asyncio.to_thread(self.apps.access.authorize, app, transport)
                with self.apps.store.db() as db:
                    row = db.execute("SELECT type FROM apps WHERE id=?", (app["id"],)).fetchone()
                if not row:
                    raise AppError("应用不存在", 404)
                app["type"] = row["type"]
                view = (
                    await asyncio.to_thread(cancel_run, self.apps, app, data["run_id"])
                    if data["action"] == "cancel"
                    else await asyncio.to_thread(respond_run, self.apps, app, data)
                )
                await self.finish(
                    job,
                    {
                        "status": view["status"],
                        "run_id": view["run_id"],
                        "session_id": view["session_id"],
                    },
                    event="control",
                )
        except IngressDisabled:
            # Enrolment turned off before admission: consume without starting work.
            await asyncio.to_thread(self.store.mark, job["id"], "done")
        except AppError as error:
            if error.status == 409 and "当前会话正在运行" in str(error):
                await asyncio.to_thread(self.store.mark, job["id"], "queued")
                return
            await self.finish(job, {"status": "failed", "error": str(error)})
        except Exception:
            # Reconcile an uncertain receipt before reporting failure or retrying.
            try:
                receipt = await asyncio.to_thread(
                    self.apps.get, dict(action="submission", app=app["id"], request=job["request"])
                )
                await asyncio.to_thread(self.store.bind, job, receipt)
            except AppError as error:
                if error.status == 404:
                    await self.finish(
                        job, {"status": "failed", "error": "应用提交失败，请检查应用配置"}
                    )
                else:
                    await asyncio.to_thread(self.store.mark, job["id"], "queued")
            except Exception:
                await asyncio.to_thread(self.store.mark, job["id"], "queued")

    async def monitor(self, job):
        try:
            row = await asyncio.to_thread(self.apps.store.run, job["run"])
            if row["status"] == "waiting":
                live = self.apps.execution.snapshot(job["run"]) or {}
                interaction = live.get("interaction")
                if interaction:
                    await asyncio.to_thread(
                        self.store.emit,
                        job,
                        "interaction:" + interaction["id"],
                        {
                            "status": "waiting",
                            "run_id": job["run"],
                            "session_id": job["session"],
                            "interaction": interaction,
                        },
                    )
                return
            if row["status"] in ACTIVE:
                # Start once to reconcile stale interrupted runs after a service restart.
                if self.apps.execution.loop is None:
                    await asyncio.to_thread(self.apps.execution.start)
                return
            with self.apps.store.db() as db:
                app = db.execute(
                    "SELECT id,public_id,type FROM apps WHERE id=?", (job["app"],)
                ).fetchone()
            if not app:
                raise AppError("应用已删除", 404)
            view = await asyncio.to_thread(run_view, self.apps, dict(app), job["run"])
            await self.finish(
                job,
                {key: view[key] for key in ("status", "output", "error", "session_id", "run_id")},
            )
        except AppError as error:
            await self.finish(job, {"status": "failed", "error": str(error)})

    async def finish(self, job, body, event="result"):
        await asyncio.to_thread(self.store.emit, job, event, body, finish=True)

    def save(self, owner, data, identity=None, expected=None):
        self.ensure_owner()
        result = self.store.save_connection(owner, data, identity, expected)
        self.wake()
        return result

    def global_enabled(self, enabled, expected):
        self.ensure_owner()
        value = self.store.save_global(enabled, expected)
        self.wake()
        return value

    def delete(self, identity, owner, expected, *, stop=False):
        self.ensure_owner()
        connection = self.store.connection(identity, owner)
        if expected != connection["revision"]:
            raise AppError("Kafka 接入已更新，请刷新后重试", 409)
        with self.apps.submit_lock:
            with self.store.db() as db:
                pending = bool(self.store.pending_in(db, identity))
                keys = [
                    r[0]
                    for r in db.execute(
                        "SELECT request FROM kafka_inbox WHERE connection=? AND state!='done'",
                        (identity,),
                    )
                ]
            if pending and not stop:
                raise AppError("此接入仍有未完成消息，确认后将停止任务并删除待发送结果", 409)
            self.store.save_connection(owner, {"enabled": False}, identity, expected)
            runs = []
            with self.apps.store.db() as db:
                for start in range(0, len(keys), 100):
                    batch = keys[start : start + 100]
                    runs.extend(
                        dict(row)
                        for row in db.execute(
                            "SELECT r.id AS run,r.session,s.app FROM runs r JOIN sessions s ON "
                            "s.id=r.session WHERE r.request_id IN ("
                            + ",".join("?" for _ in batch)
                            + ")",
                            batch,
                        )
                    )
        if self.loop:

            async def detach():
                existing = self.runners.pop(identity, None)
                self.active.pop(identity, None)
                if existing:
                    existing[1].cancel()
                    await asyncio.gather(existing[1], return_exceptions=True)

            asyncio.run_coroutine_threadsafe(detach(), self.loop).result(15)
        for run in runs:
            with contextlib.suppress(AppError):
                self.apps.mutate(
                    dict(action="stop", app=run["app"], session=run["session"], run=run["run"])
                )
        current = self.store.connection(identity, owner)
        self.store.delete_connection(identity, owner, current["revision"])
        self.wake()
        return {"ok": True}

    def close(self):
        with self.guard:
            if self.closed:
                return
            self.closed = True
        if self.loop:

            async def drain():
                # Cancelling a concurrent Future does not wait for its asyncio Task's
                # finally blocks. Await the actual host task before cancelling leftovers.
                task = getattr(self, "run_task", None)
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await self.loop.shutdown_asyncgens()
                await self.loop.shutdown_default_executor(timeout=5)

            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(drain(), self.loop).result(25)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
            self.loop.close()
            self.loop = None
        if self.lock:
            from corki.storage.file_lock import unlock_and_close

            unlock_and_close(self.lock)
            self.lock = None
