"""Exact snapshot recovery, legacy compatibility and bounded storage growth."""

import asyncio
import base64
import random
import sqlite3
from contextlib import closing

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from corki.core.checkpoint import checkpoint_serializer
from corki.core.checkpoint_lifecycle import setup_checkpoint
from corki.core.checkpoint_store import CompactSqliteSaver
from corki.protocol.ids import TurnId
from corki.protocol.items import UserMessageItem
from corki.protocol.tools import ToolSpec


def configuration(thread="thread", namespace=""):
    return {"configurable": {"thread_id": thread, "checkpoint_ns": namespace}}


def checkpoint(index=0):
    value = empty_checkpoint()
    value["id"] = f"checkpoint-{index:05}"
    value["channel_values"] = {
        "request_items": [UserMessageItem("中文内容" * 500, TurnId("turn"))],
        "request_tools": [ToolSpec("fixture", "description", {"type": "object"})],
        "dispatch_tools": [ToolSpec("fixture", "description", {"type": "object"})],
        "context_instructions": "fixed instruction" * 200,
        "turn_base_instructions": "fixed instruction" * 200,
        "status": "running",
        "custom_state": {"items": ["not a reference"]},
    }
    value["channel_versions"] = {"request_items": "0001"}
    value["versions_seen"] = {"call_model": {"request_items": "0001"}}
    return value


def canonical(value):
    serde = checkpoint_serializer()
    return serde.loads_typed(serde.dumps_typed(value))


def test_exact_ids_versions_pending_writes_namespaces_and_listing(tmp_path):
    async def check():
        async with CompactSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as saver:
            first, second = checkpoint(), checkpoint(1)
            config = await saver.aput(configuration(), first, {"source": "input", "step": 0}, {})
            writes = [
                ("request_items", first["channel_values"]["request_items"]),
                ("context_instructions", "prefix"),
                ("custom", {"scalar": "literal"}),
            ]
            await saver.aput_writes(config, writes, "task")
            # Ordinary task writes are idempotent: a retry cannot replace their result.
            await saver.aput_writes(config, [("request_items", [])], "task")
            saved = await saver.aget_tuple(config)
            assert saved.checkpoint == canonical(first)
            assert canonical([(c, v) for _, c, v in saved.pending_writes]) == canonical(writes)
            config2 = await saver.aput(config, second, {"source": "loop", "step": 1}, {})
            assert (await saver.aget_tuple(config2)).parent_config == config
            assert (await saver.aget_tuple(config)).checkpoint == canonical(first)
            iterator = saver.alist(configuration())
            await anext(iterator)
            await iterator.aclose()
            await asyncio.wait_for(saver.aget_tuple(config2), 1)
            rows = [r async for r in saver.alist(configuration())]
            assert [r.checkpoint["id"] for r in rows] == [second["id"], first["id"]]
            assert len([r async for r in saver.alist(configuration(), before=config2)]) == 1
            assert len([r async for r in saver.alist(configuration(), filter={"step": 1})]) == 1
            await saver.aput(configuration(namespace="child"), second, {}, {})
            assert (
                await saver.aget_tuple(configuration(namespace="child"))
            ).checkpoint == canonical(second)
            assert (await saver.aget_tuple(configuration("absent"))) is None

    asyncio.run(check())


def test_legacy_rows_and_new_rows_coexist_across_restarts(tmp_path):
    async def check():
        path = str(tmp_path / "checkpoints.db")
        original = checkpoint()
        async with AsyncSqliteSaver.from_conn_string(path) as old:
            old.serde = checkpoint_serializer()
            config = await old.aput(configuration(), original, {"step": 0}, {})
            await old.aput_writes(
                config, [("request_items", original["channel_values"]["request_items"])], "task"
            )
        async with CompactSqliteSaver.from_conn_string(path) as new:
            saved = await new.aget_tuple(config)
            assert saved.checkpoint == canonical(original)
            assert saved.pending_writes[0][2] == canonical(
                original["channel_values"]["request_items"]
            )
            second = checkpoint(1)
            config2 = await new.aput(config, second, {"step": 1}, {})
            await new.aput_writes(config2, [("request_tools", [])], "task2")
        async with CompactSqliteSaver.from_conn_string(path) as restarted:
            assert (await restarted.aget_tuple(configuration())).checkpoint == canonical(second)
            assert len([row async for row in restarted.alist(None)]) == 2
            assert (await restarted.aget_tuple(config2)).pending_writes[0][2] == []

    asyncio.run(check())


