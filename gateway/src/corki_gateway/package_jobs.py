"""Durable package operations, independent of browser and gateway lifetime."""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from corki.cron.locking import FileLock

from .store import BUILTINS, Store

ACTIVE = ("queued", "running")


def snapshot(store):
    with store.db() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM package_jobs")]
    for row in rows:
        if row["status"] not in ACTIVE or time.time() - row["updated"] < 20:
            continue
        lock = FileLock(store.root / ("package-" + row["channel"] + ".lock"))
        if lock.acquire():
            try:
                update(store, row["channel"], row["token"], "failed", "操作已中断，请重试")
                row.update(status="failed", stage="操作已中断，请重试")
            finally:
                lock.close()
    return {r["channel"]: {k: r[k] for k in ("token", "action", "status", "stage")} for r in rows}


def update(store, identity, token, status, stage):
    with store.db() as db:
        db.execute(
            "UPDATE package_jobs SET status=?,stage=?,updated=? WHERE channel=? AND token=?",
            (status, stage, time.time(), identity, token),
        )


def start(store, identity, action):
    if identity not in BUILTINS or action not in ("dependencies", "uninstall-dependencies"):
        raise ValueError("请选择有效渠道操作")
    snapshot(store)  # Recover abandoned work before accepting a retry.
    token = uuid.uuid4().hex
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT status FROM package_jobs WHERE channel=?", (identity,)).fetchone()
        if old and old["status"] in ACTIVE:
            return
        db.execute(
            "INSERT OR REPLACE INTO package_jobs VALUES(?,?,?,?,?,?)",
            (identity, token, action, "queued", "准备中…", time.time()),
        )
    env = {
        **os.environ,
        "CORKI_HOME": str(store.home),
        "PYTHONUTF8": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1])
        + os.pathsep
        + os.environ.get("PYTHONPATH", ""),
    }
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "corki_gateway.control",
                "--home",
                str(store.home),
                "--package-job",
                identity,
                token,
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=(0x00000200 | 0x00000008) if os.name == "nt" else 0,
        )
        # No browser/manager request waits for npm. The worker owns its lifecycle.
        process.poll()
    except OSError as error:
        update(store, identity, token, "failed", "无法启动安装服务，请检查 Python 环境")
        raise ValueError("无法启动渠道安装服务") from error


def run(home, identity, token):
    from .control import control
    from .plugins import install_dependencies, uninstall_dependencies

    store = Store(home)
    if identity not in BUILTINS:
        return
    lock = FileLock(store.root / ("package-" + identity + ".lock"))
    deadline = time.monotonic() + 5
    while not lock.acquire():
        if time.monotonic() > deadline:
            return
        time.sleep(0.05)
    try:
        with store.db() as db:
            row = db.execute(
                "SELECT * FROM package_jobs WHERE channel=? AND token=?", (identity, token)
            ).fetchone()
        if not row or row["status"] not in ACTIVE:
            return

        def progress(stage):
            update(store, identity, token, "running", stage)

        progress("准备中…")
        if row["action"] == "uninstall-dependencies":
            progress("正在停止渠道…")
            # Brief controlled mutation, then release the global control lock before I/O.
            for attempt in range(100):
                try:
                    control(
                        home,
                        {"action": "prepare-uninstall", "id": identity, "package_token": token},
                    )
                    break
                except ValueError as error:
                    if "正在处理其他操作" not in str(error) or attempt == 99:
                        raise
                    time.sleep(0.1)
            progress("正在移除渠道组件…")
            uninstall_dependencies(store, identity)
        else:
            install_dependencies(store, identity, progress=progress)
        update(
            store,
            identity,
            token,
            "completed",
            "安装完成" if row["action"] == "dependencies" else "卸载完成",
        )
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else "操作失败，请检查目录权限后重试"
        update(store, identity, token, "failed", message)
    finally:
        lock.close()
