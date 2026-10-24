"""A real worker process owns the occurrence lock, including after stale heartbeat."""

import subprocess
import sys
import time

from corki.cron.service import CronService


def test_live_worker_is_not_replayed_and_crashed_worker_is_paused(tmp_path):
    home = tmp_path / "任务 数据"
    service = CronService(home, launch=lambda _: None)
    job = service.store.save({
        "name": "isolated recovery fixture",
        "prompt": "fixture only",
        "schedule": "every 2h",
        "timezone": "UTC",
    })
    run = service.store.claim(job)
    lock = service.store.root / "locks" / (run + ".lock")
    ready = tmp_path / "ready"
    child = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", """
import sys
from pathlib import Path
from corki.cron.locking import FileLock
lock = FileLock(Path(sys.argv[1]))
assert lock.acquire()
Path(sys.argv[2]).write_text('ready', encoding='utf-8')
try:
    sys.stdin.buffer.read(1)
finally:
    lock.close()
""", str(lock), str(ready)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 120
        while not ready.exists():
            assert child.poll() is None, child.stderr.read().decode("utf-8", errors="replace")
            assert time.monotonic() < deadline, "isolated lock owner did not become ready"
            time.sleep(0.05)
        stale = time.time() + 100
        service.recover(now=stale)
        assert service.store.run(run)["status"] == "claimed"
        assert service.store.list()["jobs"][0]["enabled"]

        # Hard exit intentionally bypasses FileLock.close: the OS must release it.
        child.kill()
        child.wait(timeout=30)
        service.recover(now=stale)
        assert service.store.run(run)["status"] == "unknown"
        assert not service.store.list()["jobs"][0]["enabled"]
        assert service.store.claim(now=stale + 10000) is None
        assert not service.store.start_run(run)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=30)
        child.stdin.close()
        child.stderr.close()
