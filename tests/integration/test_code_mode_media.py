"""Media helpers must reach the real model request, not only exist in JavaScript."""

import asyncio
import base64
import io
import json
import re
import struct

import httpx
import pytest
from PIL import Image

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.mcp.tools import MCPTool
from corki.media.images import LOW_ERROR
from corki.models import ModelCompleted
from corki.protocol.events import ContextCompacted, TurnCompleted
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
    ImageAttachment,
    TextContent,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from corki.sessions.models import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


def _fixture_image(color):
    stream = io.BytesIO()
    with Image.new("RGB", (2, 2), color) as pixels:
        pixels.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


_IMAGE_URLS = {
    marker: _fixture_image((index * 20, 30, 40))
    for index, marker in enumerate(
        (
            "data:checkpoint",
            "data:a",
            "data:first",
            "data:second",
            "data:persisted",
            "data:image/png;base64,YWJj",
        )
    )
}


def _source(source):
    # Replace complete JS fixture literals, never prefixes such as data:a in audio.
    for marker, url in _IMAGE_URLS.items():
        source = source.replace(repr(marker), repr(url)).replace(
            json.dumps(marker), json.dumps(url)
        )
    return source


class ClosingModel:
    async def aclose(self):
        pass


def test_pending_turn_resumes_media_from_real_graph_checkpoint(tmp_path):
    async def scenario():
        started = asyncio.Event()
        samples = []
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode="code_mode_only",
            supports_audio_input=True,
        )
        database = tmp_path / "checkpoint.db"

        class Sink:
            async def emit(self, event):
                pass

        class Before(ClosingModel):
            async def stream(self, request):
                samples.append(request)
                if len(samples) == 1:
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments=_source(
                            "image('data:checkpoint'); "
                            + f"audio({json.dumps(wav_url())}); "
                            + "text('persisted');"
                        ),
                    )
                    yield ModelCompleted(
                        (ToolCallItem(call, request.items[-1].turn_id, new_step_id()),)
                    )
                else:
                    started.set()
                    await asyncio.Event().wait()

        class After(ClosingModel):
            async def stream(self, request):
                samples.append(request)
                result = next(i for i in request.items if isinstance(i, ToolResultItem))
                assert result.content_items[1] == ImageAttachment(_IMAGE_URLS["data:checkpoint"])
                assert result.content_items[2] == AudioAttachment(wav_url())
                yield ModelCompleted(
                    (AssistantMessageItem("resumed", request.items[-1].turn_id, new_step_id()),)
                )

        repository = SQLiteSessionRepository(database)
        first = LangGraphRuntime.create(
            settings=settings, database_path=database, repository=repository, model=Before()
        )
        invocation = None
        try:
            await first._ensure_ready()
            turn = new_turn_id()
            user = UserMessageItem("run media", turn)
            await repository.save_turn(
                TurnRecord(turn, first.thread_id, TurnStatus.RUNNING, user.content)
            )
            await repository.append_items(first.thread_id, (user,))
            invocation = asyncio.create_task(
                first._compiled.ainvoke(
                    _initial_state(first.thread_id, turn, settings, user),
                    context=GraphRunContext(events=Sink()),
                    config=first._graph_config(turn),
                )
            )
            await asyncio.wait_for(started.wait(), 3)
            invocation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await invocation
            checkpoint = await first._checkpointer.aget_tuple(first._graph_config(turn))
            assert any(
                isinstance(i, ToolResultItem) and i.content_items
                for i in checkpoint.checkpoint["channel_values"]["request_items"]
            )
            thread = first.thread_id
        finally:
            if invocation is not None and not invocation.done():
                invocation.cancel()
                await asyncio.gather(invocation, return_exceptions=True)
            await first.aclose()
        second = LangGraphRuntime.create(
            settings=settings, database_path=database, thread_id=thread, model=After()
        )
        try:
            events = [event async for event in second.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "resumed" and len(samples) == 3
            stored = await SQLiteSessionRepository(database).load_items(thread)
            assert sum(isinstance(i, ToolCallItem) and i.call.name == "exec" for i in stored) == 1
        finally:
            await second.aclose()

    asyncio.run(scenario())


def test_helpers_return_undefined_and_zero_budget_keeps_image(tmp_path):
    result = asyncio.run(
        run_media(
            tmp_path,
            "text(typeof image('data:a')); text(typeof audio('data:b')); "
            "text(typeof text('marker'));",
        )
    )
    assert [part.text for part in result.content_items if isinstance(part, TextContent)].count(
        "undefined"
    ) == 3
    result = asyncio.run(
        run_media(
            tmp_path / "zero",
            '// @exec: {"max_output_tokens":0}\ntext("before"); image("data:a"); audio("data:b");',
        )
    )
    assert isinstance(result.content_items[1], ImageAttachment)
    assert not any(isinstance(part, AudioAttachment) for part in result.content_items)
    assert "omitted 1 audio" in result.content
    assert "Script completed" in result.content


@pytest.mark.parametrize("terminate", [False, True])
def test_yield_wait_consumes_media_once_and_joins_nested_work(tmp_path, terminate):
    async def scenario():
        started, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
        requests, results = [], []

        class Gate:
            spec = ToolSpec("gate", "fixture", {"type": "object"})

            async def execute(self, call, context):
                started.set()
                try:
                    await release.wait()
                    return ToolResult(call.id, call.name, "released")
                finally:
                    stopped.set()

        class Model(ClosingModel):
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 1:
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments=_source(
                            "image('data:first'); yield_control(); "
                            "await tools.gate({}); image('data:second');"
                        ),
                    )
                elif len(requests) == 2:
                    await asyncio.wait_for(started.wait(), 3)
                    result = next(i for i in request.items if isinstance(i, ToolResultItem))
                    results.append(result)
                    identifier = re.search(r"cell ID (\S+)", result.content)[1]
                    if not terminate:
                        release.set()
                    call = ToolCall(
                        new_tool_call_id(), "wait", {"cell_id": identifier, "terminate": terminate}
                    )
                else:
                    results.append([i for i in request.items if isinstance(i, ToolResultItem)][-1])
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))

        registry = ToolRegistry()
        registry.register(Gate())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "yield.db",
            model=Model(),
            registry=registry,
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert [
                part.data_url
                for part in results[0].content_items
                if isinstance(part, ImageAttachment)
            ] == [_IMAGE_URLS["data:first"]]
            assert [
                part.data_url
                for part in results[1].content_items
                if isinstance(part, ImageAttachment)
            ] == ([] if terminate else [_IMAGE_URLS["data:second"]])
            assert stopped.is_set() and not runtime._code_mode.cells
            assert not results[1].is_error
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_reopened_runtime_replays_media_history_without_rerunning_script(tmp_path):
    async def scenario():
        requests = []

        class Model(ClosingModel):
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 1:
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments=_source("image('data:persisted'); text('after');"),
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
                ),
                database_path=tmp_path / "reopen.db",
                model=Model(),
                thread_id=thread,
            )

        runtime = create()
        try:
            events = [event async for event in runtime.stream("emit")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = create(thread)
            events = [event async for event in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            old = next(i for i in requests[1].items if isinstance(i, ToolResultItem))
            restored = next(i for i in requests[2].items if isinstance(i, ToolResultItem))
            assert (
                restored == old
                and restored.content_items[1].data_url == _IMAGE_URLS["data:persisted"]
            )
            assert len(requests) == 3 and not runtime._code_mode.cells
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_media_cost_triggers_real_compaction_without_losing_current_input(tmp_path):
    async def scenario():
        ordinary, summaries = [], []

        class Model(ClosingModel):
            async def stream(self, request):
                turn = request.items[-1].turn_id
                if not request.tools:
                    summaries.append(request)
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "Image observations summarized.", turn, new_step_id()
                            ),
                        )
                    )
                else:
                    ordinary.append(request)
                    if len(ordinary) == 1:
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=_source(
                                "for (let i=0;i<4;i++) image('data:image/png;base64,YWJj');"
                            ),
                        )
                        yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
                    else:
                        yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode="code_mode_only",
                context_window_tokens=16000,
                auto_compact_tokens=6000,
            ),
            database_path=tmp_path / "compact.db",
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("CURRENT_INPUT_KEEP_VERBATIM")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(summaries) == 1 and any(
                isinstance(event, ContextCompacted) for event in events
            )
            result = next(i for i in summaries[0].items if isinstance(i, ToolResultItem))
            assert sum(isinstance(part, ImageAttachment) for part in result.content_items) == 4
            assert any(
                isinstance(i, UserMessageItem) and i.content == "CURRENT_INPUT_KEEP_VERBATIM"
                for i in ordinary[-1].items
            )
            stored = await SQLiteSessionRepository(tmp_path / "compact.db").load_items(
                runtime.thread_id
            )
            assert any(
                isinstance(i, ToolResultItem) and i.content_items == result.content_items
                for i in stored
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def wav_url(milliseconds=100):
    data = b"\0\0" * milliseconds
    raw = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    raw += struct.pack("<IHHIIHH", 16, 1, 1, 1000, 2000, 2, 16)
    raw += b"data" + struct.pack("<I", len(data)) + data
    return "data:audio/wav;base64," + base64.b64encode(raw).decode()


async def run_media(tmp_path, source, *, registry=None, status="completed", original=False):
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            turn = request.items[-1].turn_id
            if len(requests) == 1:
                call = ToolCall(
                    new_tool_call_id(),
                    "exec",
                    None,
                    raw_arguments=_source(source),
                    input_kind="freeform",
                )
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
            else:
                yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

        async def aclose(self):
            pass

    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode="code_mode_only",
            supports_image_detail_original=original,
            supports_audio_input=True,
        ),
        database_path=tmp_path / "media.db",
        model=Model(),
        registry=registry,
    )
    try:
        events = [event async for event in runtime.stream("emit media")]
        assert isinstance(events[-1], TurnCompleted), events[-1]
        assert len(requests) == 2
        result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
        assert f"Script {status}" in result.content, result.content
        assert not runtime._code_mode.cells
        return result
    finally:
        await runtime.aclose()


