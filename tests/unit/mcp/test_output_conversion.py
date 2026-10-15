import asyncio
import json

import pytest

from corki.mcp.tools import MCPTool
from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import EncryptedContent, ImageAttachment, TextContent, ToolCall
from corki.tools import ToolContext


@pytest.mark.parametrize("kind", ["structured", "encrypted", "image", "unknown"])
def test_mcp_source_output_precedence_and_order(tmp_path, kind):
    raw = {
        "content": [{"type": "text", "text": "ignored"}],
        "structuredContent": {"count": 1},
        "_meta": {"secret": "PRIVATE"},
    }
    if kind == "encrypted":
        raw["content"] = [
            {"type": "text", "text": "opaque==", "_meta": {"codex/encryptedContent": True}},
            {"type": "text", "text": "after"},
        ]
    elif kind == "image":
        raw = {
            "content": [
                {"type": "text", "text": "before"},
                {
                    "type": "image",
                    "data": "data:image/png;base64,AAAA",
                    "mimeType": "image/png",
                    "_meta": {"codex/imageDetail": "low"},
                },
                {"type": "text", "text": "after"},
            ]
        }
    elif kind == "unknown":
        raw = {"content": [None, 42, {"type": "audio", "mimeType": "audio/wav"}]}

    class Client:
        async def call_tool(self, name, arguments):
            return raw

    async def scenario():
        tool = MCPTool("fixture", {"name": "read"}, Client())
        result = await tool.execute(
            ToolCall(new_tool_call_id(), tool.spec.name, {}), ToolContext(tmp_path)
        )
        assert result.fallback_token_limit_override == 3000
        assert (
            result.display_content.startswith("Wall time: ")
            and "\nOutput:" in result.display_content
        )
        assert "PRIVATE" not in result.display_content
        if kind == "unknown":
            assert result.is_error and not result.dispatch_error
            assert "MCPProtocolError" in result.content
            assert result.code_mode_output.value["isError"] is True
            return
        if kind == "structured":
            assert result.content.split("\nOutput:\n", 1)[1] == '{"count":1}'
            assert not result.content_items and "ignored" not in result.content
        else:
            assert result.content_items[0].text.startswith("Wall time: ")
            if kind == "encrypted":
                assert result.content_items[1:] == (
                    EncryptedContent("opaque=="),
                    TextContent("after"),
                )
                assert "opaque==" not in result.display_content
            elif kind == "image":
                assert result.content_items[1:] == (
                    TextContent("before"),
                    ImageAttachment("data:image/png;base64,AAAA", "low"),
                    TextContent("after"),
                )
                assert "base64" not in result.display_content
        assert "_meta" not in result.code_mode_output.value
        assert result.code_mode_output.value.get("structuredContent") == raw.get(
            "structuredContent"
        )
        assert (
            json.loads(json.dumps(result.code_mode_output.value)) == result.code_mode_output.value
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["image", "audio"])
@pytest.mark.parametrize("mime_key", [None, "mimeType", "mime_type"])
def test_mcp_media_requires_wire_mime_type_before_data_url_conversion(tmp_path, kind, mime_key):
    from corki.mcp.client import MCPProtocolError
    from corki.mcp.output import mcp_output
    from corki.protocol.tools import AudioAttachment
    from corki.protocol.truncation import TruncationPolicy

    block = {"type": kind, "data": "AAAA", "_meta": {"codex/imageDetail": "original"}}
    if mime_key:
        block[mime_key] = "fixture/mime"
    if mime_key != "mimeType":
        with pytest.raises(MCPProtocolError):
            mcp_output(
                ToolCall(new_tool_call_id(), "read", {}),
                {"content": [block]},
                context=ToolContext(tmp_path, supports_audio_input=True),
                policy=TruncationPolicy(),
                wall_time=1.25,
            )
        return
    result = mcp_output(
        ToolCall(new_tool_call_id(), "read", {}),
        {
            "content": [
                block,
                {"type": kind, "data": "data:already;raw", "mimeType": "fixture/mime"},
            ]
        },
        context=ToolContext(tmp_path, supports_audio_input=True),
        policy=TruncationPolicy(),
        wall_time=1.25,
    )
    cls = ImageAttachment if kind == "image" else AudioAttachment
    assert result.content_items == (
        TextContent("Wall time: 1.2500 seconds\nOutput:"),
        cls("data:fixture/mime;base64,AAAA"),
        cls("data:already;raw"),
    )
    assert result.display_content == "Wall time: 1.2500 seconds\nOutput:"


