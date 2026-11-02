"""Recall never rewrites Agent history or trusts message IDs supplied by the UI."""
import asyncio
import json

import pytest

from corki_gateway.history import history
from corki_gateway.messages import send_logged
from corki_gateway.recall import public_message, receipt
from corki_gateway.service import Host
from test_messages import account, message

RECEIPT = {'robot_code': 'fixture-app', 'keys': ['part-1', 'part-2'], 'complete': True}


def test_receipts_survive_restart_and_are_private(tmp_path):
    async def scenario():
        host = Host(tmp_path, online=False)
        identity = account(host.store)
        m = message()
        host.store.messages.contact(identity, m)
        class Connector:
            async def call(self, *_args, **_kwargs):
                return {'sent': True, 'receipt': {**RECEIPT, 'unexpected_secret': 'never expose'}}
        host.connectors[identity] = Connector()
        await send_logged(host, identity, {'message': m, 'text': 'Private retained text'})
        session = history(host.store, identity, {}, {})['thread']
        from corki_gateway.store import Store
        restored = Store(tmp_path)
        row = history(restored, identity, {'session': session}, {})['messages'][0]
        assert row['can_recall']
        assert 'recall_receipt' not in row and 'part-1' not in json.dumps(row)
        with restored.db() as db:
            saved = db.execute('SELECT recall_receipt FROM dingtalk_messages').fetchone()[0]
        assert json.loads(saved) == RECEIPT
        await host.close()
    asyncio.run(scenario())


def test_scoped_idempotent_partial_recall_then_retry(tmp_path):
    async def scenario():
        host = Host(tmp_path, online=False)
        identity = account(host.store)
        m = message()
        host.store.messages.contact(identity, m)
        host.store.messages.contact(identity, message('bob-in', 'bob'))
        entry = host.store.messages.record(identity, m, 'manual:fixture', 'assistant', 'manual', 'Original answer')
        host.store.messages.finish(identity, entry['event_key'], 'sent', delivery_receipt=RECEIPT)
        session = 'dt:' + str(host.store.messages.peer(identity, history(host.store, identity, {}, {})['thread'])['id'])
        with host.store.db() as db:
            session = 'dt:' + str(db.execute("SELECT id FROM dingtalk_contacts WHERE sender='alice'").fetchone()[0])
            other = 'dt:' + str(db.execute("SELECT id FROM dingtalk_contacts WHERE sender='bob'").fetchone()[0])
        command = {'action': 'recall-message', 'id': identity, 'session': session, 'message_id': 'dtmsg:'+str(entry['id'])}
        entered, gate = asyncio.Event(), asyncio.Event()
        class Connector:
            calls = []
            async def call(self, method, params, **_):
                assert method == 'recall'
                self.calls.append(params['receipt'])
                entered.set()
                await gate.wait()
                return {'recalled': ['part-1'], 'error': 'fixture partial'} if len(self.calls) == 1 else {'recalled': ['part-2']}
        connector = Connector()
        host.connectors[identity] = connector
        try:
            with pytest.raises(ValueError, match='当前联系人'):
                await host.change({**command, 'session': other})
            first = asyncio.create_task(host.change(command))
            await entered.wait()
            second = await host.change(command)
            assert second['message']['recall_state'] == 'recalling'
            assert len(connector.calls) == 1
            gate.set()
            result = await first
            assert result['message']['recall_state'] == 'partial'
            assert result['message']['content'] == 'Original answer'
            result = await host.change(command)
            assert connector.calls[-1]['keys'] == ['part-2']
            assert result['message']['recall_state'] == 'recalled'
            assert result['message']['content'] == 'Original answer'
            assert not result['message']['can_recall']
            assert (await host.change(command))['message']['recall_state'] == 'recalled'
            assert len(connector.calls) == 2
            view = history(host.store, identity, {'session': session}, {})['messages'][0]
            assert view['content'] == 'Original answer' and not view['can_recall']
            with host.store.db() as db:
                assert db.execute('SELECT content FROM dingtalk_messages WHERE id=?',(entry['id'],)).fetchone()[0] == 'Original answer'
        finally:
            gate.set()
            await host.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['legacy', 'user', 'failed', 'bad-receipt'])
def test_unrecallable_messages_never_call_platform(tmp_path, kind):
    async def scenario():
        host = Host(tmp_path, online=False)
        identity = account(host.store)
        m = message()
        host.store.messages.contact(identity, m)
        row = host.store.messages.record(identity, m, 'fixture', 'user' if kind=='user' else 'assistant', 'manual', 'Answer', 'failed' if kind=='failed' else 'sent')
        if kind != 'legacy':
            host.store.messages.finish(identity, 'fixture', row['status'], delivery_receipt=RECEIPT if kind!='bad-receipt' else {**RECEIPT,'complete':False})
        class Forbidden:
            async def call(self, *_, **__):
                raise AssertionError('must not call')
        host.connectors[identity] = Forbidden()
        try:
            with pytest.raises(ValueError):
                await host.change({'action':'recall-message','id':identity,'session':history(host.store,identity,{},{})['thread'],'message_id':'dtmsg:'+str(row['id'])})
        finally:
            await host.close()
    asyncio.run(scenario())


def test_recall_recovery_and_unconfirmed_result_are_not_success(tmp_path):
    async def scenario():
        host = Host(tmp_path, online=False)
        identity = account(host.store)
        m = message();host.store.messages.contact(identity,m)
        row = host.store.messages.record(identity,m,'fixture','assistant','manual','Answer')
        host.store.messages.finish(identity,'fixture','sent',delivery_receipt=RECEIPT)
        with host.store.db() as db:
            db.execute("UPDATE dingtalk_messages SET recall_state='recalling'")
        host.store.messages.recover()
        class Empty:
            async def call(self,*_,**__):return {'recalled':['unrelated']}
        host.connectors[identity] = Empty()
        try:
            result=await host.change({'action':'recall-message','id':identity,'session':history(host.store,identity,{},{})['thread'],'message_id':'dtmsg:'+str(row['id'])})
            assert result['message']['recall_state']=='failed'
            assert result['message']['content']=='Answer'
        finally:await host.close()
    asyncio.run(scenario())
