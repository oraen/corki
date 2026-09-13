"""Compatible Responses summaries survive storage and cold CLI history replay."""

import asyncio
import json
from dataclasses import replace
from io import StringIO

import httpx
import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import (
    ModelCompleted,
    ModelItemCompleted,
    ModelReasoningDelta,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.models.response_content import ResponseContent
from corki.protocol.items import ReasoningItem, new_step_id


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("anonymous", [False, True])
@pytest.mark.parametrize("raw_first", [False, True])
def test_completed_summary_is_visible_without_cold_reload(tmp_path, streamed, anonymous, raw_first):
    async def scenario():
        class Model:
            async def stream(self, request):
                item = ReasoningItem(
                    "opaque",
                    request.items[-1].turn_id,
                    new_step_id(),
                    summary="**Review** complete detail",
                )
                if raw_first:
                    yield ModelReasoningDelta("RAW_ONLY_TEXT", item_id=item.id, channel="raw")
                if streamed:
                    yield ModelReasoningDelta("**Review** ", item_id=None if anonymous else item.id)
                yield ModelItemCompleted(item)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        settings = CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False)
        runtime = LangGraphRuntime.create(
            settings=settings,
            model=Model(),
            database_path=tmp_path / "sessions.db",
            home_path=tmp_path,
        )
        ui = TerminalUI(settings, tmp_path / "history", console=Console(file=StringIO()))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
        try:
            await app._consume_events(runtime.stream("Review"))
            detail = ui._transcript.render(80, include_reasoning=True)
            assert detail.count("complete detail") == 1
            assert detail.count("Review") == 1
            assert "Review complete detail" in detail
            assert "opaque" not in detail
            assert "RAW_ONLY_TEXT" not in detail
            assert not ui._reasoning_active
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_reasoning_section_identity_reaches_live_status(tmp_path):
    async def scenario():
        class Model:
            async def stream(self, request):
                content = ResponseContent(request.items[-1].turn_id, new_step_id(), "fixture")
                yield content.delta(
                    {
                        "type": "response.reasoning_text.delta",
                        "item_id": "r1",
                        "delta": "**RAW_HEADER** RAW_BODY",
                    },
                    reasoning=True,
                )
                for item_id, index, delta in (
                    ("r1", 0, "**First** body one"),
                    ("r1", 1, ""),
                    ("r1", 1, "**Sec"),
                    ("r1", 1, "ond** body two"),
                    ("r2", 0, "**Third** body three"),
                ):
                    yield content.delta(
                        {
                            "type": "response.reasoning_summary_text.delta",
                            "item_id": item_id,
                            "summary_index": index,
                            "delta": delta,
                        },
                        reasoning=True,
                    )
                yield ModelCompleted(tuple(content.finish_pending()))

            async def aclose(self):
                pass

        class UI(TerminalUI):
            def append_reasoning_delta(self, delta):
                super().append_reasoning_delta(delta)
                headers.append(self._reasoning_header)

        headers = []
        settings = CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False)
        runtime = LangGraphRuntime.create(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=Model(),
            home_path=tmp_path,
        )
        ui = UI(settings, tmp_path / "input-history", console=Console(file=StringIO()))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
        try:
            await app._consume_events(runtime.stream("Review sections"))
            assert headers == ["First", None, None, "Second", "Third"]
            assert not ui._reasoning_active
            detailed = ui._transcript.render(80, include_reasoning=True)
            assert "RAW_BODY" not in detailed
            for text in ("body one", "body two", "body three"):
                assert detailed.count(text) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("source", ["completed", "summary_delta", "raw_delta"])
@pytest.mark.parametrize("legacy", [False, True])
def test_provider_summary_survives_cold_display_history(tmp_path, monkeypatch, source, legacy):
    if legacy:
        complete = ResponseContent.complete

        def old_complete(self, *args, **kwargs):
            item = complete(self, *args, **kwargs)
            return replace(item, summary=None) if isinstance(item, ReasoningItem) else item

        monkeypatch.setattr(ResponseContent, "complete", old_complete)

    async def scenario():
        text = "**Review** retained provider detail"
        if source == "completed":
            packets = [
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "reasoning",
                        "id": "r1",
                        "encrypted_content": "OPAQUE_SECRET",
                        "summary": [{"type": "summary_text", "text": text}],
                    },
                }
            ]
        else:
            packets = [
                {
                    "type": "response.reasoning_summary_text.delta"
                    if source == "summary_delta"
                    else "response.reasoning_text.delta",
                    "item_id": "r1",
                    "delta": text,
                }
            ]
        packets.append({"type": "response.completed", "response": {"id": "response1"}})
        body = "".join(f"data: {json.dumps(packet)}\n\n" for packet in packets)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body))
        )
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
        )
        settings = CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False)
        runtime = LangGraphRuntime.create(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=model,
            home_path=tmp_path,
        )
        thread = runtime.thread_id
        try:
            _ = [event async for event in runtime.stream("Review")]
        finally:
            await runtime.aclose()
            await client.aclose()
        # Fresh repository/Runtime, not the original in-memory display transcript.
        cold = LangGraphRuntime.create(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=model,
            thread_id=thread,
            home_path=tmp_path,
        )
        try:
            history = await cold.load_display_history()
            reasoning = [item for item in history if isinstance(item, ReasoningItem)]
            assert len(reasoning) == 1
            assert reasoning[0].summary == (None if legacy or source == "raw_delta" else text)
            output = StringIO()
            ui = TerminalUI(settings, tmp_path / "input-history", console=Console(file=output))
            ui.replay_history(history)
            assert "retained provider detail" not in output.getvalue()
            details = ui._transcript.render(80, include_reasoning=True)
            recoverable = source == "completed" or (source == "summary_delta" and not legacy)
            assert ("retained provider detail" in details) == recoverable
            assert "OPAQUE_SECRET" not in details
            assert await cold.load_display_history() == history
        finally:
            await cold.aclose()

    asyncio.run(scenario())
