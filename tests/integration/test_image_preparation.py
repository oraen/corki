"""Runtime image preparation must replace malformed pixels before model input."""

import asyncio
import base64
import io
import json
import sqlite3

import httpx
import pytest
from PIL import Image

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.media.images import PROCESSING_ERROR, UNSUPPORTED
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
    CodeModeOutput,
    ImageAttachment,
    TextContent,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


def png_bytes(size=(2048, 2048)):
    stream = io.BytesIO()
    with Image.new("RGB", size, (10, 20, 30)) as pixels:
        pixels.save(stream, format="PNG")
    return stream.getvalue()


def decoded_size(url):
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as pixels:
        pixels.load()
        return pixels.size


@pytest.mark.parametrize("supported", [False, True])
def test_legacy_user_and_tool_media_are_projected_without_rewriting_sqlite(tmp_path, supported):
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
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                supports_image_input=supported,
            ),
            database_path=database,
            repository=repository,
            model=Model(),
        )
        try:
            await runtime._ensure_ready()
            turn = new_turn_id()
            url = "data:image/wrong;base64," + base64.b64encode(png_bytes()).decode()
            user = UserMessageItem(
                "user input unchanged",
                turn,
                content_items=(
                    TextContent("before"),
                    ImageAttachment("data:image/png;base64,UFJJVkFURQ=="),
                    TextContent("after"),
                    ImageAttachment(url),
                ),
            )
            call = ToolCall(new_tool_call_id(), "old_tool", {})
            result = ToolResultItem(
                call.id, call.name, "old output", turn, attachments=(ImageAttachment(url),)
            )
            originals = (user, ToolCallItem(call, turn, new_step_id()), result)
            await repository.append_items(runtime.thread_id, originals)
            events = [event async for event in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            projected_user = next(i for i in requests[0].items if i.id == user.id)
            projected_result = next(i for i in requests[0].items if i.id == result.id)
            assert projected_user.content == user.content
            assert projected_user.content_items[:3] == (
                TextContent("before"),
                TextContent(PROCESSING_ERROR),
                TextContent("after"),
            )
            for image in (projected_user.content_items[-1], projected_result.content_items[-1]):
                if supported:
                    assert decoded_size(image.data_url) == (1600, 1600)
                else:
                    assert image == TextContent(UNSUPPORTED)
            stored = await repository.load_items(runtime.thread_id)
            assert tuple(i for i in stored if i.id in {x.id for x in originals}) == originals
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def wire_response(transport, call):
    if transport == "chat":
        delta = (
            {"tool_calls": [{"index": 0, "id": "c", "type": "function", "function": call}]}
            if call
            else {}
        )
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "tool_calls" if call else "stop"}
                ]
            },
        ]
        return "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
    body = ""
    if call:
        custom = transport == "responses_native" and call["name"] == "exec"
        item = {
            "id": "i",
            "call_id": "c",
            "type": "custom_tool_call" if custom else "function_call",
            "name": call["name"],
        }
        item.update(
            {"input": json.loads(call["arguments"])["input"]}
            if custom
            else {"arguments": call["arguments"]}
        )
        body = "data: " + json.dumps({"type": "response.output_item.done", "item": item}) + "\n\n"
    return body + 'data: {"type":"response.completed","response":{"id":"r"}}\n\n'


@pytest.mark.parametrize("transport", ["responses_native", "responses_compatible", "chat"])
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_real_view_image_reaches_wire_resized_but_js_and_ledger_keep_typed_original(
    tmp_path, monkeypatch, transport, mode
):
    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        raw = png_bytes()
        (tmp_path / "image.untrusted-extension").write_bytes(raw)
        arguments = {"path": "image.untrusted-extension", "detail": "original"}
        call = {
            "name": "view_image" if mode == "direct" else "exec",
            "arguments": json.dumps(
                arguments
                if mode == "direct"
                else {
                    "input": "const r=await tools.view_image(" + json.dumps(arguments) + "); "
                    "text({detail:r.detail,length:r.image_url.length}); image(r);"
                }
            ),
        }
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(
                200, text=wire_response(transport, call if len(requests) == 1 else None)
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
        database = tmp_path / "wire.db"
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=mode,
                api_key="fixture",
                api_mode="chat_completions" if transport == "chat" else "responses",
                tool_freeform_mode="native" if transport == "responses_native" else "compatible",
            ),
            database_path=database,
        )
        try:
            events = [event async for event in runtime.stream("show image")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
            if transport == "chat":
                messages = requests[1]["messages"]
                result = next(i for i in messages if i.get("role") == "tool")
                parts = messages[messages.index(result) + 1]["content"]
                image = next(p["image_url"] for p in parts if p["type"] == "image_url")
                url, detail = image["url"], image["detail"]
            else:
                result = next(
                    i for i in requests[1]["input"] if i.get("type", "").endswith("call_output")
                )
                parts = result["output"]
                image = next(p for p in parts if p["type"] == "input_image")
                url, detail = image["image_url"], image["detail"]
            assert decoded_size(url) == (1600, 1600) and detail == "high"
            with sqlite3.connect(database) as connection:
                rows = connection.execute(
                    "SELECT status, result_json FROM tool_executions WHERE tool_name='view_image'"
                ).fetchall()
            assert len(rows) == 1 and rows[0][0] == "completed"
            ledger = json.loads(rows[0][1])
            typed = ledger["code_mode_output"]["value"]
            assert decoded_size(typed["image_url"]) == (2048, 2048)
            assert base64.b64decode(typed["image_url"].split(",")[1]) == raw
            assert typed["detail"] == "high"
            assert typed["image_url"] not in json.dumps(requests[1])
            if mode != "direct":
                expected = json.dumps(
                    {"detail": "high", "length": len(typed["image_url"])}, separators=(",", ":")
                )
                assert any(p.get("text") == expected for p in parts)
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode", "code_mode_only"])
def test_malformed_image_is_replaced_in_real_runtime_history(tmp_path, mode):
    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        requests = []
        url = "data:image/png;base64,UFJJVkFURSBub3QgYW4gaW1hZ2U="

        class Probe:
            spec = ToolSpec("probe", "image fixture", {"type": "object"})

            async def execute(self, call, context):
                return ToolResult(
                    call.id,
                    call.name,
                    "image",
                    attachments=(ImageAttachment(url),),
                    code_mode_output=CodeModeOutput({"image_url": url}),
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
                            raw_arguments="image(await tools.probe({}));",
                        )
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode=mode
            ),
            database_path=tmp_path / "media.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("show image")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
            assert "image content omitted because it could not be processed" in result.content
            assert not result.attachments
            assert url not in json.dumps([str(part) for part in result.content_items])
            assert not result.is_error
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
