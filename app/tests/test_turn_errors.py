"""Old failures belong to their own turn, not the end of every snapshot."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from service import Service
from test_bridge import setup

from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import AssistantMessageItem, UserMessageItem
from corki.sessions.models import TurnRecord, TurnStatus


def test_failures_stay_with_original_turn_and_do_not_leak_into_other_pages(tmp_path):
    async def check():
        setup(tmp_path)

        async def send(event):
            pass

        service = Service(tmp_path, send)
        thread = ThreadId("error-history")
        try:
            await service.repository.create_thread(thread, tmp_path)
            for i in range(65):
                turn = TurnId(f"turn-{i}")
                await service.repository.save_turn(
                    TurnRecord(
                        turn,
                        thread,
                        TurnStatus.FAILED if i < 3 else TurnStatus.COMPLETED,
                        f"question {i}",
                        error="synthetic 400" if i < 3 else None,
                    )
                )
                await service.repository.append_items(
                    thread, (UserMessageItem(f"question {i}", turn),)
                )
                if i >= 3:
                    await service.repository.append_items(
                        thread, (AssistantMessageItem(f"answer {i}", turn, f"step-{i}"),)
                    )
            page = await service.read_thread({"threadId": str(thread)})
            assert not any(m["role"] == "error" for m in page["messages"])
            all_rows = page["messages"]
            while page["next"] is not None:
                page = await service.read_thread({"threadId": str(thread), "before": page["next"]})
                all_rows = page["messages"] + all_rows
            errors = [m for m in all_rows if m["role"] == "error"]
            assert len(errors) == 3
            for error in errors:
                index = all_rows.index(error)
                assert all_rows[index - 1]["turnId"] == error["turnId"]
                assert all_rows[index + 1]["turnId"] != error["turnId"]
        finally:
            await service.close()

    asyncio.run(check())