@pytest.mark.parametrize("limit", [1, 128])
def test_mcp_structured_model_budget_does_not_pretruncate_log_or_nested(tmp_path, limit):
    from corki.mcp.output import mcp_output
    from corki.protocol.truncation import TruncationPolicy

    raw = {
        "content": [{"type": "text", "text": "ignored"}],
        "structuredContent": {"items": "large " * 1000},
    }
    result = mcp_output(
        ToolCall(new_tool_call_id(), "read", {}),
        raw,
        context=ToolContext(tmp_path),
        policy=TruncationPolicy("bytes", limit),
        wall_time=1.25,
    )
    assert "chars truncated" in result.content and "ignored" not in result.content
    assert result.display_content == "Wall time: 1.2500 seconds\nOutput:\n" + json.dumps(
        raw["structuredContent"], separators=(",", ":")
    )
    assert result.code_mode_output.value == raw


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "100"])
def test_mcp_per_tool_output_limit_requires_positive_integer(limit):
    from corki.config import MCPServerSettings

    with pytest.raises(ValueError, match="positive integer"):
        MCPServerSettings.from_mapping(
            "docs",
            {
                "transport": "http",
                "url": "https://fixture.invalid",
                "tools": {"read": {"output_token_limit": limit}},
            },
        )


def test_mcp_per_tool_config_parses_distinct_limits():
    from corki.config import MCPServerSettings

    settings = MCPServerSettings.from_mapping(
        "docs",
        {
            "transport": "http",
            "url": "https://fixture.invalid",
            "tools": {"read": {"output_token_limit": 80}, "write": {"output_token_limit": 9}},
        },
    )
    assert settings.tool_output_token_limits == (("read", 80), ("write", 9))


@pytest.mark.parametrize("tag", [[], {}])
def test_malformed_mcp_type_is_a_protocol_error(tmp_path, tag):
    from corki.mcp.client import MCPProtocolError
    from corki.mcp.output import mcp_output
    from corki.protocol.truncation import TruncationPolicy

    block = {"type": tag, "data": "raw"}
    with pytest.raises(MCPProtocolError):
        mcp_output(
            ToolCall(new_tool_call_id(), "read", {}),
            {"content": [block]},
            context=ToolContext(tmp_path),
            policy=TruncationPolicy(),
            wall_time=0,
        )


def test_mcp_inflight_call_keeps_its_output_policy_while_connection_retires(tmp_path):
    from corki.config import MCPServerSettings
    from corki.mcp.connection import MCPConnection

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Client:
            def __init__(self, limit, wait):
                self.settings = MCPServerSettings(
                    "docs",
                    "http",
                    url="https://fixture.invalid",
                    tool_output_token_limits=(("read", limit),),
                )
                self.wait, self.closed = wait, False

            async def call_tool(self, name, arguments):
                if self.wait:
                    entered.set()
                    await release.wait()
                return {"content": [{"type": "text", "text": "x" * 5000}]}

            async def aclose(self):
                self.closed = True

        old, latest = Client(1, True), Client(80, False)
        first = MCPConnection(old, lambda *args: None)
        second = MCPConnection(latest, lambda *args: None)
        tool = MCPTool("docs", {"name": "read"}, first)
        call = ToolCall(new_tool_call_id(), tool.spec.name, {})
        task = asyncio.create_task(tool.execute(call, ToolContext(tmp_path)))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            first.retire()
            assert not old.closed
            fresh = await MCPTool("docs", {"name": "read"}, second).execute(
                call, ToolContext(tmp_path)
            )
            assert fresh.fallback_token_limit_override == 96
            release.set()
            original = await asyncio.wait_for(task, 2)
            assert original.fallback_token_limit_override == 2
            assert original.code_mode_output.value == fresh.code_mode_output.value
            assert len(original.content) < len(fresh.content)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await first.aclose()
            await second.aclose()
        assert old.closed and latest.closed

    asyncio.run(scenario())
