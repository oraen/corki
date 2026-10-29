"""Copy-only migration must preserve every historical checkpoint and pending write."""

import asyncio
import hashlib
import sqlite3
from contextlib import closing

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from corki.core.checkpoint import checkpoint_serializer
from corki.core.checkpoint_store import CompactSqliteSaver
from corki.protocol.ids import TurnId
from corki.protocol.items import UserMessageItem
from corki.storage.checkpoint_migrate import compact_copy


def test_mixed_copy_preserves_history_metadata_and_orphan_pending_writes(tmp_path):
    async def check():
        source, target, second_copy = [tmp_path / name for name in ("old.db", "new.db", "again.db")]
        item = UserMessageItem("原始内容" * 1000, TurnId("turn"))
        original = empty_checkpoint()
        original["channel_values"] = {"request_items": [item], "context_instructions": "prefix"}
        config = {"configurable": {"thread_id": "thread:turn", "checkpoint_ns": ""}}
        async with AsyncSqliteSaver.from_conn_string(str(source)) as old:
            old.serde = checkpoint_serializer()
            first = await old.aput(config, original, {"source": "input", "step": 0}, {})
            await old.aput_writes(first, [("request_items", [item])], "task")
            orphan = {"configurable": {**first["configurable"], "checkpoint_id": "orphan"}}
            await old.aput_writes(orphan, [("request_items", [item])], "task-orphan")
        async with CompactSqliteSaver.from_conn_string(str(source)) as current:
            next_checkpoint = empty_checkpoint()
            next_checkpoint["channel_values"] = original["channel_values"]
            last = await current.aput(first, next_checkpoint, {"step": 1}, {})
            await current.aput_writes(last, [("dispatch_tools", [])], "task-2")
        with closing(sqlite3.connect(source)) as db, db:
            db.execute("UPDATE writes SET idx=7 WHERE task_id='task-orphan'")
            original_rows = db.execute(
                "SELECT thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,metadata "
                "FROM checkpoints ORDER BY checkpoint_id"
            ).fetchall()
            original_keys = db.execute(
                "SELECT thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel FROM writes "
                "ORDER BY checkpoint_id,task_id,idx"
            ).fetchall()
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        result = await compact_copy(source, target)
        assert result["checkpoints"] == 2 and result["writes"] == 3
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        with closing(sqlite3.connect(target)) as db:
            assert (
                db.execute(
                    "SELECT thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,metadata "
                    "FROM checkpoints ORDER BY checkpoint_id"
                ).fetchall()
                == original_rows
            )
            assert (
                db.execute(
                    "SELECT thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel FROM writes "
                    "ORDER BY checkpoint_id,task_id,idx"
                ).fetchall()
                == original_keys
            )
        async with (
            CompactSqliteSaver.from_conn_string(str(source)) as old,
            CompactSqliteSaver.from_conn_string(str(target)) as new,
        ):
            assert [r async for r in old.alist(None)] == [r async for r in new.alist(None)]
        await compact_copy(target, second_copy)
        async with CompactSqliteSaver.from_conn_string(str(second_copy)) as again:
            assert (await again.aget_tuple(last)).checkpoint["channel_values"]["request_items"] == [
                item
            ]
        with pytest.raises(FileExistsError):
            await compact_copy(source, target)
        with pytest.raises(ValueError, match="不能与源文件相同"):
            await compact_copy(source, source)

    asyncio.run(check())


def test_failed_copy_removes_only_its_partial_output(tmp_path):
    source, target = tmp_path / "broken.db", tmp_path / "copy.db"
    with closing(sqlite3.connect(source)) as db, db:
        db.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(ValueError, match="不是完整的 checkpoint"):
        asyncio.run(compact_copy(source, target))
    assert source.is_file() and not target.exists()


def test_copy_refuses_unrecognized_tables_instead_of_silently_dropping_them(tmp_path):
    async def check():
        source, target = tmp_path / "source.db", tmp_path / "target.db"
        async with CompactSqliteSaver.from_conn_string(str(source)) as saver:
            await saver.setup()
            await saver.conn.execute("CREATE TABLE extra_user_data(value TEXT)")
            await saver.conn.execute("INSERT INTO extra_user_data VALUES('keep')")
            await saver.conn.commit()
        with pytest.raises(ValueError, match="未知表"):
            await compact_copy(source, target)
        assert not target.exists()
        with closing(sqlite3.connect(source)) as db:
            assert db.execute("SELECT value FROM extra_user_data").fetchone()[0] == "keep"

    asyncio.run(check())
