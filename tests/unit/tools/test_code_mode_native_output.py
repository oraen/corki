import asyncio
import json

import pytest

from corki.code_mode.service import CodeModeService
from corki.protocol.tools import TextContent
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
        service = CodeModeService(ToolRegistry())
        try:
            first = await service.execute(
                "fixture",
                "text('FIRST'); await yield_control(); text('0123456789'.repeat(4)); await new Promise(()=>{});",
                10000,
                1000,
            )
            cell_id = first.content.split("cell ID ")[1].splitlines()[0]
            # Let queued post-yield text reach the actor before terminating it.
            cell = service.cells[cell_id]
            for _ in range(100):
                if cell.output:
                    break
                await asyncio.sleep(0.01)
            second = await service.wait(cell_id, 0, 5, terminate=True)
            assert second.content.startswith("Script terminated") and not second.is_error
            assert "FIRST" not in second.content
            assert second.content.endswith("0123456789…5 tokens truncated…0123456789")
            assert not service.cells
        finally:
            await service.aclose()

    asyncio.run(scenario())