@pytest.mark.parametrize(
    "helper,source",
    [
        ("image", "image('data:image/png;base64,YWJj');"),
        ("audio", "audio('data:audio/wav;base64,YWJj');"),
        (
            "generatedImage",
            "generatedImage({image_url:'data:image/png;base64,YWJj',output_hint:'hint'});",
        ),
    ],
)
def test_media_helper_keeps_output_in_order(tmp_path, helper, source):
    result = asyncio.run(run_media(tmp_path, "text('before');" + source + "text('after');"))
    assert result.content_items
    names = [type(item).__name__ for item in result.content_items]
    assert names[:3] == [
        "TextContent",
        "TextContent",
        "AudioAttachment" if helper == "audio" else "ImageAttachment",
    ]
    assert result.content_items[1].text == "before"
    assert result.content_items[-1].text == "after"
    if helper == "generatedImage":
        assert result.content_items[-2].text == "hint"


@pytest.mark.parametrize("milliseconds,kept", [(24, False), (25, True)])
def test_tiny_wav_is_replaced_with_explanation(tmp_path, milliseconds, kept):
    result = asyncio.run(run_media(tmp_path, f"audio({json.dumps(wav_url(milliseconds))});"))
    assert any(isinstance(part, AudioAttachment) for part in result.content_items) == kept
    if not kept:
        assert "shorter than 25 ms" in result.content


