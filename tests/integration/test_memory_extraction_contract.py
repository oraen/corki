"""The real startup pipeline preserves stage-one output and isolates invalid JSON."""

import asyncio
import json
import sqlite3

import pytest
from memory_evidence import inspect_worker_evidence

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize(
    "case",
    ["missing_slug", "padded", "whitespace", "unknown", "duplicate", "missing_raw", "invalid_slug"],
)
def test_stage_one_parser_and_redaction_survive_runtime_publication(tmp_path, case):
    async def scenario():
        database, root = tmp_path / "sessions.db", tmp_path / "memories"
        sessions = SQLiteSessionRepository(database)
        source, turn = new_thread_id(), new_turn_id()
        await sessions.create_thread(source, tmp_path)
        secret = "AKIAABCDEFGHIJKLMNOP"
        original = UserMessageItem(f"Use pytest. token=abcdefgh {secret}", turn)
        await sessions.append_items(source, (original,))
        invalid = case in {"unknown", "duplicate", "missing_raw", "invalid_slug"}
        values = {
            "raw_memory": f"  fact {secret}\n",
            "rollout_summary": "  route\n",
            "rollout_slug": "  slug  ",
        }
        if case == "missing_slug":
            values.pop("rollout_slug")
        elif case == "whitespace":
            values = {"raw_memory": " \n", "rollout_summary": "\t", "rollout_slug": ""}
        elif case == "unknown":
            values["unknown"] = "must reject"
        elif case == "missing_raw":
            values.pop("raw_memory")
        elif case == "invalid_slug":
            values["rollout_slug"] = 1
        response = json.dumps(values)
        if case == "duplicate":
            response = '{"raw_memory":"first",' + response[1:]

        class MemoryModel:
            requests = []

            async def stream(self, request):
                self.requests.append(request)
                assert bool(request.tools) == (request.output_schema is None)
                if len(self.requests) == 1:
                    assert set(request.output_schema["required"]) == {
                        "raw_memory",
                        "rollout_summary",
                        "rollout_slug",
                    }
                    assert secret not in request.items[0].content
                    assert "abcdefgh" not in request.items[0].content
                    assert "[REDACTED_SECRET]" in request.items[0].content
                    text = response
                else:
                    data = json.dumps(inspect_worker_evidence(request))
                    assert secret not in data
                    text = json.dumps(
                        {
                            "memory": f"Saved fact {secret}",
                            "memory_summary": "Fact route",
                            "skills": [],
                        }
                    )
                yield ModelCompleted(
                    (AssistantMessageItem(text, request.items[-1].turn_id, new_step_id()),)
                )

        class MainModel:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        model = MemoryModel()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_min_thread_idle_hours=0,
            ),
            database_path=database,
            model=MainModel(),
            memory_model=model,
            memory_root=root,
            home_path=tmp_path / "home",
        )
        try:
            events = [event async for event in runtime.stream("initialize")]
            assert isinstance(events[-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.failed == int(invalid), runtime._memory_service.warnings
            assert report.extracted == int(not invalid) and report.empty == 0
            assert report.consolidated and len(model.requests) == 2
            with sqlite3.connect(database) as connection:
                row = connection.execute(
                    "SELECT raw_memory,rollout_summary,rollout_slug "
                    "FROM memory_stage1_outputs WHERE thread_id=?",
                    (source,),
                ).fetchone()
            if invalid:
                assert row is None
            else:
                expected_raw = values["raw_memory"].replace(secret, "[REDACTED_SECRET]")
                assert row == (expected_raw, values["rollout_summary"], values.get("rollout_slug"))
            assert secret not in (root / "MEMORY.md").read_text()
            assert "[REDACTED_SECRET]" in (root / "MEMORY.md").read_text()
            assert await sessions.load_items(source) == (original,)
        finally:
            await runtime.aclose()
            await sessions.close()

    asyncio.run(scenario())
