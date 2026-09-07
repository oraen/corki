"""Actual Runtime requests exercise memory text, retrieval, and failure boundaries."""

import asyncio
import json

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import LocalMemoryBackend
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall


def test_summary_search_read_and_rejected_searches_reach_the_model(tmp_path):
    async def scenario():
        root = tmp_path / "memories"
        LocalMemoryBackend(root)
        original = "HEAD\r\nmy/project.name-v2\r\nTAIL"
        (root / "MEMORY.md").write_bytes(original.encode())
        (root / "memory_summary.md").write_text(
            "INDEXHEAD " + "padding " * 100 + " TAIL:MEMORY.md", encoding="utf-8"
        )
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("PRIVATE_EXTERNAL_CONTENT", encoding="utf-8")

        class Model:
            requests = []

            async def stream(self, request):
                self.requests.append(request)
                index = len(self.requests)
                turn, step = request.items[-1].turn_id, new_step_id()
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                if index == 1:
                    fragment = next(
                        i
                        for i in request.items
                        if isinstance(i, ContextItem) and i.key == "memory.instructions"
                    )
                    assert "INDEXHEAD" in fragment.content and "TAIL:MEMORY.md" in fragment.content
                    assert (
                        "tokens truncated" in fragment.content
                        and "padding " * 100 not in fragment.content
                    )
                    name, args = (
                        "memory_search",
                        {"queries": ["my project_name v2"], "normalized": True},
                    )
                elif index == 2:
                    assert not results[-1].is_error
                    match = json.loads(results[-1].content)["matches"][0]
                    assert match["path"] == "MEMORY.md" and match["match_line_number"] == 2
                    name, args = (
                        "memory_read",
                        {"path": match["path"], "line_offset": 2, "max_lines": 1},
                    )
                elif index == 3:
                    assert json.loads(results[-1].content)["content"] == "my/project.name-v2\r\n"
                    name, args = "memory_read", {"path": "MEMORY.md", "max_tokens": 3}
                elif index == 4:
                    read = json.loads(results[-1].content)
                    assert read["truncated"] and read["content"].startswith("HEAD\r\n")
                    assert "tokens truncated" in read["content"] and read["content"].endswith(
                        "\r\nTAIL"
                    )
                    name, args = "memory_search", {"queries": ["---"], "normalized": True}
                elif index == 5:
                    assert results[-1].is_error and "empty" in results[-1].content
                    root.rename(tmp_path / "preserved-memory")
                    root.symlink_to(outside, target_is_directory=True)
                    name, args = "memory_search", {"queries": ["PRIVATE"]}
                else:
                    assert index == 6
                    assert results[-1].is_error and "symbolic link" in results[-1].content
                    assert "PRIVATE_EXTERNAL_CONTENT" not in str(request.items)
                    yield ModelCompleted((AssistantMessageItem("retrieval complete", turn, step),))
                    return
                yield ModelCompleted(
                    (ToolCallItem(ToolCall(new_tool_call_id(), name, args), turn, step),)
                )

            async def aclose(self):
                pass

        model = Model()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_dedicated_tools=True,
                memories_summary_token_limit=12,
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=root,
            model=model,
        )
        try:
            assert isinstance(
                [e async for e in runtime.stream("recover the project command")][-1], TurnCompleted
            )
            assert len(model.requests) == 6
            stored = await runtime._repository.load_items(runtime.thread_id)
            observations = [i for i in stored if isinstance(i, ToolResultItem)]
            assert [i.is_error for i in observations] == [False, False, False, True, True]
            assert (tmp_path / "preserved-memory/MEMORY.md").read_bytes() == original.encode()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
