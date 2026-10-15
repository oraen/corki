"""Failed compaction reports actual usage without publishing a summary."""

import asyncio
from types import SimpleNamespace

import pytest

from corki.context.window import ContextWindowManager
from corki.models.base import ModelError, ModelErrorKind
from corki.models.types import ModelUsage


def test_incomplete_compaction_reports_usage_and_keeps_terminal_error():
    async def scenario():
        usage = ModelUsage(input_tokens=10, output_tokens=3, total_tokens=13)
        seen = []

        class Model:
            async def stream(self, _request):
                raise ModelError(
                    "Incomplete response returned, reason: max_output_tokens",
                    kind=ModelErrorKind.OUTPUT_LIMIT,
                    usage=usage,
                )
                yield  # Make this an async stream.

        async def record(value):
            seen.append(value)

        manager = SimpleNamespace(_model=Model())
        with pytest.raises(ModelError, match="max_output_tokens"):
            await ContextWindowManager._summary_attempt(manager, object(), record)
        assert seen == [usage]

    asyncio.run(scenario())
