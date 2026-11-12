"""Global switches and daily retention use only disposable homes and histories."""
import asyncio
import importlib.util
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from corki.config import CorkiSettings
from corki.protocol.ids import ThreadId
from corki.storage.sqlite import SQLiteSessionRepository
from corki.storage.thread_delete import SQLiteThreadDeleteStore
from corki.storage.thread_writer import ThreadWriterLease
from corki.storage.transcripts import transcript_name

spec = importlib.util.spec_from_file_location("global_config_manager", Path(__file__).parents[1] / "server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

@pytest.fixture
def store(tmp_path):
    home=tmp_path/'home';home.mkdir()
    (home/'settings.json').write_text('// preserve comments\n{"providers":{},"unrelated":{"value":42}}\n')
    return module.Store(home,tmp_path)

def change(store,key,enabled):
    return store.mutate({'kind':'global-config','action':'set','key':key,'enabled':enabled,'revision':module.revision(store.config_text())})

@pytest.mark.parametrize('key,path',[('self_evolution',('features','self_evolution')),('memories',('memories','enabled')),('auto_cleanup',('sessions','auto_cleanup'))])
def test_switches_preserve_other_config_and_history(store,key,path,monkeypatch):
    monkeypatch.setattr(store.maintenance,'start_if_due',Mock())
    for enabled in (True,False):
        result=change(store,key,enabled)
        content=store.config_text();config=module.document.loads(content)
        assert config[path[0]][path[1]] is enabled
        assert result['global_config'][key] is enabled
        assert config['unrelated']=={'value':42} and '// preserve comments' in content
        assert result['revision']==module.revision(content)
        assert store.config_document()['versions']
    if key=='memories':
        assert CorkiSettings.for_directory(store.workspace,config_file=store.config).memories_enabled is False

def test_approval_switch_uses_real_permission_selection(store):
    change(store,'approval_free',True)
    config=module.document.loads(store.config_text())
    assert config['default_permissions']==':danger-full-access'
    assert config['approval_policy']=='never'
    settings=CorkiSettings.for_directory(store.workspace,config_file=store.config)
    assert json.loads(settings.execution_permissions.profile_json)['default_permissions']==':danger-full-access'
    change(store,'approval_free',False)
    config=module.document.loads(store.config_text())
    assert config['default_permissions']==':workspace' and config['approval_policy']=='on-request'

@pytest.mark.parametrize('mode,profile,policy', [
    ('read-only', ':read-only', 'on-request'),
    ('workspace', ':workspace', 'on-request'),
    ('full', ':danger-full-access', 'never'),
])
def test_permission_dropdown_saves_real_configuration(store, mode, profile, policy):
    result = store.mutate({'kind':'global-config', 'action':'set', 'key':'permissions',
        'value':mode, 'revision':module.revision(store.config_text())})
    config = module.document.loads(store.config_text())
    assert config['default_permissions'] == profile
    assert config['approval_policy'] == policy
    assert result['global_config']['permissions'] == mode
    settings = CorkiSettings.for_directory(store.workspace, config_file=store.config)
    assert json.loads(settings.execution_permissions.profile_json)['default_permissions'] == profile
    assert config['unrelated'] == {'value':42}

def test_permission_switch_converts_concrete_profile_without_conflict(store):
    store.config.write_text('{"execution":{"profile":{"type":"disabled"}}}')
    change(store,'approval_free',True)
    config=module.document.loads(store.config_text())
    assert 'profile' not in config['execution']
    CorkiSettings.for_directory(store.workspace,config_file=store.config)

@pytest.mark.parametrize('payload',[{'key':'unknown','enabled':True},{'key':'memories','enabled':'true'},{'key':'auto_cleanup','enabled':1}])
def test_reject_invalid_switches(store,payload):
    with pytest.raises(ValueError):store.mutate({'kind':'global-config','action':'set',**payload})

def test_reject_stale_revision_and_invalid_config_sections(store):
    with pytest.raises(ValueError):store.mutate({'kind':'global-config','action':'set','key':'memories','enabled':True,'revision':'old'})
    with pytest.raises(ValueError):store.validate_config('{"sessions":{"auto_cleanup":"true"}}')
    with pytest.raises(ValueError):store.validate_config('{"features":[]}')

def test_daily_claim_is_atomic_committed_before_worker_and_respects_opt_in(store,monkeypatch):
    now=datetime(2026,10,24,tzinfo=UTC)
    assert store.maintenance.start_if_due(now=now) is False
    store.config.write_text('{"sessions":{"auto_cleanup":true}}')
    workers=[]
    original_start = module.threading.Thread.start
    def start(thread):
        if thread.name != "corki-session-cleanup":
            return original_start(thread)
        with closing(sqlite3.connect(store.home/'manager/maintenance.db')) as db:
            assert db.execute("SELECT started_at FROM maintenance").fetchone()[0]==now.timestamp()
        workers.append(thread)
    monkeypatch.setattr(module.threading.Thread,'start',start)
    with ThreadPoolExecutor(max_workers=8) as pool:
        # Independent Store instances simulate independent manager processes.
        stores=[module.Store(store.home,store.workspace) for _ in range(8)]
        assert sum(pool.map(lambda s:s.maintenance.start_if_due(now=now),stores))==1
    assert len(workers)==1
    assert not store.maintenance.start_if_due(now=now+timedelta(days=1))
    # Stop checking exact initial timestamp for the second day's claim.
    monkeypatch.setattr(module.threading.Thread,'start',lambda thread:workers.append(thread))
    assert store.maintenance.start_if_due(now=now+timedelta(days=1,seconds=1))

@pytest.mark.parametrize('now,expected',[('2026-05-31','2026-02-28'),('2024-05-31','2024-02-29'),('2026-01-15','2025-10-15')])
def test_three_calendar_months(now,expected):
    assert str(module.SessionMaintenance.cutoff(datetime.fromisoformat(now)).date())==expected

async def histories(store):
    repository=SQLiteSessionRepository(store.database)
    for identity in ('old','recent','busy','running','fresh-message','boundary'):
        await repository.create_thread(ThreadId(identity),store.workspace)
    with closing(sqlite3.connect(store.database)) as db,db:
        db.execute("UPDATE threads SET updated_at='2026-01-01T00:00:00Z'")
        db.execute("UPDATE threads SET updated_at='2026-10-24T00:00:00Z' WHERE id='recent'")
        db.execute("UPDATE threads SET updated_at='2026-07-24T00:00:00Z' WHERE id='boundary'")
        db.execute("INSERT INTO turns(id,thread_id,status,user_input,updated_at) VALUES ('running-turn','running','running','fixture','2026-01-01')")
        db.execute("INSERT INTO conversation_items(id,thread_id,turn_id,sequence,kind,payload_json,created_at) VALUES ('new-item','fresh-message','turn',0,'user_message','{}','2026-10-24')")
    root=store.database.with_name(store.database.name+'.transcripts');root.mkdir()
    (root/transcript_name('old')).write_text('fixture history')
    return root

def test_real_retention_deletes_idle_only_and_preserves_workspace(store):
    async def run():
        store.config.write_text('{"sessions":{"auto_cleanup":true}}')
        root=await histories(store)
        workspace_file=store.workspace/'keep.txt';workspace_file.write_text('keep')
        lease=ThreadWriterLease(store.database,ThreadId('busy'));await lease.acquire()
        desktop=store.home/'app/desktop.db';desktop.parent.mkdir()
        with closing(sqlite3.connect(desktop)) as db,db:
            db.executescript("CREATE TABLE metadata(id TEXT,title TEXT);CREATE TABLE values_store(key TEXT,value TEXT);")
            db.execute("INSERT INTO metadata VALUES ('old','fixture')")
            db.execute("INSERT INTO values_store VALUES ('draft:old','fixture')")
        try:
            assert await store.maintenance.clean('2026-07-24T00:00:00+00:00')==1
        finally:await lease.aclose()
        with closing(sqlite3.connect(store.database)) as db:
            assert {r[0] for r in db.execute('SELECT id FROM threads')}=={'recent','busy','running','fresh-message','boundary'}
        with closing(sqlite3.connect(desktop)) as db:
            assert db.execute('SELECT count(*) FROM metadata').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM values_store').fetchone()[0]==0
        assert not (root/transcript_name('old')).exists()
        assert workspace_file.read_text()=='keep'
    asyncio.run(run())

def test_conditional_delete_rechecks_recent_activity_before_cleanup(store):
    async def run():
        await histories(store)
        callback=Mock()
        # Candidate was old when scanned, but received a new reply before locking.
        with closing(sqlite3.connect(store.database)) as db,db:
            db.execute("UPDATE threads SET updated_at='2026-10-24' WHERE id='old'")
        assert not await SQLiteThreadDeleteStore(store.database).delete(ThreadId('old'),inactive_before='2026-07-24',before_delete=callback)
        callback.assert_not_called()
    asyncio.run(run())

def test_disabling_cleanup_stops_queued_work(store):
    async def run():
        await histories(store)
        assert await store.maintenance.clean('2026-07-24')==0
    asyncio.run(run())
