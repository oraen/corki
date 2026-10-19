"""The runtime discovers and reads knowledge through normal model tool calls."""

import asyncio
import json

from test_thread_settings_update import make_runtime

from corki.knowledge.extraction import batches, validate
from corki.knowledge.store import KnowledgeStore
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


def test_runtime_reads_knowledge_and_observes_live_disable(tmp_path):
    store = KnowledgeStore(tmp_path / "home")
    path = store.root / "reference.md"
    path.parent.mkdir(parents=True)
    text = "# Reading lamp\n\nDouble click cancels the timer."
    path.write_text(text)
    doc = store.sync(path, "Reading lamp", text)
    store.set_enabled(doc["id"], True)
    job = store.claim(doc["id"], "fixture")
    blocks = next(batches(text))
    rows = validate(
        {
            "segments": [
                {
                    "start": 1,
                    "end": len(blocks),
                    "title": "Timer",
                    "summary": "How to cancel the reading lamp timer",
                    "keywords": ["timer"],
                }
            ]
        },
        blocks,
        text,
    )
    store.finish(doc["id"], job, doc["hash"], rows)

    class Model:
        def __init__(self):
            self.calls = 0
            self.saw_original = False

        async def stream(self, request):
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            names = {t.name for t in request.tools}
            assert {"knowledge_search", "knowledge_read"} <= names
            if self.calls == 1:
                call = ToolCall("search", "knowledge_search", {"query": "timer"})
                yield ModelCompleted((ToolCallItem(call, turn, step),))
            elif self.calls == 2:
                result = next(i for i in reversed(request.items) if isinstance(i, ToolResultItem))
                found = json.loads(result.content)["results"][0]
                call = ToolCall("read", "knowledge_read", {"segment_id": found["id"]})
                yield ModelCompleted((ToolCallItem(call, turn, step),))
            else:
                result = next(i for i in reversed(request.items) if isinstance(i, ToolResultItem))
                self.saw_original = json.loads(result.content)["content"] == text
                yield ModelCompleted(
                    (AssistantMessageItem("Double click. [reference.md]", turn, step),)
                )

        async def aclose(self):
            pass

    async def scenario():
        model = Model()
        runtime = await make_runtime(tmp_path, model)
        try:
            events = [event async for event in runtime.stream("How do I cancel the timer?")]
            assert isinstance(events[-1], TurnCompleted)
            assert model.calls == 3 and model.saw_original
            store.set_enabled(doc["id"], False)
            tool = runtime._registry.get("knowledge_search")
            result = await tool.execute(ToolCall("after-disable", "knowledge_search", {}), None)
            assert all(d["title"] == "corki_docs" for d in json.loads(result.content)["results"])
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_runtime_answers_corki_question_from_builtin_manual(tmp_path):
    from corki.config import CorkiSettings
    from corki.knowledge.builtin import bundled_document, install_builtin_knowledge
    from corki.knowledge.extraction import extract
    from corki.models.types import ModelTextDelta

    class Extractor:
        async def stream(self, request):
            blocks = json.loads(request.items[0].content)["blocks"]
            yield ModelTextDelta(json.dumps({"segments": [{
                "start": i + 1, "end": i + 1, "title": "说明",
                "summary": "权限配置" if "default_permissions" in block["text"] else "使用说明",
                "keywords": (["default_permissions"]
                             if "default_permissions" in block["text"] else [])}
                for i, block in enumerate(blocks)]}))
            yield ModelCompleted(())

        async def aclose(self):
            pass

    home = tmp_path / "home"
    doc = install_builtin_knowledge(home)
    store = KnowledgeStore(home)
    settings = CorkiSettings(working_directory=tmp_path)
    job = store.claim(doc["id"], "fixture/model", prepare=True)
    asyncio.run(extract(Extractor(), settings, store, doc, job, bundled_document()))

    class Model:
        calls = 0
        read_source = False

        async def stream(self, request):
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.calls == 1:
                call = ToolCall("docs-search", "knowledge_search",
                                {"query": "default_permissions", "limit": 1})
                yield ModelCompleted((ToolCallItem(call, turn, step),))
            elif self.calls == 2:
                result = next(i for i in reversed(request.items) if isinstance(i, ToolResultItem))
                hits = json.loads(result.content)["results"]
                assert hits[0]["source"] == ".system/corki_docs.md"
                call = ToolCall("docs-read", "knowledge_read", {"segment_id": hits[0]["id"]})
                yield ModelCompleted((ToolCallItem(call, turn, step),))
            else:
                result = next(i for i in reversed(request.items) if isinstance(i, ToolResultItem))
                source = json.loads(result.content)
                self.read_source = "default_permissions" in source["content"]
                assert source["start_line"] >= 1
                yield ModelCompleted((AssistantMessageItem("可使用命名权限。", turn, step),))

        async def aclose(self):
            pass

    async def scenario():
        model = Model()
        runtime = await make_runtime(tmp_path, model)
        try:
            events = [
                event async for event in runtime.stream("Corki 的 default_permissions 怎么配置？")
            ]
            assert isinstance(events[-1], TurnCompleted)
            assert model.calls == 3 and model.read_source
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
