import asyncio
import base64
import io
import json
import zipfile

import pytest
from corki_gateway.plugins import install, validate_manifest
from corki_gateway.service import Host
from corki_gateway.store import Store


def message(identity="one", sender="person", text="hello"):
    return {
        "id": identity,
        "sender": sender,
        "conversation": sender,
        "kind": "direct",
        "text": text,
    }


def enabled(store):
    store.configure("telegram", {"token": "fixture-not-a-credential"})
    store.enable("telegram", True)


def test_secrets_blank_preserve_and_account_isolation(tmp_path):
    store = Store(tmp_path)
    enabled(store)
    assert "fixture-not-a-credential" not in json.dumps(store.snapshot())
    store.configure("telegram", {"token": ""})
    assert store.channel("telegram")["config"]["token"] == "fixture-not-a-credential"
    store.account("telegram", "bot1")
    thread = store.admit("telegram", message(), str(tmp_path / "main/default"))
    assert thread and store.admit("telegram", message(), str(tmp_path / "main/default")) is None
    assert store.admit("telegram", message("two"), str(tmp_path / "main/default")) == thread
    assert (
        store.admit("telegram", message("three", "different"), str(tmp_path / "main/default"))
        != thread
    )
    store.account("telegram", "bot2")
    assert store.admit("telegram", message(), str(tmp_path / "main/default")) != thread


def test_recovery_does_not_reexecute_admitted_messages(tmp_path):
    store = Store(tmp_path)
    store.admit("telegram", message(), str(tmp_path / "main/default"))
    Store(tmp_path).recover()
    assert store.snapshot()["receipts"][0]["status"] == "interrupted"
    assert store.admit("telegram", message(), str(tmp_path / "main/default")) is None


def package(entry="main.py", extra=None):
    buffer = io.BytesIO()
    manifest = {
        "protocol": 1,
        "id": "fixture",
        "name": "Fixture",
        "runtime": "python",
        "entry": entry,
    }
    with zipfile.ZipFile(buffer, "w") as zip:
        zip.writestr("connector.json", json.dumps(manifest))
        zip.writestr("main.py", "print('fixture')")
        if extra:
            zip.writestr(extra, "unsafe")
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.mark.parametrize("entry", ["../main.py", "/main.py", "C:\\main.py"])
def test_manifest_rejects_outside_entries(entry):
    with pytest.raises(ValueError):
        validate_manifest(
            {"protocol": 1, "id": "fixture", "name": "Fixture", "runtime": "python", "entry": entry}
        )


@pytest.mark.parametrize(
    "extra", ["../outside", "C:/outside", "folder\\outside", "CON.txt", "bad."]
)
def test_archive_rejects_unsafe_paths(tmp_path, extra):
    with pytest.raises(ValueError):
        install(Store(tmp_path), package(extra=extra))
    assert not (tmp_path / "gateway/plugins/fixture").exists()


def test_install_is_disabled_and_no_duplicate(tmp_path):
    store = Store(tmp_path)
    assert install(store, package()) == "fixture"
    assert not store.channel("fixture")["enabled"]
    with pytest.raises(ValueError, match="同名"):
        install(store, package())


def test_dispatch_permission_dedup_serialization_and_cancel(tmp_path):
    async def scenario():
        seen, sent = [], []
        gate = asyncio.Event()

        async def runner(thread, text, workdir, channel):
            seen.append((thread, text))
            if text == "wait":
                await gate.wait()
            return "reply " + text

        class Transport:
            async def call(self, method, params, **kwargs):
                sent.append(params["text"])
                return {"sent": True}

        host = Host(tmp_path, runner=runner)
        enabled(host.store)
        host.connectors["telegram"] = Transport()
        first = await host.inbound("telegram", message(text="wait"))
        assert (await host.inbound("telegram", message(text="wait")))["duplicate"]
        second = await host.inbound("telegram", message("two"))
        assert first["thread"] == second["thread"]
        await asyncio.sleep(0.01)
        assert [text for _, text in seen] == ["wait"]
        gate.set()
        await asyncio.gather(*list(host.tasks.values()))
        assert sent == ["reply wait", "reply hello"]
        assert [r["status"] for r in host.snapshot()["receipts"]] == ["completed", "completed"]
        gate.clear()
        await host.inbound("telegram", message("three", text="wait"))
        await asyncio.sleep(0.01)
        await host.change({"action": "channel", "id": "telegram", "enabled": False})
        assert host.snapshot()["receipts"][0]["status"] == "cancelled"
        await host.close()

    asyncio.run(scenario())


