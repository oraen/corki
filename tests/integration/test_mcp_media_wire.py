import asyncio
import base64
import io
import json
import sqlite3
import wave
from dataclasses import replace

import httpx
import pytest
from PIL import Image

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.tools import MCPTool
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import ToolOutputDelta, TurnCompleted
from corki.protocol.items import ToolResultItem
from corki.protocol.tool_names import compatible_tool_name
from corki.protocol.tools import AudioAttachment, EncryptedContent, ImageAttachment, TextContent
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "api,native", [("responses", True), ("responses", False), ("chat_completions", False)]
)
@pytest.mark.parametrize("media", [False, True])
@pytest.mark.parametrize("detail", ["high", "low"])
def test_mcp_ordered_media_and_cipher_reach_model_without_leaking_into_logs(
    tmp_path, api, native, media, detail
):
    async def scenario():
        png, wav = io.BytesIO(), io.BytesIO()
        Image.new("RGB", (1, 1), "red").save(png, format="PNG")
        with wave.open(wav, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\x00\x00" * 8)
        cipher = "OPAQUE_CIPHERTEXT=="
        raw = {
            "content": [
                {"type": "text", "text": "before"},
                {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": base64.b64encode(png.getvalue()).decode(),
                    "_meta": {"codex/imageDetail": detail},
                },
                {
                    "type": "audio",
                    "mimeType": "audio/wav",
                    "data": base64.b64encode(wav.getvalue()).decode(),
                },
                {"type": "text", "text": cipher, "_meta": {"codex/encryptedContent": True}},
                {"type": "text", "text": "after"},
            ],
            "structuredContent": {"ignored": "STRUCTURED_IGNORED"},
            "_meta": {"secret": "PRIVATE"},
        }
        calls, requests = [], []

        class Client:
            async def call_tool(self, name, arguments):
                calls.append(name)
                return raw

        def respond(request):
            requests.append(json.loads(request.content))
            first = len(requests) == 1
            if api == "responses":
                output = (
                    [
                        {
                            "type": "function_call",
                            "call_id": "read",
                            "name": compatible_tool_name("mcp__docs::read"),
                            "arguments": "{}",
                        }
                    ]
                    if first
                    else [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ]
                )
                packet = {
                    "type": "response.completed",
                    "response": {"id": f"r{len(requests)}", "output": output},
                }
            else:
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "read",
                                "type": "function",
                                "function": {
                                    "name": compatible_tool_name("mcp__docs::read"),
                                    "arguments": "{}",
                                },
                            }
                        ]
                    }
                    if first
                    else {"content": "done"}
                )
                packet = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": "tool_calls" if first else "stop",
                        }
                    ]
                }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = OpenAIResponsesModel if api == "responses" else OpenAICompatibleModel
        model = adapter(
            api_key="fixture",
            base_url="https://fixture.invalid",
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url="https://fixture.invalid", api_mode=api),
                supports_encrypted_tool_output=native,
                supports_audio_input=media,
            ),
        )
        registry = ToolRegistry()
        registry.register(MCPTool("docs", {"name": "read"}, Client()))
        database = tmp_path / "session.db"
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                supports_image_input=media,
                supports_audio_input=media,
            ),
            database_path=database,
            registry=registry,
            model=model,
        )
        try:
            events = [e async for e in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2 and calls == ["read"]
            wire = json.dumps(requests[1])
            assert cipher not in wire
            assert "STRUCTURED_IGNORED" not in wire and "PRIVATE" not in wire
            display = "".join(e.delta for e in events if isinstance(e, ToolOutputDelta))
            assert "before" in display and "after" in display
            assert cipher not in display and "base64" not in display and "PRIVATE" not in display
            original = [
                i
                for i in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(i, ToolResultItem)
            ][0]
            parts = original.content_items
            assert parts[0].text.startswith("Wall time:") and parts[1] == TextContent("before")
            assert parts[4:] == (EncryptedContent(cipher), TextContent("after"))
            if media:
                if detail == "low":
                    from corki.media.images import LOW_ERROR

                    assert parts[2] == TextContent(LOW_ERROR)
                else:
                    assert isinstance(parts[2], ImageAttachment) and parts[2].detail == "high"
                assert isinstance(parts[3], AudioAttachment)
            else:
                assert parts[2] == TextContent(
                    "<image content omitted because you do not support image input>"
                )
                assert parts[3] == TextContent(
                    "<audio content omitted because you do not support audio input>"
                )
            with sqlite3.connect(database) as db:
                (value,) = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name='mcp__docs::read'"
                ).fetchone()
            public = json.loads(value)["code_mode_output"]["value"]
            assert "_meta" not in public and public["structuredContent"] == raw["structuredContent"]
            assert public["content"][1]["type"] == ("image" if media else "text")
            assert public["content"][2]["type"] == ("audio" if media else "text")
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
