import asyncio
import sqlite3

from corki.protocol.ids import new_thread_id, new_turn_id
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository


def test_legacy_turns_gain_normal_operation_and_compact_identity_survives_updates(tmp_path):
    database = tmp_path / "legacy.db"
    thread, old, compact = new_thread_id(), new_turn_id(), new_turn_id()
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE turns (
            id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, status TEXT NOT NULL,
            user_input TEXT NOT NULL, final_answer TEXT, error TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        connection.execute(
            "INSERT INTO turns(id, thread_id, status, user_input) "
            "VALUES (?, ?, 'running', 'legacy')",
            (str(old), str(thread)),
        )

    async def scenario():
        repository = SQLiteSessionRepository(database)
        try:
            await repository.create_thread(thread, tmp_path)
            assert (await repository.latest_running_turn(thread)).operation == "normal"
            await repository.save_turn(TurnRecord(old, thread, TurnStatus.COMPLETED, "legacy"))
            await repository.save_turn(
                TurnRecord(compact, thread, TurnStatus.RUNNING, "", operation="compact")
            )
            assert (await repository.latest_running_turn(thread)).operation == "compact"
            await repository.save_turn(TurnRecord(compact, thread, TurnStatus.COMPLETED, ""))
            with sqlite3.connect(database) as connection:
                assert connection.execute(
                    "SELECT operation FROM turns WHERE id=?", (str(compact),)
                ).fetchone() == ("compact",)
        finally:
            await repository.close()

    asyncio.run(scenario())