def test_storage_growth_is_references_not_full_prefix_copies(tmp_path):
    async def check():
        sizes = {}
        random_source = random.Random(0)
        entries = [
            UserMessageItem(
                base64.b64encode(random_source.randbytes(8000)).decode(), TurnId("turn")
            )
            for _ in range(40)
        ]
        for cls, name in [(AsyncSqliteSaver, "legacy"), (CompactSqliteSaver, "compact")]:
            path = tmp_path / (name + ".db")
            async with cls.from_conn_string(str(path)) as saver:
                if name == "legacy":
                    saver.serde = checkpoint_serializer()
                config = configuration()
                for index in range(40):
                    value = checkpoint(index)
                    value["channel_values"]["request_items"] = entries[: index + 1]
                    config = await saver.aput(config, value, {"step": index}, {})
                    await saver.aput_writes(
                        config,
                        [("request_items", value["channel_values"]["request_items"])],
                        "task",
                    )
                assert (
                    len(
                        (await saver.aget_tuple(config)).checkpoint["channel_values"][
                            "request_items"
                        ]
                    )
                    == 40
                )
            sizes[name] = path.stat().st_size
            if name == "compact":
                with closing(sqlite3.connect(path)) as db:
                    assert db.execute("SELECT COUNT(*) FROM checkpoint_objects").fetchone()[0] == 42

        print("checkpoint storage bytes", sizes)
        assert sizes["compact"] < sizes["legacy"] * 0.1, sizes

    asyncio.run(check())


def test_corrupt_or_missing_objects_fail_closed_and_deletion_is_scoped(tmp_path):
    async def check():
        path = tmp_path / "checkpoints.db"
        async with CompactSqliteSaver.from_conn_string(str(path)) as saver:
            config = await saver.aput(configuration(), checkpoint(), {}, {})
            await saver.aput(configuration("other"), checkpoint(), {}, {})
            with closing(sqlite3.connect(path)) as db, db:
                db.execute(
                    "UPDATE checkpoint_objects SET raw_size=raw_size+1 WHERE thread_id='thread'"
                )
            with pytest.raises(ValueError, match="Corrupt"):
                await saver.aget_tuple(config)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("DELETE FROM checkpoint_objects WHERE thread_id='thread'")
            with pytest.raises(ValueError, match="Missing"):
                await saver.aget_tuple(config)
            await saver.adelete_thread("thread")
            assert await saver.aget_tuple(config) is None
            assert await saver.aget_tuple(configuration("other")) is not None
            with closing(sqlite3.connect(path)) as db:
                assert db.execute(
                    "SELECT DISTINCT thread_id FROM checkpoint_objects"
                ).fetchall() == [("other",)]

    asyncio.run(check())


def test_object_insert_and_snapshot_are_one_transaction(tmp_path):
    async def check():
        path = tmp_path / "checkpoints.db"
        async with CompactSqliteSaver.from_conn_string(str(path)) as saver:
            await saver.setup()
            with closing(sqlite3.connect(path)) as db, db:
                db.execute(
                    "CREATE TRIGGER reject_checkpoint BEFORE INSERT ON checkpoints "
                    "BEGIN SELECT RAISE(ABORT,'fixture failure'); END"
                )
            with pytest.raises(sqlite3.IntegrityError):
                await saver.aput(configuration(), checkpoint(), {}, {})
            with closing(sqlite3.connect(path)) as db:
                assert db.execute("SELECT COUNT(*) FROM checkpoint_objects").fetchone()[0] == 0
            assert await saver.aget_tuple(configuration()) is None

    asyncio.run(check())


def test_cancelled_transaction_rolls_back_and_cached_payloads_are_reinserted(tmp_path):
    async def check():
        path = tmp_path / "cancelled.db"
        async with CompactSqliteSaver.from_conn_string(str(path)) as saver:
            original = saver._insert_objects
            entered = asyncio.Event()

            async def hold(*args):
                await original(*args)
                entered.set()
                await asyncio.Event().wait()

            saver._insert_objects = hold
            value = checkpoint()
            task = asyncio.create_task(saver.aput(configuration(), value, {}, {}))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with closing(sqlite3.connect(path)) as db:
                assert db.execute("SELECT COUNT(*) FROM checkpoint_objects").fetchone()[0] == 0
                assert db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
            saver._insert_objects = original
            saved = await saver.aput(configuration(), value, {}, {})
            assert (await saver.aget_tuple(saved)).checkpoint == canonical(value)

    asyncio.run(check())


