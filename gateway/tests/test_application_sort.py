"""Conversation ordering is global and paginated, not a sort of one visible page."""
import time

import pytest
from corki_gateway.store import Store
from test_access import account, message
from test_user_usage import record


def test_ordering_keyset_pages_and_latest_message(tmp_path):
    store=Store(tmp_path)
    channel=account(store,session_validation='true')
    # Deliberately invert creation order and usage/activity order across a page boundary.
    now=time.time()
    with store.db() as db:
        for index in range(105):
            sender=f'user-{index:03}'
            db.execute("INSERT INTO access_requests VALUES(?,?,?,?,?,?,?,'approved')",
                       (channel,sender,sender,'Initial message',sender,sender,now-10000))
            db.execute('INSERT INTO user_usage_threads VALUES(?,?,?)',
                       ('thread-'+sender,channel,sender))
    for index in range(105):
        sender=f'user-{index:03}'
        record(tmp_path,'thread-'+sender,'step-'+sender,105-index,0)
        store.messages.record(channel,message(sender,sender),sender,'user','user','Hello',
                              created_at=now-index)
    first=store.applications(channel,sort='tokens')
    second=store.applications(channel,first['before'],sort='tokens')
    users=[r['sender_id'] for r in first['requests']+second['requests']]
    assert users==[f'user-{i:03}' for i in range(105)]
    assert second['before'] is None
    recent=store.applications(channel,sort='last_chat')
    older=store.applications(channel,recent['before'],sort='last_chat')
    assert [r['sender_id'] for r in recent['requests']+older['requests']]==users
    by_id=store.applications(channel)
    assert by_id['requests'][0]['sender_id']=='user-104'
    filtered=store.applications(channel,sort='tokens',name='user-00',status='approved')
    assert len(filtered['requests'])==10
    assert filtered['requests'][0]['token_usage']==105
    # Agent/manual replies update last-chat time without changing the access message.
    store.messages.record(channel,message('manual','user-104'),'manual','assistant','manual','Reply',
                          created_at=now+10)
    updated=store.applications(channel,sort='last_chat')['requests'][0]
    assert updated['sender_id']=='user-104'
    assert updated['last_chat_at']>=int(now+10)
    assert updated['first_message']=='Initial message'
    for invalid in ('unknown',{},1):
        with pytest.raises(ValueError):
            store.applications(channel,sort=invalid)
    for cursor in (recent['before'],{'sort':'tokens','id':1,'value':float('nan')},0):
        with pytest.raises(ValueError):
            store.applications(channel,cursor,sort='tokens')


def test_equal_sort_values_use_descending_id_without_duplicates(tmp_path):
    store=Store(tmp_path)
    channel=account(store,session_validation='true')
    with store.db() as db:
        for index in range(105):
            sender=str(index)
            db.execute("INSERT INTO access_requests VALUES(?,?,?,?,?,?,?,'pending')",
                       (channel,sender,sender,'Message',sender,sender,1000))
    for sort in ('last_chat','tokens'):
        page=store.applications(channel,sort=sort)
        tail=store.applications(channel,page['before'],sort=sort)
        assert [r['sender_id'] for r in page['requests']+tail['requests']]==[
            str(i) for i in reversed(range(105))]


def test_last_activity_queries_use_covering_indexes(tmp_path):
    store = Store(tmp_path)
    with store.db() as db:
        message_plan = [row['detail'] for row in db.execute(
            'EXPLAIN QUERY PLAN SELECT unixepoch(max(created_at)) '
            'FROM dingtalk_messages WHERE channel=? AND sender=?', ('fixture', 'user'))]
        route_plan = [row['detail'] for row in db.execute(
            'EXPLAIN QUERY PLAN SELECT max(last_seen) FROM routes '
            'WHERE channel=? AND sender_id=?', ('fixture', 'user'))]
    assert any('COVERING INDEX dingtalk_message_activity' in row for row in message_plan)
    assert any('COVERING INDEX routes_user_activity' in row for row in route_plan)
