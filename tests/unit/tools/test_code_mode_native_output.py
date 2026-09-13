import asyncio
import json

import pytest

from corki.code_mode.output import truncate_cell_output
from corki.code_mode.service import CodeModeService
from corki.protocol.tools import (
    AudioAttachment,
    EncryptedContent,
    ImageAttachment,
    TextContent,
    ToolResult,
    ToolSpec,
)
from corki.tools import ToolRegistry


@pytest.mark.parametrize("budget", [0, 5, 15])
@pytest.mark.parametrize("raw", ["0123456789" * 4, "头" + "字" * 20 + "尾"])
def test_real_cell_uses_native_utf8_head_tail_budget(raw, budget):
    async def scenario():
        service = CodeModeService(ToolRegistry())
        try:
            result = await service.execute("fixture", f"text({json.dumps(raw)});", 10000, budget)
            size = len(raw.encode())
            if size <= budget * 4:
                assert result.content_items[1:] == (TextContent(raw),)
            else:
                left = budget * 2
                prefix = raw.encode()[:left].decode("utf-8", errors="ignore")
                suffix = raw.encode()[-left:].decode("utf-8", errors="ignore") if left else ""
                expected = (
                    f"Warning: truncated output (original token count: {(size + 3) // 4})\n"
                    "Total output lines: 1\n\n"
                    f"{prefix}…{(size - budget * 4 + 3) // 4} tokens truncated…{suffix}"
                )
                assert result.content_items[1:] == (TextContent(expected),)
            assert not result.is_error and not service.cells
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_real_failed_cell_error_is_separate_line_after_prior_output():
    async def scenario():
        service = CodeModeService(ToolRegistry())
        try:
            result = await service.execute(
                "fixture", "text('before'); throw new Error('boom');", 10000, 1000
            )
            assert result.is_error and result.content_items[1] == TextContent("before")
            assert result.content_items[2].text.startswith("Script error:\n")
            assert "boom" in result.content_items[2].text
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_real_wait_uses_its_own_budget_and_only_new_output():
    async def scenario():
        gate = asyncio.Event()

        class Gate:
            spec = ToolSpec("gate", "deterministic observation boundary", {})

            async def execute(self, call, context):
                raise AssertionError("service fixture uses dispatch")

        registry = ToolRegistry()
        registry.register(Gate())
        service = CodeModeService(registry)

        async def dispatch(call, spec):
            await gate.wait()
            return ToolResult(call.id, call.name, "released")

        service.activate("turn", dispatch, None, registry=registry)
        try:
            first = await service.execute(
                "fixture",
                "text('FIRST'); await yield_control(); await tools.gate({}); "
                "text('0123456789'.repeat(4)); await yield_control(); await new Promise(()=>{});",
                10000,
                1000,
            )
            cell_id = first.content.split("cell ID ")[1].splitlines()[0]
            gate.set()
            second = await service.wait(cell_id, 10000, 5)
            assert second.content.startswith("Script running") and not second.is_error
            assert "FIRST" not in second.content
            assert second.content.endswith("0123456789…5 tokens truncated…0123456789")
            terminal = await service.wait(cell_id, 0, 1000, terminate=True)
            assert terminal.content.startswith("Script terminated") and not terminal.is_error
            assert len(terminal.content_items) == 1
            assert not service.cells
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("budget", [0, 1, 20])
def test_mixed_cell_output_uses_shared_native_policy(budget):
    image = ImageAttachment("data:image/png;base64,AA==")
    audio = AudioAttachment("data:audio/wav;base64,AA==")
    opaque = EncryptedContent("opaque")
    parts = (TextContent("abcdef"), image, TextContent("after"), audio, opaque)
    result = truncate_cell_output(parts, budget)
    if budget == 20:
        assert result == parts
    else:
        expected = (
            image,
            opaque,
            TextContent(f"[omitted {2 if budget == 0 else 1} text items ...]"),
            TextContent("[omitted 1 audio items ...]"),
        )
        if budget:
            expected = (TextContent("ab…1 tokens truncated…ef"), *expected)
        assert result == expected


def test_cell_text_preserves_blocks_until_combined_utf8_budget_is_exceeded():
    parts = (TextContent(""), TextContent("a\u2028b"), TextContent(""), TextContent("tail"))
    assert truncate_cell_output(parts, 100) == parts
    result = truncate_cell_output(parts, 1)
    assert len(result) == 1
    assert "Total output lines: 3" in result[0].text
    assert result[0].text.endswith("a…2 tokens truncated…il")
