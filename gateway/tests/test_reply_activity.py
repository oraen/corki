"""Current-reply cancellation is distinct from revoking a user's access."""
import asyncio

import pytest

from corki_gateway.control import control
from corki_gateway.service import Host
from test_access import Transport, account, drain, message, status


def test_stop_only_captured_reply_and_keep_authorization(tmp_path):
    async def scenario():
        started = {name: asyncio.Event() for name in ('first', 'next', 'other')}
        release = asyncio.Event()
        cleanup = asyncio.Event()

        async def run(thread, text, *_):
            started[text].set()
            try:
                await release.wait()
            finally:
                if text == 'first':
                    await cleanup.wait()
            return text

        host = Host(tmp_path, runner=run, online=False)
        identity = account(host.store, session_validation='true')
        host.connectors[identity] = Transport()
        for sender in ('alice', 'bob'):
            host.store.allows_message(host.store.connection(identity), message('apply-'+sender, sender))
            host.store.set_application_status(identity, sender, 'approved')
        command = {'action': 'stop-reply', 'id': identity, 'sender': 'alice', 'message_ids': ['first']}
        try:
            await host.inbound(identity, message('first', text='first'))
            await asyncio.wait_for(started['first'].wait(), 2)
            await host.inbound(identity, message('next', text='next'))
            await host.inbound(identity, message('other', 'bob', 'other'))
            await asyncio.sleep(0)
            page = await host.change({'action': 'applications', 'id': identity})
            assert page['reply_activity']['alice']['message_ids'] == ['first']
            assert not page['reply_activity']['alice']['stopping']
            assert (await host.change({**command, 'sender': 'bob'}))['requested'] == 0
            result = await host.change(command)
            assert result['requested'] == 1 and result['reply_activity']['alice']['stopping']
            await asyncio.sleep(0.01)
            assert (await host.change(command))['requested'] == 1
            assert host.tasks[(identity, 'first')].cancelling() == 1
            cleanup.set()
            await asyncio.wait_for(started['next'].wait(), 2)
            assert status(host.store, identity, 'first') == 'cancelled'
            assert (await host.change(command))['requested'] == 0  # stale dialog
            assert host.reply_activity(identity)['alice']['message_ids'] == ['next']
            assert all(r['status'] == 'approved' for r in host.store.applications(identity)['requests'])
            release.set()
            await drain(host)
            assert status(host.store, identity, 'next') == 'completed'
            assert status(host.store, identity, 'other') == 'completed'
            assert host.reply_activity(identity) == {}
        finally:
            release.set()
            cleanup.set()
            await host.close()

    asyncio.run(scenario())


def test_search_displayed_id_and_nickname_offline(tmp_path):
    host = Host(tmp_path, online=False)
    store = host.store
    identity = store.save_connection({'channel': 'dingtalk', 'name': 'Fixture', 'values': {
        'client_id': 'fixture', 'client_secret': 'fixture', 'session_validation': 'true'}})
    row = store.connection(identity)
    store.allows_message(row, {**message('apply'), 'staff_id': 'STAFF-1234', 'name': '测试昵称'})
    for name in ('昵称', 'staff-123', '1234'):
        page = control(tmp_path, {'action': 'applications', 'id': identity, 'name': name})
        assert len(page['requests']) == 1
        assert page['reply_activity'] == {}
    for name in ('alice', '%', "' OR 1=1 --", 'unknown'):
        assert store.applications(identity, name=name)['requests'] == []
    other = account(store, session_validation='true')
    store.allows_message(store.connection(other), message('apply-2', 'telegram-id'))
    assert len(store.applications(other, name='gram-ID')['requests']) == 1
    with pytest.raises(ConnectionError):
        control(tmp_path, {'action': 'stop-reply', 'id': identity, 'sender': 'alice', 'message_ids': ['apply']})


@pytest.mark.parametrize('ids', [None, [], ['x']*5, [1], [''], ['x'*1001]])
def test_stop_validates_identity(tmp_path, ids):
    async def scenario():
        host = Host(tmp_path, online=False)
        identity = account(host.store)
        try:
            with pytest.raises(ValueError):
                await host.change({'action': 'stop-reply', 'id': identity, 'sender': 'alice', 'message_ids': ids})
        finally:
            await host.close()
    asyncio.run(scenario())


def test_control_reads_live_activity_and_forwards_scoped_stop(tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module('corki_gateway.control')
    seen = []
    def request(home, data, timeout):
        seen.append((home, data, timeout))
        return {'reply_activity': {'alice': {'message_ids': ['current'], 'stopping': False}}}
    monkeypatch.setattr(module, 'request', request)
    for action in ('applications', 'stop-reply'):
        command = {'action': action, 'id': 'fixture', 'sender': 'alice', 'message_ids': ['current']}
        assert module.control(tmp_path, command)['reply_activity']['alice']['message_ids'] == ['current']
        assert seen[-1] == (tmp_path, command, 10)


@pytest.mark.parametrize('card_mode', ['off', 'on', 'broken', 'send-failed'])
def test_admin_stop_notifies_once_and_records_delivery(tmp_path, card_mode):
    async def scenario():
        started, cleanup = asyncio.Event(), asyncio.Event()
        async def runner(*_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await cleanup.wait()
        class Connector:
            def __init__(self):
                self.calls = []
            async def call(self, method, params, **_):
                self.calls.append((method, params))
                if method == 'progress':
                    return {'supported': card_mode == 'on'}
                if card_mode == 'send-failed':
                    raise OSError('fixture delivery failure')
                return {'sent': True}
        host = Host(tmp_path, runner=runner, online=False)
        identity = host.store.save_connection({'channel': 'dingtalk', 'name': 'Fixture', 'values': {
            'client_id': 'fixture', 'client_secret': 'fixture', 'session_validation': 'false',
            'ai_card_enabled': 'true' if card_mode in ('on', 'broken') else 'false',
            'ai_card_template': 'fixture.schema'}})
        host.store.enable(identity, True)
        connector = Connector()
        host.connectors[identity] = connector
        incoming = {**message('current'), 'staff_id': 'fixture-user'}
        command = {'action': 'stop-reply', 'id': identity, 'sender': 'alice', 'message_ids': ['current']}
        try:
            await host.inbound(identity, incoming)
            await asyncio.wait_for(started.wait(), 2)
            assert (await host.change(command))['requested'] == 1
            await asyncio.sleep(0.01)
            assert (await host.change(command))['requested'] == 1
            cleanup.set()
            await drain(host)
            assert (await host.change(command))['requested'] == 0
            notices = [(method, params) for method, params in connector.calls
                       if params.get('text') == '回答已被管理员终止']
            assert len(notices) == 1
            method, params = notices[0]
            assert params['message']['sender'] == 'alice'
            if card_mode == 'on':
                assert method == 'progress' and params['phase'] == 'stopped'
                assert not any(method == 'send' for method, _ in connector.calls)
            else:
                assert method == 'send' and params['fallback_proactive'] is True
                assert params['card'] == {'enabled': False}
            with host.store.db() as db:
                rows = db.execute("SELECT content,status,kind FROM dingtalk_messages WHERE event_key=?",
                                  ('admin-stop:current',)).fetchall()
            assert len(rows) == 1 and rows[0]['content'] == '回答已被管理员终止'
            assert rows[0]['kind'] == 'system'
            assert rows[0]['status'] == ('failed' if card_mode == 'send-failed' else 'sent')
            assert status(host.store, identity, 'current') == 'cancelled'
            assert not host.admin_stops
        finally:
            cleanup.set()
            await host.close()
    asyncio.run(scenario())
