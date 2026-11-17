"""Owned event-loop worker; browser connections never own execution tasks."""

import asyncio
import concurrent.futures
import json
import sqlite3
import threading
from contextlib import closing

from .models import AppError, now


class ExecutionHost:
    def __init__(self, store, model_factory=None, runtime_factory=None):
        self.store = store
        self.model_factory = model_factory
        self.runtime_factory = runtime_factory
        self.loop = None
        self.thread = None
        self.guard = threading.RLock()
        self.initialization = threading.Lock()
        self.live = {}
        self.runtimes = {}
        self.tasks = {}
        self.repositories = None
        self.lock = None
        self.closed = False
        self.database = store.root / "runtime/sessions/corki.db"
        self.ledger = store.home / "sessions/corki.db"

    def start(self):
        with self.guard:
            if self.closed:
                raise AppError("应用运行服务正在关闭", 503)
            if self.loop is not None:
                return
            from corki.storage.file_lock import open_lock, try_lock

            self.lock = open_lock(self.store.root / "worker.lock")
            if not try_lock(self.lock):
                self.lock.close()
                self.lock = None
                raise AppError("应用由另一个管理服务运行，请使用原管理页面", 409)
            # Only the kernel-lock owner reconciles interrupted tasks.
            with self.store.db() as db:
                db.execute(
                    (
                        "UPDATE runs SET status='failed',error="
                        "'运行服务已断开；未自动重发任务',updated=?"
                        " WHERE status IN ('submitted','running','waiting','stopping')"
                    ),
                    (now(),),
                )
            self.loop = asyncio.new_event_loop()
            self.thread = threading.Thread(
                target=self.loop.run_forever, name="corki-applications", daemon=True
            )
            self.thread.start()

            # Initialize once for the execution host, including usage recovery.
            async def ready():
                await asyncio.to_thread(self.repositories_ready)

            self.call(ready())

    def call(self, coro, timeout=30):
        if self.loop is None:
            coro.close()
            raise AppError("应用运行服务尚未启动", 503)
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)
        except concurrent.futures.TimeoutError as error:
            raise AppError("操作仍在处理中，请刷新查看状态；不要重复提交", 504) from error

    def snapshot(self, run):
        with self.guard:
            view = self.live.get(run)
            return json.loads(json.dumps(view)) if view else None

    def publish(self, run, **values):
        with self.guard:
            if run in self.live:
                self.live[run].update(values)
                self.live[run]["sequence"] += 1

    async def schedule(self, run, app, session, text, settings, mentions):
        if len(self.tasks) >= 4:
            self.store.update_run(
                run["id"], status="failed", error="同时运行的应用较多，请稍后重试"
            )
            return
        with self.guard:
            self.live[run["id"]] = dict(
                id=run["id"],
                session=session["id"],
                input=text,
                answer="",
                messages=[],
                tools={},
                interaction=None,
                sequence=0,
                status="running",
            )
        self.tasks[run["id"]] = asyncio.create_task(
            self.execute(run, app, session, text, settings, mentions)
        )

    def repositories_ready(self):
        with self.initialization:
            if self.repositories is None:
                from corki.storage import SQLiteSessionRepository

                self.repositories = (
                    SQLiteSessionRepository(self.database),
                    SQLiteSessionRepository(self.ledger),
                )
                from .agent_runner import sync_usage

                with self.store.db() as db:
                    owners = db.execute(
                        "SELECT s.id,s.app FROM sessions s JOIN apps a ON a.id=s.app "
                        "WHERE a.type='agent'"
                    ).fetchall()
                for owner in owners:
                    sync_usage(self, {"id": owner["app"]}, {"id": owner["id"]})
        return self.repositories[0]

    def error_text(self, error, settings):
        text = str(error).strip() or type(error).__name__
        for secret in (settings.api_key,):
            if secret:
                text = text.replace(secret, "[已隐藏]")
        return text[:1000]

    async def execute(self, run, app, session, text, settings, mentions):
        identity = run["id"]
        self.store.update_run(
            identity, status="running", provider=settings.provider_id, model=settings.model
        )
        try:
            await asyncio.to_thread(self.repositories_ready)
            if app["type"] == "llm":
                from .llm_runner import run_llm

                await run_llm(self, run, app, session, text, settings)
            else:
                from .agent_runner import run_agent

                await run_agent(self, run, app, session, text, settings, mentions)
            status, error = "completed", None
        except asyncio.CancelledError:
            status, error = "cancelled", None
        except Exception as exc:
            status, error = "failed", self.error_text(exc, settings)
        finally:
            view = self.snapshot(identity) or {}
            if app["type"] == "agent":
                # Keep an accepted input until the canonical transcript owns it.
                # Initialization failures must not make the user's question disappear.
                current = self.store.run(identity)
                if current["core_turn"] and self.database.exists():
                    with closing(sqlite3.connect(self.database)) as db:
                        present = db.execute(
                            "SELECT 1 FROM chat_history WHERE thread_id=? AND turn_id=? "
                            "AND kind='user_message' LIMIT 1",
                            (session["id"], current["core_turn"]),
                        ).fetchone()
                    if present:
                        self.store.update_run(identity, input="")
            self.store.update_run(
                identity,
                status=locals().get("status", "failed"),
                error=locals().get("error"),
                answer=(
                    view.get("answer", "")
                    if app["type"] == "llm"
                    else view["messages"][-1]["content"]
                    if view.get("message_open") and view.get("messages")
                    else ""
                ),
            )
            with self.guard:
                self.live.pop(identity, None)
            self.tasks.pop(identity, None)

    async def stop(self, identity):
        task = self.tasks.get(identity)
        if task is None:
            return
        self.store.update_run(identity, status="stopping")
        self.publish(identity, status="stopping")
        runtime = self.runtimes.get(identity)
        if runtime:
            await runtime.cancel_active()
        else:
            task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), 25)
        except TimeoutError:
            task.cancel()
            raise AppError("任务正在停止，请稍后刷新", 409) from None

    async def respond(self, run, data):
        pending = self.live.get(run, {}).get("interaction")
        if pending is None or data.get("request") != pending["id"]:
            raise AppError("该交互已经结束，请刷新", 409)
        runtime = self.runtimes[run]
        if pending["kind"] == "question":
            if not runtime.respond_user_input(pending["turn"], pending["id"], data.get("response")):
                raise AppError("问题已失效", 409)
        elif pending["server"] == "local-shell":
            runtime.respond_execution_approval(pending["id"], data.get("decision"))
        else:
            runtime.respond_mcp_elicitation(
                pending["server"], pending["id"], data.get("decision"), content=data.get("response")
            )
        self.publish(run, interaction=None, status="running")
        self.store.update_run(run, status="running")

    def close(self):
        with self.guard:
            self.closed = True
        if self.loop:

            async def shutdown():
                for task in list(self.tasks.values()):
                    task.cancel()
                await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
                for repository in self.repositories or ():
                    await repository.close()

            self.call(shutdown(), 40)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
            self.loop.close()
            self.loop = None
        if self.lock:
            from corki.storage.file_lock import unlock_and_close

            unlock_and_close(self.lock)
            self.lock = None
