"""Create a losslessly compacted copy; never replace or modify the source database.

Run after stopping Corki for a cutover-ready copy. A running source can still be
read consistently, but new writes after the read transaction starts are excluded.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

import aiosqlite

from corki.core.checkpoint_store import CompactSqliteSaver


class _ReadOnlySaver(CompactSqliteSaver):
    async def setup(self):
        # Source schemas already exist. The inherited read paths must not execute
        # CREATE, PRAGMA journal_mode, migration or commits against the source.
        return None


async def compact_copy(source: Path, destination: Path):
    source = source.expanduser().resolve(strict=True)
    destination = destination.expanduser().absolute()
    if source == destination.resolve():
        raise ValueError("目标文件不能与源文件相同")
    # Exclusive creation protects existing files, aliases and symbolic links.
    with destination.open("xb"):
        destination.chmod(0o600)
    identity = destination.stat()
    complete = False
    counts = {"checkpoints": 0, "writes": 0}
    try:
        async with aiosqlite.connect(source.as_uri() + "?mode=ro", uri=True) as connection:
            reader = _ReadOnlySaver(connection)
            await connection.execute("BEGIN")
            async with connection.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ) as schema:
                objects = await schema.fetchall()
            tables = {name for kind, name, _ in objects if kind == "table"}
            if not {"checkpoints", "writes"} <= tables:
                raise ValueError("源文件不是完整的 checkpoint 数据库")
            if tables - {"checkpoints", "writes", "checkpoint_objects"} or any(
                kind != "table" and sql is not None for kind, _, sql in objects
            ):
                raise ValueError("源库包含未知表、索引或触发器，拒绝省略未识别的数据结构")
            async with CompactSqliteSaver.from_conn_string(str(destination)) as writer:
                await writer.setup()
                # Walk the thin primary index, not a sort carrying every BLOB.
                async with connection.execute(
                    "SELECT thread_id,checkpoint_ns,checkpoint_id FROM checkpoints "
                    "ORDER BY thread_id,checkpoint_ns,checkpoint_id"
                ) as cursor:
                    async for thread, namespace, checkpoint_id in cursor:
                        config = {
                            "configurable": {
                                "thread_id": thread,
                                "checkpoint_ns": namespace,
                                "checkpoint_id": checkpoint_id,
                            }
                        }
                        stored = await reader.aget_tuple(config)
                        if stored.checkpoint.get("id") != checkpoint_id:
                            raise ValueError("checkpoint ID 与记录不一致，拒绝迁移损坏的数据")
                        parent = stored.parent_config or {
                            "configurable": {
                                "thread_id": thread,
                                "checkpoint_ns": namespace,
                            }
                        }
                        await writer.aput(parent, stored.checkpoint, stored.metadata, {})
                        async with connection.execute(
                            "SELECT metadata FROM checkpoints WHERE thread_id=? "
                            "AND checkpoint_ns=? AND checkpoint_id=?",
                            (thread, namespace, checkpoint_id),
                        ) as meta:
                            metadata = (await meta.fetchone())[0]
                        # Preserve original metadata bytes and nulls, not just parsed values.
                        await writer.conn.execute(
                            "UPDATE checkpoints SET metadata=? WHERE thread_id=? "
                            "AND checkpoint_ns=? AND checkpoint_id=?",
                            (metadata, thread, namespace, checkpoint_id),
                        )
                        await writer.conn.commit()
                        counts["checkpoints"] += 1
                # Writes are copied independently: a process may have durably
                # written a task result whose referenced checkpoint is not present.
                async with connection.execute(
                    "SELECT DISTINCT thread_id,checkpoint_ns,checkpoint_id FROM writes "
                    "ORDER BY thread_id,checkpoint_ns,checkpoint_id"
                ) as cursor:
                    async for thread, namespace, checkpoint_id in cursor:
                        async with connection.execute(
                            "SELECT task_id,idx,channel,type,value FROM writes WHERE thread_id=? "
                            "AND checkpoint_ns=? AND checkpoint_id=? ORDER BY task_id,idx",
                            (thread, namespace, checkpoint_id),
                        ) as writes:
                            raw = await writes.fetchall()
                        # Hydrate both legacy values and new immutable references using
                        # the same reader as runtime recovery, including orphan writes.
                        from langgraph.checkpoint.base import CheckpointTuple

                        snapshot = CheckpointTuple(
                            {"configurable": {"thread_id": thread, "checkpoint_ns": namespace}},
                            {},
                            {},
                            None,
                            [
                                (task, channel, reader.serde.loads_typed((kind, value)))
                                for task, _, channel, kind, value in raw
                            ],
                        )
                        restored = await reader._restore(snapshot)
                        objects, rows = {}, []
                        for record, (_, _, value) in zip(raw, restored.pending_writes, strict=True):
                            task, index, channel, _, _ = record
                            reference = writer._field(channel, value, objects)
                            kind, payload = (
                                writer._envelope({"value": reference})
                                if reference is not None
                                else writer.serde.dumps_typed(value)
                            )
                            rows.append(
                                (
                                    thread,
                                    namespace,
                                    checkpoint_id,
                                    task,
                                    index,
                                    channel,
                                    kind,
                                    payload,
                                )
                            )
                        async with writer.lock:
                            try:
                                await writer.conn.execute("BEGIN IMMEDIATE")
                                await writer._insert_objects(thread, namespace, objects)
                                await writer.conn.executemany(
                                    "INSERT INTO writes VALUES(?,?,?,?,?,?,?,?)",
                                    rows,
                                )
                                await writer.conn.commit()
                            except BaseException:
                                await writer.conn.rollback()
                                raise
                        counts["writes"] += len(rows)
                async with writer.conn.execute("PRAGMA integrity_check") as cursor:
                    if (await cursor.fetchone())[0] != "ok":
                        raise ValueError("目标数据库完整性校验失败")
                await writer.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        complete = True
        return {
            **counts,
            "source_bytes": source.stat().st_size,
            "destination_bytes": destination.stat().st_size,
        }
    finally:
        if not complete and destination.exists():
            current = destination.stat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                destination.unlink()
                for suffix in ("-wal", "-shm"):
                    Path(str(destination) + suffix).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="生成精简 checkpoint 副本，不覆盖源文件")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(
            json.dumps(asyncio.run(compact_copy(args.source, args.destination)), ensure_ascii=False)
        )
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + os.linesep)


if __name__ == "__main__":
    main()
