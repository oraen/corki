"""Per-user consumption survives routing resets and stays isolated across accounts."""
import sqlite3
from contextlib import contextmanager

from corki.storage.sqlite import SQLiteSessionRepository
from corki_gateway.store import Store
from test_access import account, message


def record(home, thread, key, input_tokens, output_tokens):
    path = home/'sessions/corki.db'
    SQLiteSessionRepository(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT OR IGNORE INTO threads(id,cwd) VALUES (?, 'fixture')",(thread,))
        db.execute('INSERT INTO model_steps(step_id,thread_id,turn_id,input_tokens,output_tokens,'
                   "cached_tokens,reasoning_tokens,metadata_json) VALUES (?,?,?, ?,?,500,100,'{}')",
                   (key,thread,key,input_tokens,output_tokens))
    from corki.storage.usage_ledger import register_usage_owner
    with sqlite3.connect(home/'gateway/gateway.db') as db:
        owner=db.execute('SELECT u.channel,u.sender,c.type_id FROM user_usage_threads u JOIN connections c ON c.id=u.channel WHERE u.thread=?',(thread,)).fetchone()
    if owner:
        register_usage_owner(path,thread,kind=owner[2],channel=owner[0],sender=owner[1])


def test_totals_keep_old_sessions_after_both_resets_and_receipt_cleanup(tmp_path):
    s=Store(tmp_path);a=account(s,session_validation='true');b=account(s,'Other',session_validation='true')
    cwd=str(tmp_path/'main/default')
    for channel in (a,b):
        s.allows_message(s.connection(channel),message('apply'))
    one=s.admit(a,message('a1'),cwd);record(tmp_path,one,'a1',1000,200)
    other=s.admit(a,message('b1','bob'),cwd);record(tmp_path,other,'b1',5000,1000)
    foreign=s.admit(b,message('c1'),cwd);record(tmp_path,foreign,'c1',9000,1000)
    s.reset_current_session(a,one)
    two=s.admit(a,message('a2'),cwd);record(tmp_path,two,'a2',3000,400)
    s.reset_conversation(a)
    three=s.admit(a,message('a3'),cwd);record(tmp_path,three,'a3',500,60)
    with s.db() as db: db.execute('DELETE FROM receipts')
    s=Store(tmp_path)
    assert s.user_usage.totals(a,['alice','bob','empty']) == {'alice':5160,'bob':6000,'empty':0}
    assert s.applications(a,name='alice')['requests'][0]['token_usage']==5160
    assert s.applications(b)['requests'][0]['token_usage']==10000
    assert s.admit(a,message('a3'),cwd)==three  # receipt pruning never duplicates ownership
    assert s.user_usage.totals(a,['alice'])['alice']==5160


def test_backfill_old_threads_and_skip_ambiguous_history(tmp_path):
    s=Store(tmp_path);a=account(s);cwd=str(tmp_path/'main/default')
    one=s.admit(a,message('1'),cwd);record(tmp_path,one,'1',100,20)
    s.reset_current_session(a,one)
    two=s.admit(a,message('2'),cwd);record(tmp_path,two,'2',1000,200)
    mixed=s.admit(a,message('3','bob'),cwd);record(tmp_path,mixed,'3',9000,0)
    with s.db() as db:
        db.execute("INSERT INTO routes(key,thread,channel,sender_id) VALUES ('legacy-mixed',?,?,?)",(mixed,a,'alice'))
        db.execute('DELETE FROM user_usage_threads')
        db.execute("DELETE FROM gateway_meta WHERE key='user-usage-v1'")
    s=Store(tmp_path)
    assert s.user_usage.totals(a,['alice','bob'])=={'alice':1320,'bob':9000}
    assert Store(tmp_path).user_usage.totals(a,['alice'])=={'alice':1320}


def test_page_query_uses_covering_index_and_no_unavailable_zero(tmp_path):
    s=Store(tmp_path);a=account(s);cwd=str(tmp_path/'main/default')
    assert s.user_usage.totals(a,['alice'])=={'alice':0}
    thread=s.admit(a,message('1'),cwd);record(tmp_path,thread,'1',1,2)
    original=s.db;queries=[]
    @contextmanager
    def traced():
        with original() as db:
            db.set_trace_callback(queries.append)
            yield db
    s.db=traced
    assert s.user_usage.totals(a,['alice'])=={'alice':3}
    query=next(q for q in queries if q.startswith('SELECT u.sender'))
    with original() as db:
        db.execute('ATTACH DATABASE ? AS usage_core',((tmp_path/'sessions/corki.db').as_uri()+'?mode=ro',))
        plan=[r['detail'] for r in db.execute('EXPLAIN QUERY PLAN '+query)]
    assert any('SEARCH s USING COVERING INDEX usage_events_thread' in p for p in plan),plan
    with sqlite3.connect(tmp_path/'sessions/corki.db') as db: db.execute('DROP TABLE usage_events')
    assert s.user_usage.totals(a,['alice'])=={'alice':None}
