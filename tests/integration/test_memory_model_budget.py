import asyncio
import json

import pytest
from memory_evidence import inspect_worker_evidence

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory.pipeline import _render_transcript
from corki.models import ModelCompleted
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize(
    "mode,budget",
    [
        ("main", 4480),
        ("separate", 2240),
        ("max", 2660),
        ("unknown", 180880),
        ("no_window", 150000),
        ("cap", 1000),
        ("version", 2240),
        ("namespace", 2240),
        ("direct_priority", 1680),
    ],
)
@pytest.mark.parametrize("dedicated_consolidation", [False, True])
def test_runtime_uses_selected_extraction_window_before_sampling_and_publication(
    tmp_path, mode, budget, dedicated_consolidation
):
    async def scenario():
        database, root = tmp_path / "sessions.db", tmp_path / "memories"
        repo = SQLiteSessionRepository(database)
        source = new_thread_id()
        await repo.create_thread(source, tmp_path)
        repeat = 150000 if mode in {"unknown", "no_window"} else 15000
        original = UserMessageItem("START " + "abc你好" * repeat + " END", new_turn_id())
        await repo.append_items(source, (original,))
        config = tmp_path / "config.toml"
        selected_model = {
            "main": "main",
            "version": "extract-v2-preview",
            "namespace": "provider_2/extract-v2-preview",
            "direct_priority": "vendor/extract-v2-preview",
        }.get(mode, "extract")
        config.write_text(
            '[agent]\nmodel="main"\ncontext_window_tokens=8000\neffective_context_window_percent=80\n'
            "[skills]\nenabled=false\n[memories]\nenabled=true\nmin_thread_idle_hours=0\n"
            + f'extraction_model="{selected_model}"\n'
            + ('consolidation_model="merge"\n' if dedicated_consolidation else "")
            + ("extraction_token_limit=1000\n" if mode == "cap" else "")
            + ("[models]\ncontext_window_override=100000\n" if mode == "max" else "")
            + (
                "[models.catalog.extract]\ncontext_window=4000\neffective_context_window_percent=80\n"
                if mode in {"separate", "cap"}
                else ""
            )
            + ("[models.catalog.extract]\nmax_context_window=4000\n" if mode == "max" else "")
            + ("[models.catalog.extract]\n" if mode == "no_window" else "")
            + (
                "[models.catalog.extract]\ncontext_window=10000\n"
                "[models.catalog.extract-v2]\ncontext_window=4000\neffective_context_window_percent=80\n"
                if mode in {"version", "namespace", "direct_priority"}
                else ""
            )
            + (
                "[models.catalog.vendor]\ncontext_window=3000\neffective_context_window_percent=80\n"
                if mode == "direct_priority"
                else ""
            )
            + ("[models.catalog.merge]\ncontext_window=64000\n" if dedicated_consolidation else "")
        )
        settings = CorkiSettings.for_directory(tmp_path, config_file=config)

        class MemoryModel:
            requests = []

            async def stream(self, request):
                self.requests.append(request)
                if len(self.requests) == 1:
                    assert request.model == selected_model
                    assert not request.tools
                    transcript = (
                        request.items[0]
                        .content.split("<historical_transcript>\n", 1)[1]
                        .split("\n</historical_transcript>", 1)[0]
                    )
                    raw = _render_transcript((original,))
                    if len(raw.encode()) > budget * 4:
                        assert "tokens truncated" in transcript
                        left, rest = transcript.split("…", 1)
                        marker, right = rest.split("…", 1)
                        assert (
                            marker
                            == f"{(len(raw.encode()) - budget * 4 + 3) // 4} tokens truncated"
                        )
                        assert (
                            len(left.encode()) <= budget * 2 and len(right.encode()) <= budget * 2
                        )
                        assert raw.startswith(left) and raw.endswith(right) and left and right
                    else:
                        assert transcript == raw
                    value = {"raw_memory": "preserved useful fact", "rollout_summary": "fact route"}
                else:
                    assert (
                        "preserved useful fact" in inspect_worker_evidence(request)["raw_memories"]
                    )
                    value = {
                        "memory": "preserved useful fact",
                        "memory_summary": "fact route",
                        "skills": [],
                    }
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps(value), request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

        class MainModel:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        memory_model = MemoryModel()
        runtime = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            model=MainModel(),
            memory_model=memory_model,
            memory_root=root,
            home_path=tmp_path / "home",
        )
        try:
            assert isinstance(
                [event async for event in runtime.stream("initialize")][-1], TurnCompleted
            )
            report = await runtime._memory_service.wait()
            if not dedicated_consolidation and mode != "max":
                # The configured 8K main model cannot fit the consolidation
                # instructions/tools. Do not silently switch to a vendor model.
                assert report.extracted == 1 and not report.consolidated
                assert len(memory_model.requests) == 1
                assert any(
                    "prepared context exceeds model window" in warning
                    for warning in runtime._memory_service.warnings
                )
                assert not (root / "MEMORY.md").exists()
                assert await repo.load_items(source) == (original,)
                assert isinstance(
                    [event async for event in runtime.stream("still usable")][-1], TurnCompleted
                )
                return
            assert report.extracted == 1 and report.consolidated and not report.failed, (
                runtime._memory_service.warnings
            )
            assert len(memory_model.requests) == 2
            assert "preserved useful fact" in (root / "MEMORY.md").read_text()
            assert await repo.load_items(source) == (original,)
        finally:
            await runtime.aclose()
            await repo.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("requested", ["main", "main-preview", "provider/main-preview"])
def test_static_main_catalog_drives_actual_compaction_not_only_memory_math(tmp_path, requested):
    async def scenario():
        config = tmp_path / "config.toml"
        config.write_text(
            f'[agent]\nmodel="{requested}"\ncontext_window_tokens=100000\n'
            "[skills]\nenabled=false\n[models]\ncontext_window_override=50000\n"
            "[models.catalog.main]\ncontext_window=9000\nmax_context_window=8000\n"
            "effective_context_window_percent=80\n"
        )
        settings = CorkiSettings.for_directory(tmp_path, config_file=config)
        requests = []

        class Model:
            async def stream(self, request):
                assert request.model == requested
                requests.append(request)
                if len(requests) == 1:
                    text = "x" * 40000
                elif len(requests) == 2:
                    assert not request.tools
                    text = "compact summary"
                else:
                    text = "done"
                yield ModelCompleted(
                    (AssistantMessageItem(text, request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=Model(),
            home_path=tmp_path / "home",
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            events = [e async for e in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert sum(isinstance(e, ContextCompacted) for e in events) == 1
            assert len(requests) == 3 and events[-1].final_answer == "done"
            assert runtime._graph._window_manager._context_window_tokens == 6400
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