def test_delivery_failure_is_not_auto_retried(tmp_path):
    async def scenario():
        calls = 0

        async def runner(*_):
            return "result"

        class Transport:
            async def call(self, *_args, **_kwargs):
                nonlocal calls
                calls += 1
                raise TimeoutError()

        host = Host(tmp_path, runner=runner)
        enabled(host.store)
        host.connectors["telegram"] = Transport()
        await host.inbound("telegram", message())
        await asyncio.gather(*list(host.tasks.values()))
        await host.inbound("telegram", message())
        assert calls == 1
        assert host.snapshot()["receipts"][0]["status"] == "failed"
        await host.close()

    asyncio.run(scenario())


def test_identity_reset_only_affects_its_own_channel(tmp_path):
    async def scenario():
        host = Host(tmp_path, runner=lambda *_: None)
        host.store.admit("wechat", message(), str(tmp_path / "main/default"))
        other = host.store.admit("telegram", message(), str(tmp_path / "main/default"))
        await host.change({"action": "reset", "id": "wechat"})
        assert host.store.admit("telegram", message("two"), str(tmp_path / "main/default")) == other

    asyncio.run(scenario())


def test_offline_edits_never_launch_connectors(tmp_path):
    async def scenario():
        host = Host(tmp_path, runner=lambda *_: None, online=False)
        enabled(host.store)
        await host.change({"action": "configure", "id": "telegram", "values": {"token": ""}})
        assert not host.monitors and not host.snapshot()["running"]

    asyncio.run(scenario())


def test_approval_routes_to_owned_runtime_and_rejects_stale(tmp_path):
    calls = []

    class Runtime:
        def respond_execution_approval(self, *args):
            calls.append(("execution", args))

        def respond_mcp_elicitation(self, *args, **kwargs):
            calls.append(("mcp", args))

        def respond_user_input(self, *args):
            calls.append(("input", args))
            return True

    host = Host(tmp_path, runner=lambda *_: None)
    host.runtimes["thread"] = Runtime()
    host.interactions["one"] = {"kind": "shell_approval", "thread": "thread"}
    host.respond({"request_id": "one", "decision": "decline"})
    assert calls == [("execution", ("one", "decline"))]
    with pytest.raises(ValueError, match="已结束"):
        host.respond({"request_id": "one", "decision": "accept"})
    host.interactions["two"] = {
        "kind": "question",
        "thread": "thread",
        "turn_id": "turn",
        "call_id": "call",
    }
    host.respond({"request_id": "two", "response": {"answers": {}}})
    assert calls[-1][0] == "input"


def test_reveal_only_requested_secret_and_keep_it_out_of_snapshot(tmp_path):
    from corki_gateway.control import control
    from corki_gateway.store import Store

    store = Store(tmp_path)
    identity = store.save_connection(
        {
            "channel": "dingtalk",
            "name": "Reveal fixture",
            "values": {"client_id": "fixture-client", "client_secret": "fixture-private-value"},
        }
    )
    assert "fixture-private-value" not in str(store.snapshot())
    result = control(
        tmp_path, {"action": "reveal-secret", "id": identity, "field": "client_secret"}
    )
    assert result == {"value": "fixture-private-value"}
    import pytest

    with pytest.raises(ValueError, match="密钥字段不存在"):
        control(tmp_path, {"action": "reveal-secret", "id": identity, "field": "client_id"})
