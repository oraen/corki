"""A fresh runtime per occurrence, with no interactive history or approval escalation."""

import asyncio
import sys
import time
from contextlib import aclosing, suppress
from dataclasses import replace
from pathlib import Path

from corki.platforms.paths import directory_error_message

from .diagnostics import exception_log
from .locking import FileLock
from .models import validate_model
from .store import CronStore


class JobStopped(Exception):
    pass


class JobTimedOut(Exception):
    pass


class JobFailed(Exception):
    def __init__(self, message, diagnostic=""):
        super().__init__(message)
        self.diagnostic = diagnostic


async def execute(store, identity, *, factory=None, settings_factory=None, idle_timeout=600):
    # Import the Agent only in the worker: the Manager ticker remains light and isolated.
    from corki.config.settings import CorkiSettings
    from corki.core.runtime import LangGraphRuntime
    from corki.protocol.events import (
        TurnCancelled,
        TurnCompleted,
        TurnFailed,
        UserInputRequested,
    )
    from corki.protocol.ids import ThreadId
    from corki.protocol.session_source import SessionSource

    factory = factory or LangGraphRuntime.acreate
    settings_factory = settings_factory or CorkiSettings.for_directory
    row = store.run(identity)
    if row["cancel"]:
        raise JobStopped()
    job = row["definition"]
    runtime = None
    answer = ""
    completed = False
    workdir = Path(job["workdir"])
    if workdir == store.home / "main/default":
        workdir.mkdir(parents=True, exist_ok=True)
    if not workdir.is_dir():
        raise JobFailed(directory_error_message(workdir, "工作目录不存在，请编辑任务"))
    try:
        overrides = {}
        if job.get("provider") or job.get("model"):
            try:
                provider, model = validate_model(store.home, job.get("provider"), job.get("model"))
            except ValueError as error:
                raise JobFailed(str(error)) from error
            overrides = {"provider": provider, "model": model}
        settings = settings_factory(workdir, config_file=store.home / "settings.json", **overrides)
        # Unattended network failures must not cause an infinite retry loop.
        settings = replace(
            settings, model_request_max_retries=3, model_unbounded_connection_retries=False
        )
        runtime = await asyncio.wait_for(
            factory(
                settings=settings,
                database_path=store.home / "sessions/corki.db",
                home_path=store.home,
                thread_id=ThreadId(identity),
                session_source=SessionSource.from_startup_arg("cron"),
            ),
            timeout=idle_timeout,
        )
        prompt = (
            "This is an unattended scheduled task in a new session. Execute the task below "
            "using the configured permissions. Do not wait for user input or request permission "
            "escalation; report any blocker. Do not create additional schedules unless the task "
            "explicitly requests them. Return a self-contained result.\n\n" + job["prompt"]
        )
        async with aclosing(runtime.stream(prompt)) as events:
            while True:
                try:
                    event = await asyncio.wait_for(anext(events), timeout=idle_timeout)
                except StopAsyncIteration:
                    break
                if isinstance(event, UserInputRequested) and event.is_blocking:
                    raise JobFailed("任务需要用户输入，请调整任务内容后重试")
                if isinstance(event, TurnFailed):
                    raise JobFailed(
                        "Agent 执行失败，请检查模型配置或对应会话日志",
                        f"{event.error_kind or 'TurnFailed'}: {event.error}",
                    )
                if isinstance(event, TurnCancelled):
                    raise JobStopped()
                if isinstance(event, TurnCompleted):
                    answer = event.final_answer[:2_000_000]
                    completed = True
        if not completed:
            raise JobFailed("Agent 未返回完成状态")
        return answer
    finally:
        if runtime is not None:
            with suppress(Exception):
                await asyncio.wait_for(runtime.aclose(), timeout=15)


async def supervise(store, identity, **kwargs):
    budget = store.run(identity)["definition"].get("timeout_seconds", 0)
    deadline = time.monotonic() + budget if budget else None
    task = asyncio.create_task(execute(store, identity, **kwargs))
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=2)
            if done:
                break
            store.heartbeat(identity)
            if store.run(identity)["cancel"]:
                task.cancel()
                # Runtime may translate cancellation into TurnCancelled/JobStopped.
                # Preserve the supervisor's original stop/timeout cause after cleanup.
                with suppress(asyncio.CancelledError, Exception):
                    await task
                raise JobStopped()
            if deadline is not None and time.monotonic() >= deadline:
                task.cancel()
                # Runtime may translate cancellation into TurnCancelled/JobStopped.
                # Preserve the supervisor's original stop/timeout cause after cleanup.
                with suppress(asyncio.CancelledError, Exception):
                    await task
                raise JobTimedOut()
        output = await task
        store.finish(identity, "completed", output=output)
    except (JobStopped, asyncio.CancelledError):
        store.finish(identity, "cancelled", error="执行已停止，已完成的操作不会撤销")
    except JobTimedOut:
        store.finish(
            identity,
            "failed",
            error=f"任务超过超时时间（{budget} 秒），已停止",
            error_log=f"任务超过配置的总执行时限：{budget} 秒",
        )
    except TimeoutError as error:
        store.finish(
            identity,
            "failed",
            error="任务连续 10 分钟无响应，已停止",
            error_log=exception_log(store.home, error),
        )
    except JobFailed as error:
        store.finish(
            identity, "failed", error=str(error), error_log=exception_log(store.home, error)
        )
    except Exception as error:
        store.finish(
            identity,
            "failed",
            error="任务执行异常，请检查配置、工作目录及会话日志",
            error_log=exception_log(store.home, error),
        )
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


def main():
    store = CronStore(Path(sys.argv[1]))
    identity = sys.argv[2]
    # Lock paths are derived from existing UUID identities, not arbitrary arguments.
    row = store.run(identity)
    lock = FileLock(store.root / "locks" / (row["id"] + ".lock"))
    if not lock.acquire():
        return
    try:
        if not store.start_run(identity):
            return
        if row["cancel"]:
            store.finish(identity, "cancelled", error="执行已取消")
            return
        asyncio.run(supervise(store, identity))
    finally:
        lock.close()


if __name__ == "__main__":
    main()
