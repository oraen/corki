"""Memory startup must never query official account services."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


class MainModel:
    async def stream(self, request):
        yield ModelCompleted(
            (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


class MemoryModel(MainModel):
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        value = (
            {"raw_memory": "Use pytest.", "rollout_summary": "pytest", "rollout_slug": "pytest"}
            if request.output_schema is not None
            else {"memory": "Use pytest.", "memory_summary": "pytest", "skills": []}
        )
        yield ModelCompleted(
            (AssistantMessageItem(json.dumps(value), request.items[-1].turn_id, new_step_id()),)
        )


async def seed_source(database, directory):
    sessions = SQLiteSessionRepository(database)
    thread, turn = new_thread_id(), new_turn_id()
    await sessions.create_thread(thread, directory)
    await sessions.save_turn(TurnRecord(turn, thread, TurnStatus.COMPLETED, "remember pytest"))
    await sessions.append_items(thread, (UserMessageItem("Use pytest.", turn),))
    await sessions.close()
    return thread


async def make_runtime(tmp_path, *, memory_model=None, runtime_options=None, **settings):
    values = dict(
        working_directory=tmp_path,
        api_base="https://inference.example.test/v1",
        api_key="fixture-backend-credential",
        memories_enabled=True,
        memories_min_thread_idle_hours=0,
        memories_generate=False,
        memories_use=False,
        skills_enabled=False,
    )
    values.update(settings)
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(**values),
        database_path=tmp_path / "sessions.db",
        home_path=tmp_path / "home",
        memory_root=tmp_path / "memories",
        model=MainModel(),
        memory_model=memory_model or MemoryModel(),
        registry=ToolRegistry(),
        **(runtime_options or {}),
    )


@pytest.mark.parametrize("provider", ["openai", "independent"])
@pytest.mark.parametrize("prune_fails", [False, True])
def test_memory_generation_never_queries_account(tmp_path, monkeypatch, provider, prune_fails):
    async def scenario():
        await seed_source(tmp_path / "sessions.db", tmp_path)
        requests = []

        async def reject_request(*args, **kwargs):
            requests.append((args, kwargs))
            raise AssertionError("Unexpected account HTTP request")

        monkeypatch.setattr(httpx.AsyncClient, "send", reject_request)
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("Memory must not allocate an account client")
            ),
        )
        memory_model = MemoryModel()
        runtime = await make_runtime(tmp_path, memory_model=memory_model, provider_name=provider)
        if prune_fails:

            async def broken_prune(**kwargs):
                raise RuntimeError("fixture maintenance failure")

            monkeypatch.setattr(runtime._memory_repository, "prune_stage_one_outputs", broken_prune)
        try:
            events = [event async for event in runtime.stream("remember")]
            assert isinstance(events[-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.extracted == 1 and report.consolidated
            assert len(memory_model.requests) == 2
            assert requests == []
            assert not hasattr(runtime._memory_service, "_quota")
            assert not any("quota" in warning for warning in runtime._memory_service.warnings)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_legacy_quota_threshold_cannot_enable_account_query(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[memories]\nmin_rate_limit_remaining_percent=100\n")
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert not hasattr(settings, "memories_min_rate_limit_remaining_percent")
