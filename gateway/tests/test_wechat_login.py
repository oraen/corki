"""Explicit QR requests are limited to enabled WeChat accounts."""

import asyncio

import pytest
from corki_gateway.service import Host


def test_wechat_login_routes_only_to_enabled_wechat_connector(tmp_path):
    async def scenario():
        host = Host(tmp_path)
        wechat = host.store.save_connection({"channel": "wechat", "name": "fixture", "values": {}})
        dingtalk = host.store.save_connection({
            "channel": "dingtalk", "name": "dingtalk", "values": {
                "client_id": "fixture-id", "client_secret": "fixture-secret",
            },
        })
        calls = []

        class Connector:
            async def call(self, method, params=None, timeout=45):
                calls.append(method)
                return {"requested": True}

        host.connectors[wechat] = Connector()
        host.connectors[dingtalk] = Connector()
        with pytest.raises(ValueError, match="启用此微信接入"):
            await host.change({"action": "wechat-login", "id": wechat})
        host.store.enable(wechat, True)
        await host.change({"action": "wechat-login", "id": wechat})
        assert calls == ["login"]
        with pytest.raises(ValueError, match="仅支持微信"):
            await host.change({"action": "wechat-login", "id": dingtalk})
        assert calls == ["login"]
        host.statuses[wechat] = {"state": "connecting"}
        with pytest.raises(ValueError, match="正在启动"):
            await host.change({"action": "wechat-login", "id": wechat})

    asyncio.run(scenario())


def test_wechat_expired_qr_is_login_required_not_connection_error(tmp_path):
    host = Host(tmp_path)
    wechat = host.store.save_connection({"channel": "wechat", "name": "fixture", "values": {}})
    host.connector_status(wechat, {"state": "qr", "qr": "data:image/png;base64,fixture"})
    host.connector_status(wechat, {"state": "login", "code": "expired"})
    assert host.statuses[wechat] == {"state": "login", "message": "二维码已过期，请点击重新获取"}
    # A different connector cannot opt into WeChat's new state or change its QR flow.
    other = host.store.save_connection({"channel": "whatsapp", "name": "other", "values": {}})
    host.connector_status(other, {"state": "login"})
    assert other not in host.statuses


@pytest.mark.parametrize("channel", ["wechat", "feishu"])
def test_reconnect_preserves_config_credentials_and_sessions(tmp_path, channel):
    async def scenario():
        host = Host(tmp_path)
        identity = host.store.save_connection({
            "channel": channel, "name": "fixture",
            "values": ({"app_id": "cli_0123456789abcdef", "app_secret": "fixture"}
                       if channel == "feishu" else {}),
        })
        host.store.enable(identity, True)
        before = host.store.connection(identity)
        thread = host.store.admit(identity, {
            "id": "message", "sender": "reader", "conversation": "reader", "text": "hello",
        }, str(tmp_path / "main/default"))
        directory = host.store.root / "accounts" / before["state_key"]
        directory.mkdir(parents=True, exist_ok=True)
        login = directory / "login.json"
        login.write_text('saved credentials', encoding='utf-8')
        started = []

        async def supervise(key):
            started.append(key)

        host.supervise = supervise
        await host.change({"action": "connection-reconnect", "id": identity})
        await asyncio.sleep(0)
        assert started == [identity]
        assert host.store.connection(identity) == before
        assert login.read_text(encoding='utf-8') == 'saved credentials'
        with host.store.db() as db:
            session = db.execute(
                'SELECT thread FROM user_sessions WHERE sender=?', ('reader',),
            ).fetchone()
            assert session['thread'] == thread
        assert host.statuses[identity]['state'] == 'connecting'
        other = host.store.save_connection({"channel": "whatsapp", "name": "other", "values": {}})
        with pytest.raises(ValueError, match='仅支持微信和飞书'):
            await host.change({"action": "connection-reconnect", "id": other})
        host.store.enable(identity, False)
        with pytest.raises(ValueError, match='启用此接入'):
            await host.change({"action": "connection-reconnect", "id": identity})

    asyncio.run(scenario())
