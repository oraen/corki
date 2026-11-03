"""Live account switches queue safely without the lifecycle file lock."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from corki_gateway import control as boundary
from corki_gateway.service import Host
from corki_gateway.store import Store

from corki.cron.locking import FileLock


def account(store, name):
    return store.save_connection({"channel": "wechat", "name": name, "values": {}})


@pytest.mark.parametrize("action", ["channel", "connection-reconnect"])
def test_live_operations_reach_host_while_another_account_is_waiting(tmp_path, monkeypatch, action):
    store = Store(tmp_path)
    one, two = account(store, "one"), account(store, "two")
    started, release = threading.Event(), threading.Event()

    def request(home, data, timeout=5):
        if data["action"] == "status":
            return {"running": True}
        if data["id"] == one:
            started.set()
            assert release.wait(5)
        return {"id": data["id"], "running": True}

    monkeypatch.setattr(boundary, "request", request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(boundary.control, tmp_path, {
            "action": action, "id": one, "enabled": False,
        })
        try:
            assert started.wait(5)
            second = pool.submit(boundary.control, tmp_path, {
                "action": action, "id": two, "enabled": False,
            })
            assert second.result(timeout=3)["id"] == two
            assert boundary.control(tmp_path, {"action": "status"})["running"]
        finally:
            release.set()
        assert first.result(timeout=3)["id"] == one


def test_offline_switches_still_respect_lifecycle_lock(tmp_path):
    store = Store(tmp_path)
    identity = account(store, "one")
    guard = FileLock(store.root / "control.lock")
    assert guard.acquire()
    try:
        with pytest.raises(ValueError, match="正在处理其他操作"):
            boundary.control(tmp_path, {"action": "channel", "id": identity, "enabled": False})
    finally:
        guard.close()


def test_host_serializes_switches_while_history_remains_readable(tmp_path):
    async def scenario():
        host = Host(tmp_path)
        one, two = account(host.store, "one"), account(host.store, "two")
        for identity in (one, two):
            host.store.enable(identity, True)
        started, release = asyncio.Event(), asyncio.Event()
        stopped = []

        async def stop(identity):
            stopped.append(identity)
            if identity == one:
                started.set()
                await release.wait()

        host.stop_channel = stop
        first = asyncio.create_task(host.change({
            "action": "channel", "id": one, "enabled": False,
        }))
        await asyncio.wait_for(started.wait(), 2)
        second = asyncio.create_task(host.change({
            "action": "channel", "id": two, "enabled": False,
        }))
        try:
            history = await asyncio.wait_for(host.change({
                "action": "conversation", "id": two,
            }), 2)
            assert history["messages"] == []
            assert not second.done()
            assert stopped == [one]
            assert host.snapshot()["running"]
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert stopped == [one, two]
        assert all(not host.store.connection(key)["enabled"] for key in (one, two))
        host.stopping = True
        with pytest.raises(ValueError, match="网关正在关闭"):
            await host.change({"action": "channel", "id": one, "enabled": True})
        assert not host.store.connection(one)["enabled"]

    asyncio.run(scenario())
