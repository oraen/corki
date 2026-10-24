"""Independent native Python processes share a disposable Corki session database."""

import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing

_WORKER = r"""
import asyncio, sys, time
from pathlib import Path
from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import UserMessageItem
from corki.storage.sqlite import SQLiteSessionRepository
root, worker = Path(sys.argv[1]), sys.argv[2]
(root / (worker + '.ready')).touch()
deadline = time.monotonic() + 120
while not (root / 'start').exists():
    if time.monotonic() > deadline:
        raise TimeoutError('parent did not release start barrier')
    time.sleep(.02)
async def run():
    repository = SQLiteSessionRepository(root / 'sessions.db')
    thread = ThreadId('worker-' + worker)
    await repository.create_thread(thread, root)
    for number in range(12):
        await repository.append_items(thread, (
            UserMessageItem(content='fixture-' + str(number), turn_id=TurnId('turn-' + worker)),
        ))
    await repository.close()
asyncio.run(run())
"""


def test_independent_processes_initialize_append_and_release_database(tmp_path):
    root = tmp_path / "中文 shared database"
    root.mkdir()
    environment = dict(os.environ, CORKI_HOME=str(root / "home"), PYTHONUTF8="1")
    processes = []
    try:
        for index in range(3):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-X", "utf8", "-c", _WORKER, str(root), str(index)],
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            )
        deadline = time.monotonic() + 120
        while not all((root / f"{index}.ready").exists() for index in range(3)):
            assert all(p.poll() is None for p in processes), "worker exited before start barrier"
            assert time.monotonic() < deadline, "workers did not reach start barrier"
            time.sleep(0.02)
        (root / "start").touch()
        for process in processes:
            _, error = process.communicate(timeout=120)
            assert process.returncode == 0, error.decode("utf-8", errors="replace")
        database = root / "sessions.db"
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert db.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 3
            for index in range(3):
                rows = db.execute(
                    "SELECT sequence FROM conversation_items WHERE thread_id=? ORDER BY sequence",
                    (f"worker-{index}",),
                ).fetchall()
                assert rows == [(n,) for n in range(12)]
        moved = root / "closed.db"
        database.rename(moved)
        moved.unlink()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=30)
