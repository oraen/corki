"""Audio normalization belongs to the Runtime history boundary, not only adapters."""

import asyncio

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.media.audio import PROCESSING_ERROR, UNSUPPORTED_INPUT
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import (
    AudioAttachment,
    CodeModeOutput,
    TextContent,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


@pytest.mark.parametrize("supported", [False, True])
def test_restored_user_and_tool_audio_are_projected_without_rewriting_history(tmp_path, supported):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        database = tmp_path / "legacy.db"
        repository = SQLiteSessionRepository(database)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                supports_audio_input=supported,
            ),
            database_path=database,
            repository=repository,
            model=Model(),
        )
        try:
            await runtime._ensure_ready()
            turn = new_turn_id()
            parts = (
                TextContent("before"),
                AudioAttachment("data:audio/x-m4a;base64,YXVkaW8="),
                AudioAttachment("https://example.test/no-network.wav"),
                TextContent("after"),
            )
            user = UserMessageItem("unchanged words", turn, content_items=parts)
            call = ToolCall(new_tool_call_id(), "old_tool", {})
            result = ToolResultItem(
                call.id, call.name, "old output", turn, is_error=True, content_items=parts
            )
            originals = (user, ToolCallItem(call, turn, new_step_id()), result)
            await repository.append_items(runtime.thread_id, originals)
            events = [event async for event in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            projected = {i.id: i for i in requests[0].items}
            expected = (
                TextContent("before"),
                AudioAttachment("data:audio/mp4;base64,YXVkaW8=")
                if supported
                else TextContent(UNSUPPORTED_INPUT),
                TextContent(PROCESSING_ERROR),
                TextContent("after"),
            )
            assert projected[user.id].content_items == expected
            assert projected[user.id].content == user.content
            assert projected[result.id].content_items == expected and projected[result.id].is_error
            stored = await repository.load_items(runtime.thread_id)
            assert tuple(i for i in stored if i.id in {x.id for x in originals}) == originals
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode", "code_mode_only"])
@pytest.mark.parametrize(
    "url,expected",
    [
        ("data:audio/x-wav;BASE64,YXVkaW8=", AudioAttachment("data:audio/wav;base64,YXVkaW8=")),
        (
            "data:audio/wav;base64,%%%",
            TextContent("audio content omitted because it could not be processed"),
        ),
        (
            "data:audio/flac;base64,YXVkaW8=",
            TextContent(
                "audio content omitted because its format is not supported; "
                "use wav, mp3, m4a, webm, or ogg"
            ),
        ),
    ],
)
def test_audio_preparation_in_real_runtime_preserves_order_and_success(
    tmp_path, mode, url, expected
):
    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        requests = []

        class Probe:
            spec = ToolSpec("probe", "audio fixture", {"type": "object"})

            async def execute(self, call, context):
                return ToolResult(
                    call.id,
                    call.name,
                    "audio",
                    content_items=(
                        TextContent("before"),
                        AudioAttachment(url),
                        TextContent("after"),
                    ),
                    code_mode_output=CodeModeOutput({"audio_url": url}),
                )

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 1:
                    call = (
                        ToolCall(new_tool_call_id(), "probe", {})
                        if mode == "direct"
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="const r=await tools.probe({}); "
                            'text("before"); audio(r); text("after");',
                        )
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=mode,
                supports_audio_input=True,
            ),
            database_path=tmp_path / "audio.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("listen")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
            assert result.content_items[-3:] == (
                TextContent("before"),
                expected,
                TextContent("after"),
            )
            assert not result.is_error
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
