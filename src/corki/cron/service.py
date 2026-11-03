"""Small supervised ticker; durable detached workers outlive a Manager restart."""

import logging
import os
import subprocess
import sys
import threading
import time

from corki.platforms.processes import background_process_options

from .locking import FileLock
from .store import CronStore

log = logging.getLogger(__name__)


class CronService:
    def __init__(self, home, *, launch=None):
        self.store = CronStore(home)
        self.lock = FileLock(self.store.root / ".scheduler.lock")
        self.stop_event = threading.Event()
        self.thread = None
        self.launch = launch or self._launch
        self.children = []

    def _launch(self, identity):
        env = os.environ.copy()
        env["CORKI_HOME"] = str(self.store.home)
        # Preserve the exact checkout/interpreter even when manager.py was launched from elsewhere.
        env["PYTHONPATH"] = os.pathsep.join(str(p) for p in sys.path if p)
        command = [sys.executable, "-m", "corki.cron.worker", str(self.store.home), identity]
        kwargs = (
            {"start_new_session": True}
            if os.name != "nt"
            # A console-free worker owns its lifetime without a visible window.
            else background_process_options()
        )
        self.children.append(
            subprocess.Popen(
                command,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                **kwargs,
            )
        )

    def start(self):
        if self.thread:
            return
        self.thread = threading.Thread(target=self._loop, name="corki-cron", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
        # Workers own their execution; do not terminate on page/server exit.

    def _loop(self):
        try:
            while not self.stop_event.is_set():
                try:
                    if self.lock.file is not None or self.lock.acquire():
                        self.tick()
                except Exception:
                    log.exception("Scheduled task tick failed; retrying next tick")
                self.stop_event.wait(2)
        finally:
            self.lock.close()

    def tick(self):
        self.store.heartbeat()
        self.children = [p for p in self.children if p.poll() is None]
        self.recover()
        while not self.stop_event.is_set():
            identity = self.store.claim()
            if identity is None:
                break
            self.dispatch(identity)

    def dispatch(self, identity):
        if self.store.run(identity)["status"] != "claimed":
            return  # A replacement is promoted by the ticker after old cleanup.
        try:
            self.launch(identity)
        except Exception:
            self.store.finish(identity, "failed", error="无法启动任务进程，请检查运行环境")
            log.exception("Unable to launch scheduled worker")

    def run_now(self, identity):
        run_id = self.store.claim(identity)
        self.dispatch(run_id)
        return run_id

    def recover(self, *, now=None):
        now = time.time() if now is None else now
        for run in self.store.active():
            if now - run["heartbeat"] < 30:
                continue
            lock = FileLock(self.store.root / "locks" / (run["id"] + ".lock"))
            if not lock.acquire():
                continue  # A live worker, even with delayed heartbeat, must never be replayed.
            try:
                self.store.finish(
                    run["id"], "unknown", error="执行进程已退出，结果未确认；任务已暂停"
                )
            finally:
                lock.close()
