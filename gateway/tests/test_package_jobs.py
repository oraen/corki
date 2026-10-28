import threading
import time
from types import SimpleNamespace

import pytest
from corki_gateway import package_jobs, plugins
from corki_gateway.control import control
from corki_gateway.store import Store

from corki.cron.locking import FileLock


def test_background_download_does_not_lock_other_channels(tmp_path, monkeypatch):
    spawned = []

    def spawn(*args, **kwargs):
        spawned.append(args)
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(package_jobs.subprocess, "Popen", spawn)
    started, release = threading.Event(), threading.Event()

    def install(store, identity, progress):
        progress("正在下载并安装组件…")
        started.set()
        assert release.wait(10)

    monkeypatch.setattr(plugins, "install_dependencies", install)
    result = control(tmp_path, {"action": "dependencies", "id": "wechat"})
    job = result["package_jobs"]["wechat"]
    assert job["status"] == "queued"
    worker = threading.Thread(target=package_jobs.run, args=(tmp_path, "wechat", job["token"]))
    worker.start()
    try:
        assert started.wait(5)
        state = control(tmp_path, {"action": "status"})
        assert state["package_jobs"]["wechat"]["stage"] == "正在下载并安装组件…"
        control(tmp_path, {"action": "configure", "id": "telegram", "values": {"token": "fixture"}})
        with pytest.raises(ValueError, match="此渠道正在安装或卸载"):
            control(tmp_path, {"action": "configure", "id": "wechat", "values": {}})
        control(tmp_path, {"action": "dependencies", "id": "wechat"})
        assert len(spawned) == 1
    finally:
        release.set()
        worker.join(5)
    assert (
        control(tmp_path, {"action": "status"})["package_jobs"]["wechat"]["status"] == "completed"
    )


def test_abandoned_jobs_recover_but_live_worker_is_not_failed(tmp_path):
    store = Store(tmp_path)
    with store.db() as db:
        db.execute(
            "INSERT INTO package_jobs VALUES(?,?,?,?,?,?)",
            ("telegram", "fixture", "dependencies", "running", "working", time.time() - 60),
        )
    lock = FileLock(store.root / "package-telegram.lock")
    assert lock.acquire()
    try:
        assert package_jobs.snapshot(store)["telegram"]["status"] == "running"
    finally:
        lock.close()
    assert package_jobs.snapshot(store)["telegram"]["status"] == "failed"


def test_real_detached_worker_installs_and_uninstalls_without_gateway(tmp_path):
    if not plugins.find("node"):
        pytest.skip("Node runtime required")

    def finish(action):
        value = control(tmp_path, {"action": action, "id": "telegram"})
        token = value["package_jobs"]["telegram"]["token"]
        for _ in range(150):
            value = control(tmp_path, {"action": "status"})
            job = value["package_jobs"]["telegram"]
            if job["token"] == token and job["status"] not in package_jobs.ACTIVE:
                assert job["status"] == "completed", job
                assert value["running"] is False
                return value
            time.sleep(0.05)
        pytest.fail("Package worker did not complete")

    assert finish("dependencies")["installed"]["telegram"] is True
    assert finish("uninstall-dependencies")["installed"]["telegram"] is False
    assert not (tmp_path / "gateway/node/telegram").exists()