@pytest.mark.parametrize(
    "expression",
    [
        "image('https://example.test/a.png')",
        "audio('file:///audio.wav')",
        "image({image_url:'data:a',detail:3})",
        "image('data:a', 'bad')",
        "generatedImage({image_url:'data:a',output_hint:null})",
        "image([])",
        "audio({type:'text',text:'wrong'})",
    ],
)
def test_bad_helper_arguments_are_catchable_without_emitting_media(tmp_path, expression):
    result = asyncio.run(
        run_media(tmp_path, f"try {{ {expression}; }} catch(e) {{ text(e.name); }}")
    )
    assert "TypeError" in result.content
    assert all(isinstance(part, TextContent) for part in result.content_items)


def test_uncaught_error_keeps_prior_media_and_is_not_success(tmp_path):
    result = asyncio.run(
        run_media(
            tmp_path,
            "image('data:image/png;base64,YWJj'); throw Error('failure');",
            status="failed",
        )
    )
    assert result.is_error
    assert isinstance(result.content_items[1], ImageAttachment)
    assert "failure" in result.content_items[-1].text


def test_mcp_content_blocks_can_be_forwarded_without_loss(tmp_path):
    audio_url = wav_url()

    class Client:
        async def call_tool(self, name, arguments):
            return {
                "content": [
                    {
                        "type": "image",
                        "mime_type": "image/png",
                        "data": _IMAGE_URLS["data:image/png;base64,YWJj"].split(",")[1],
                        "_meta": {"codex/imageDetail": "original"},
                    },
                    {"type": "audio", "mimeType": "audio/wav", "data": audio_url.split(",")[1]},
                ],
                "_meta": {"secret": "PRIVATE"},
            }

    registry = ToolRegistry()
    registry.register(MCPTool("fixture", {"name": "read"}, Client()))
    result = asyncio.run(
        run_media(
            tmp_path,
            "const r=await tools.mcp__fixture__read({}); image(r.content[0]); "
            "image(r.content[0], 'LOW'); audio(r.content[1]);",
            registry=registry,
            original=True,
        )
    )
    assert result.content_items[1:] == (
        ImageAttachment(_IMAGE_URLS["data:image/png;base64,YWJj"], "original"),
        TextContent(LOW_ERROR),
        AudioAttachment(audio_url),
    )
    assert "PRIVATE" not in result.content


