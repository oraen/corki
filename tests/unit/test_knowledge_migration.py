"""Concurrent real SQLite openers must safely upgrade the same legacy knowledge DB."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from corki.knowledge.store import KnowledgeStore


def test_concurrent_legacy_migration_keeps_data_and_transaction_contract(tmp_path):
    store = KnowledgeStore(tmp_path)
    with store.connect() as db:
        db.execute("ALTER TABLE documents DROP COLUMN owner_pid")
        db.execute(
            "INSERT INTO documents(id,path,title,hash,updated) VALUES(?,?,?,?,?)",
            ("existing", "用户.md", "existing document", "fixture-hash", 1),
        )
    barrier = Barrier(8)

    def open_legacy(_):
        barrier.wait(timeout=30)
        with KnowledgeStore(tmp_path).connect() as db:
            # Schema initialization must not leave a transaction open.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,title,owner_pid FROM documents").fetchone()
            assert tuple(row) == ("existing", "existing document", 0)
            columns = [r[1] for r in db.execute("PRAGMA table_info(documents)")]
            assert columns.count("owner_pid") == 1

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(open_legacy, range(8)))