def test_hashes_actual_content_and_does_not_alias_restored_mutable_fields(tmp_path):
    async def check():
        async with CompactSqliteSaver.from_conn_string(str(tmp_path / "objects.db")) as saver:
            item = {"id": "same-id", "content": ["first"]}
            first = checkpoint()
            first["channel_values"].update(request_items=[item], last_model_items=[item])
            config = await saver.aput(configuration(), first, {}, {})
            item["content"][0] = "changed"
            second = checkpoint(1)
            second["channel_values"]["request_items"] = [item]
            await saver.aput(config, second, {}, {})
            restored = (await saver.aget_tuple(config)).checkpoint["channel_values"]
            assert restored["request_items"][0]["content"] == ["first"]
            restored["request_items"][0]["content"].append("local mutation")
            assert restored["last_model_items"][0]["content"] == ["first"]
            assert (await saver.aget_tuple(configuration())).checkpoint["channel_values"][
                "request_items"
            ][0]["content"] == ["changed"]

    asyncio.run(check())


def test_multiple_connections_preserve_independent_turns(tmp_path):
    async def check():
        path = str(tmp_path / "concurrent.db")
        async with (
            CompactSqliteSaver.from_conn_string(path) as one,
            CompactSqliteSaver.from_conn_string(path) as two,
        ):
            await asyncio.gather(setup_checkpoint(one), setup_checkpoint(two))

            async def write(saver, name):
                config = configuration(name)
                for index in range(8):
                    config = await saver.aput(config, checkpoint(index), {"step": index}, {})
                return config

            first, second = await asyncio.gather(write(one, "one"), write(two, "two"))
            assert len([r async for r in one.alist(configuration("one"))]) == 8
            assert len([r async for r in two.alist(configuration("two"))]) == 8
            assert (await two.aget_tuple(first)).config == first
            assert (await one.aget_tuple(second)).config == second

    asyncio.run(check())


def test_media_opaque_reasoning_and_distinct_ui_output_are_not_discarded(tmp_path):
    from corki.protocol.ids import ModelStepId, ToolCallId
    from corki.protocol.items import ReasoningItem, ToolResultItem
    from corki.protocol.tools import EncryptedContent, ImageAttachment, TextContent

    async def check():
        image = ImageAttachment("data:image/png;base64,AA==")
        result = ToolResultItem(
            ToolCallId("call"),
            "fixture",
            "raw output\n" * 10000,
            TurnId("turn"),
            display_content="different display output\n" * 6000,
            attachments=(image,),
            content_items=(
                TextContent("before"),
                image,
                EncryptedContent("opaque"),
                TextContent("after"),
            ),
            exit_code=7,
        )
        reasoning = ReasoningItem(
            "",
            TurnId("turn"),
            ModelStepId("step"),
            encrypted_content="opaque-provider-payload",
            summary="visible summary",
        )
        value = checkpoint()
        value["channel_values"]["request_items"] = [result, reasoning]
        value["channel_values"]["streamed_tool_results"] = [result]
        async with CompactSqliteSaver.from_conn_string(str(tmp_path / "media.db")) as saver:
            config = await saver.aput(configuration(), value, {}, {})
            assert (await saver.aget_tuple(config)).checkpoint == canonical(value)

    asyncio.run(check())


def test_restore_holds_consistent_snapshot_while_another_connection_deletes(tmp_path):
    async def check():
        path = str(tmp_path / "read-delete.db")
        async with (
            CompactSqliteSaver.from_conn_string(path) as reader,
            CompactSqliteSaver.from_conn_string(path) as deleter,
        ):
            config = await reader.aput(configuration(), checkpoint(), {}, {})
            await setup_checkpoint(deleter)
            entered, release = asyncio.Event(), asyncio.Event()
            original_restore = reader._restore

            async def hold(stored):
                entered.set()
                await release.wait()
                return await original_restore(stored)

            reader._restore = hold
            pending = asyncio.create_task(reader.aget_tuple(config))
            await entered.wait()
            await asyncio.wait_for(deleter.adelete_thread("thread"), 2)
            release.set()
            assert (await pending).checkpoint["id"] == config["configurable"]["checkpoint_id"]
            assert await reader.aget_tuple(config) is None

    asyncio.run(check())


def test_cancelling_read_during_begin_does_not_leave_transaction_open(tmp_path):
    async def check():
        async with CompactSqliteSaver.from_conn_string(str(tmp_path / "cancel-read.db")) as saver:
            config = await saver.aput(configuration(), checkpoint(), {}, {})
            original = saver.conn.execute
            entered = asyncio.Event()

            async def hold(sql, *args):
                result = await original(sql, *args)
                if sql == "BEGIN":
                    entered.set()
                    await asyncio.Event().wait()
                return result

            saver.conn.execute = hold
            pending = asyncio.create_task(saver.aget_tuple(config))
            await entered.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            saver.conn.execute = original
            assert not saver.conn.in_transaction
            assert await saver.aget_tuple(config) is not None

    asyncio.run(check())
