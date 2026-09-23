import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.skills import SkillService
from corki.tools import ToolRegistry


def write_skill(root, name, description):
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBODY {name}", encoding="utf-8"
    )


@pytest.mark.parametrize("configured", [None, 1], ids=["window-budget", "all-omitted"])
def test_runtime_catalog_budget_does_not_disable_reading_or_repeat_warnings(
    tmp_path, monkeypatch, configured
):
    async def scenario():
        root = tmp_path / ".corki" / "skills"
        for i in range(8):
            write_skill(root, f"fixture{i}", "d" * 1000)
        renders, requests = [], []
        original = SkillService.catalog

        def catalog(service, cwd):
            result = original(service, cwd)
            renders.append(result)
            return result

        monkeypatch.setattr(SkillService, "catalog", catalog)

        class Change:
            spec = ToolSpec("change", "update skill metadata", {})

            async def execute(self, call, context):
                write_skill(root, "fixture0", "e" * 1000)
                return ToolResult(call.id, call.name, "metadata changed")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) <= 2:
                    name = "change" if len(requests) == 1 else "skill_read"
                    arguments = {} if name == "change" else {"name": "fixture7"}
                    items = (
                        ToolCallItem(ToolCall(new_tool_call_id(), name, arguments), turn, step),
                    )
                else:
                    items = (AssistantMessageItem("done", turn, step),)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Change())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                context_window_tokens=100_000,
                # This fixture measures skill warnings, not unknown-model admission.
                model_contexts=(ModelContextInfo("gpt-5", 100_000),),
                skills_max_context_tokens=configured,
            ),
            database_path=tmp_path / "sessions.db",
            home_path=tmp_path / ".corki",
            model=Model(),
            registry=registry,
        )
        try:
            events = [e async for e in runtime.stream("Inspect the skills")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            notices = [e for e in events if isinstance(e, WarningEvent)]
            assert len(notices) == 1
            assert "skills context budget" in notices[0].message
            assert all(render.metadata_cost <= (configured or 2000) for render in renders)
            assert renders[0].report.omitted_count == (
                renders[0].report.total_count if configured else 0
            )
            assert len(requests) == 3
            assert any(
                isinstance(item, ToolResultItem) and "BODY fixture7" in item.content
                for item in requests[-1].items
            )
            initial = requests[0].items
            assert requests[1].items[: len(initial)] == initial
            catalogs = [
                item
                for item in requests[-1].items
                if isinstance(item, ContextItem) and item.key == "extensions.skills.catalog"
            ]
            assert len(catalogs) == (1 if configured else 2)
            assert all("Current context:" not in item.content for item in catalogs)
            assert all("no longer apply" not in item.content for item in catalogs)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert notices[0].message not in repr(stored)
            following = [e async for e in runtime.stream("Continue")]
            assert isinstance(following[-1], TurnCompleted)
            assert not any(isinstance(e, WarningEvent) for e in following)  # unchanged section
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
