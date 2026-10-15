"""Display reads retain admitted modes independently of current thread defaults."""

import asyncio
import json
import sqlite3

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.protocol.collaboration import CollaborationMode
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.sessions import TurnRecord, TurnStatus


@pytest.mark.parametrize("read_path", ["snapshot", "pages"])
def test_cold_display_retains_each_turn_mode_and_legacy_default(tmp_path, read_path):
    async def scenario():
        model = Model()
        runtime = await make_runtime(tmp_path, model)
        expected = []
        try:
            for mode in ("plan", "default", "plan"):
                await runtime.update_thread_settings(
                    collaboration_mode=CollaborationMode(mode, "large")
                )
                events = [event async for event in runtime.stream(f"Request in {mode}")]
                assert isinstance(events[-1], TurnCompleted)
                turn_id = events[-1].turn_id
                with sqlite3.connect(tmp_path / "sessions.db") as connection:
                    payload = connection.execute(
                        "SELECT model_settings_json FROM turns WHERE id=?", (str(turn_id),)
                    ).fetchone()[0]
                assert json.loads(payload)["collaboration_mode"] == mode
                expected.append((turn_id, mode))

            legacy = TurnRecord(new_turn_id(), runtime.thread_id, TurnStatus.COMPLETED, "legacy")
            await runtime._repository.save_turn(legacy)
            expected.append((legacy.id, "default"))
            await runtime.update_thread_settings(
                collaboration_mode=CollaborationMode("default", "large")
            )
            original_items = await runtime.load_display_items_page()
            thread = runtime.thread_id
        finally:
            await runtime.aclose()

        cold_model = Model()
        cold = await make_runtime(
            tmp_path,
            cold_model,
            configured=settings(tmp_path, collaboration_mode="default"),
            thread=thread,
        )
        try:
            if read_path == "snapshot":
                turns = (await cold.load_display_snapshot()).turns
            else:
                turns, cursor = (), None
                while True:
                    page = await cold.load_display_turns_page(cursor=cursor, limit=1)
                    turns = page.turns + turns
                    cursor = page.next_cursor
                    if cursor is None:
                        break
            assert cold_model.requests == []
            assert await cold.load_display_items_page() == original_items
            assert [(turn.id, getattr(turn, "collaboration_mode", None)) for turn in turns] == (
                expected
            )
        finally:
            await cold.aclose()

    asyncio.run(scenario())
