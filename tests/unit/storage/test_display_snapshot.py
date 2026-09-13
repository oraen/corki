"""Terminal state and conversation rows are captured at one SQLite read boundary."""

import asyncio
from contextlib import contextmanager

from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository


def test_display_snapshot_does_not_mix_concurrent_terminal_and_item_commit(tmp_path, monkeypatch):
    repository = SQLiteSessionRepository(tmp_path / "snapshot.db")
    thread, turn = new_thread_id(), new_turn_id()
    user = UserMessageItem("Request", turn)
    answer = AssistantMessageItem("Later answer", turn, new_step_id())

    async def seed():
        await repository.create_thread(thread, tmp_path)
        await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, user.content))
        await repository.append_items(thread, (user,))

    asyncio.run(seed())
    connect = repository._connect
    injected, errors = [], []

    def between_reads(statement):
        if (
            statement.startswith("SELECT id,status,error,")
            and " FROM turns " in statement
            and not injected
        ):
            injected.append(True)
            try:
                with connect() as writer:
                    writer.execute(
                        "UPDATE turns SET status='failed',error='New failure' WHERE id=?",
                        (str(turn),),
                    )
                    repository._append_items_in_connection(writer, thread, (answer,))
            except Exception as error:
                errors.append(error)

    @contextmanager
    def intercept():
        with connect() as connection:
            connection.set_trace_callback(between_reads)
            yield connection

    try:
        with monkeypatch.context() as patch:
            patch.setattr(repository, "_connect", intercept)
            snapshot = asyncio.run(repository.load_display_snapshot(thread))
        assert injected and not errors
        assert snapshot.items == (user,)
        assert snapshot.turns[0].status is TurnStatus.RUNNING
        assert snapshot.turns[0].error is None
        newer = asyncio.run(repository.load_display_snapshot(thread))
        assert newer.items == (user, answer)
        assert newer.turns[0].status is TurnStatus.FAILED
        assert newer.turns[0].error == "New failure"
    finally:
        asyncio.run(repository.close())
