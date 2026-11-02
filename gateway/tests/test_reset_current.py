"""A reset changes only the selected user's future context, never message history."""
import asyncio
import pytest

from corki_gateway.service import Host
from corki_gateway.store import Store
from corki_gateway.history import history
from test_access import Transport, account, message, status, drain
from test_messages import account as ding_account


def test_current_reset_cancels_only_selected_user(tmp_path):
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        async def run(thread, text, *_):
            started.set()
            await release.wait()
            return text
        host = Host(tmp_path, runner=run, online=False)
        identity = account(host.store, session_validation='false')
        host.connectors[identity] = Transport()
        try:
            a = await host.inbound(identity, message('a', 'alice', 'first'))
            await started.wait()
            await host.inbound(identity, message('a2', 'alice', 'queued'))
            b = await host.inbound(identity, message('b', 'bob', 'other'))
            result = await host.change({'action':'reset-current-conversation','id':identity,'session':a['thread']})
            assert result['reset_session'] != a['thread']
            assert status(host.store, identity, 'a') == 'cancelled'
            assert status(host.store, identity, 'a2') == 'cancelled'
            assert not host.tasks[(identity,'b')].cancelling()
            cwd = str(tmp_path / 'main/default')
            assert host.store.admit(identity, message('a3'), cwd) == result['reset_session']
            assert host.store.admit(identity, message('b2','bob'), cwd) == b['thread']
            with pytest.raises(ValueError, match='不属于'):
                await host.change({'action':'reset-current-conversation','id':identity,'session':a['thread']})
            release.set()
            await drain(host)
        finally:
            release.set()
            await host.close()
    asyncio.run(scenario())


def test_dingtalk_contact_reset_preserves_transcript_and_other_users(tmp_path):
    store = Store(tmp_path)
    identity = ding_account(store)
    cwd = str(tmp_path / 'main/default')
    a, b = message('a'), message('b','bob')
    store.messages.contact(identity, a)
    store.messages.record(identity, a, 'user:a', 'user', 'user', 'Retained fixture')
    old = store.admit(identity, a, cwd)
    other = store.admit(identity, b, cwd)
    session = history(store, identity, {}, {})['thread']
    assert store.reset_current_session(identity, session) == session
    assert history(store, identity, {'session':session}, {})['messages'][0]['content'] == 'Retained fixture'
    assert store.admit(identity, message('a2'), cwd) != old
    assert store.admit(identity, message('b2','bob'), cwd) == other
    foreign = account(store, name='other')
    with pytest.raises(ValueError):
        store.reset_current_session(foreign, session)


def test_pending_queue_reset_is_scoped_by_sender(tmp_path):
    store = Store(tmp_path)
    identity = account(store)
    store.http_queue.enqueue(identity, {'messages':[message('a'),message('b','bob')]})
    store.http_queue.skip_pending(identity, 'alice')
    assert store.http_queue.get(identity,'a')['phase'] == 'skipped'
    assert store.http_queue.get(identity,'b')['phase'] == 'queued'