@pytest.mark.parametrize("transport", ["responses_native", "responses_compatible", "chat"])
@pytest.mark.parametrize("audio_enabled", [False, True])
def test_provider_wire_preserves_order_and_audio_capability(
    tmp_path, monkeypatch, transport, audio_enabled
):
    async def scenario():
        requests = []
        audio_url = wav_url()
        source = "text('before'); image('data:image/png;base64,YWJj'); text('between');"
        # The wire must receive the canonical MIME even when JS emits an alias.
        source += (
            f"audio({json.dumps(audio_url.replace('audio/wav', 'audio/vnd.wave'))}); text('after');"
        )
        source = _source(source)

        def handle(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if transport == "chat":
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c",
                                "type": "function",
                                "function": {
                                    "name": "exec",
                                    "arguments": json.dumps({"input": source}),
                                },
                            }
                        ]
                    }
                    if len(requests) == 1
                    else {}
                )
                body = (
                    "data: "
                    + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
                    + "\n\n"
                )
                body += (
                    "data: "
                    + json.dumps(
                        {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "tool_calls" if len(requests) == 1 else "stop",
                                }
                            ]
                        }
                    )
                    + "\n\ndata: [DONE]\n\n"
                )
            else:
                native = transport == "responses_native"
                item = {
                    "type": "custom_tool_call" if native else "function_call",
                    "name": "exec",
                    "id": "i",
                    "call_id": "c",
                }
                item.update(
                    {"input": source} if native else {"arguments": json.dumps({"input": source})}
                )
                body = (
                    "data: "
                    + json.dumps({"type": "response.output_item.done", "item": item})
                    + "\n\n"
                    if len(requests) == 1
                    else ""
                )
                body += 'data: {"type":"response.completed","response":{"id":"r"}}\n\n'
            return httpx.Response(200, text=body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_key="fixture",
                tool_mode="code_mode_only",
                supports_audio_input=audio_enabled,
                api_mode="chat_completions" if transport == "chat" else "responses",
                tool_freeform_mode="native" if transport == "responses_native" else "compatible",
            ),
            database_path=tmp_path / "wire.db",
        )
        try:
            events = [event async for event in runtime.stream("emit")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
            if transport == "chat":
                messages = requests[-1]["messages"]
                result = next(i for i in messages if i.get("role") == "tool")
                content = messages[messages.index(result) + 1]["content"][1:]
                text_type, image_type = "text", "image_url"
            else:
                result = next(
                    i for i in requests[-1]["input"] if i.get("type", "").endswith("call_output")
                )
                content = result["output"]
                text_type, image_type = "input_text", "input_image"
            assert [p["type"] for p in content] == [
                text_type,
                text_type,
                image_type,
                text_type,
                "input_audio" if audio_enabled else text_type,
                text_type,
            ]
            assert [content[i]["text"] for i in (1, 3, 5)] == ["before", "between", "after"]
            if not audio_enabled:
                assert (
                    content[4]["text"]
                    == "audio content omitted because you do not support audio input"
                )
            elif transport == "chat":
                assert content[4]["input_audio"] == {
                    "data": audio_url.split(",")[1],
                    "format": "wav",
                }
            else:
                assert content[4]["audio_url"] == audio_url
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
