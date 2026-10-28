"""Per-channel ownership, legacy migration and failed installation recovery."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from corki_gateway import plugins
from corki_gateway.service import Host
from corki_gateway.store import BUILTINS, Store


@pytest.fixture
def packages(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    for identity in BUILTINS:
        folder = root / "packages" / identity
        folder.mkdir(parents=True)
        dependencies = {} if identity == "telegram" else {f"{identity}-sdk": "1.0.0"}
        (folder / "package.json").write_text(json.dumps({"dependencies": dependencies}))
        (folder / "package-lock.json").write_text(
            json.dumps(
                {
                    "packages": {
                        "": {},
                        **{f"node_modules/{name}": {"version": "1.0.0"} for name in dependencies},
                    }
                }
            )
        )
    monkeypatch.setattr(plugins, "assets", lambda: root)
    monkeypatch.setattr(plugins, "find", lambda name: "/fake/" + name)
    return root


def legacy(store):
    root = store.root / "node"
    root.mkdir()
    (root / ".installed").write_text("1")
    (root / "package.json").write_text("{}")
    for identity in BUILTINS:
        path = root / "node_modules" / (identity + "-sdk")
        path.mkdir(parents=True)
        (path / "index.js").write_text(identity)
    return root


def test_legacy_split_and_uninstall_do_not_remove_other_channel_or_login(tmp_path, packages):
    store = Store(tmp_path / "home")
    root = legacy(store)
    lockfile = packages / "packages/whatsapp/package-lock.json"
    lock = json.loads(lockfile.read_text())
    lock["packages"]["node_modules/other-platform-native"] = {"optional": True}
    lockfile.write_text(json.dumps(lock))
    login = store.root / "accounts/whatsapp/login.json"
    login.parent.mkdir(parents=True)
    login.write_text("fixture")
    assert all(plugins.installation_status(store).values())
    plugins.migrate_dependencies(store)
    assert plugins.dependencies_ready(store, "whatsapp")
    plugins.uninstall_dependencies(store, "whatsapp")
    assert not plugins.dependencies_ready(store, "whatsapp")
    assert plugins.dependencies_ready(store, "feishu")
    assert not (root / "feishu/node_modules/whatsapp-sdk").exists()
    assert (root / "node_modules").exists()  # Old live processes may still import here.
    plugins.cleanup_legacy_dependencies(store)
    assert not (root / "node_modules").exists()
    assert (root / "feishu/node_modules/feishu-sdk/index.js").read_text() == "feishu"
    assert login.read_text() == "fixture"
    plugins.migrate_dependencies(store)  # Cannot resurrect an uninstalled channel.
    assert not plugins.dependencies_ready(store, "whatsapp")


def test_telegram_install_is_offline_and_failed_sdk_install_not_marked_ready(
    tmp_path, packages, monkeypatch
):
    store = Store(tmp_path / "home")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0 if "--version" in command else 1, stdout="v24.0.0")

    monkeypatch.setattr(plugins.subprocess, "run", run)
    plugins.install_dependencies(store, "telegram")
    assert plugins.dependencies_ready(store, "telegram")
    assert len(calls) == 1 and calls[0][-1] == "--version"
    with pytest.raises(ValueError, match="安装失败"):
        plugins.install_dependencies(store, "feishu")
    assert not plugins.dependencies_ready(store, "feishu")
    assert plugins.dependencies_ready(store, "telegram")
    with pytest.raises(ValueError):
        plugins.uninstall_dependencies(store, "../accounts")


def test_uninstall_stops_only_target_and_preserves_configuration(tmp_path, packages):
    async def check():
        host = Host(tmp_path / "home", online=False)
        legacy(host.store)
        host.store.configure("telegram", {"token": "fixture"})
        host.store.enable("telegram", True)
        stopped = []

        async def stop(identity):
            stopped.append(identity)

        host.stop_channel = stop
        await host.change({"action": "prepare-uninstall", "id": "telegram"})
        plugins.uninstall_dependencies(host.store, "telegram")
        result = host.snapshot()
        assert stopped == ["telegram"]
        assert not host.store.channel("telegram")["enabled"]
        assert host.store.channel("telegram")["config"]["token"] == "fixture"
        assert result["installed"]["telegram"] is False
        assert result["installed"]["feishu"] is True

    asyncio.run(check())
