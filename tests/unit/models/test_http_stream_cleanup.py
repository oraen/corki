"""Secondary close failures cannot replace sampling errors or cancellation."""

import asyncio

import httpx
import pytest

from corki.models.http_stream import model_http_stream


@pytest.mark.parametrize("consumer", ["success", "read_error", "cancel"])
@pytest.mark.parametrize("close_cancelled", [False, True])
def test_response_close_error_priority_and_single_attempt(consumer, close_cancelled):
    async def scenario():
        closed, requests = [], []
        primary = {
            "success": None,
            "read_error": httpx.ReadError("primary read failure"),
            "cancel": asyncio.CancelledError("primary cancellation"),
        }[consumer]
        secondary = (
            asyncio.CancelledError("close cancellation")
            if close_cancelled
            else RuntimeError("secondary close failure")
        )

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"unused"

            async def aclose(self):
                closed.append(True)
                raise secondary

        def handle(request):
            requests.append(request)
            return httpx.Response(200, stream=Stream())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            expected = secondary if close_cancelled or primary is None else primary
            with pytest.raises(type(expected)) as caught:
                async with model_http_stream(
                    client,
                    "https://fixture.invalid/v1/responses",
                    headers={},
                    payload={},
                    max_retries=4,
                    base_seconds=0,
                ):
                    if primary is not None:
                        raise primary
            assert caught.value is expected
            assert closed == [True]
            assert len(requests) == 1

    asyncio.run(scenario())
