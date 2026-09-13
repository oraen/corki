import asyncio

import httpx

from corki.models.http_stream import model_http_stream


def test_failed_http_attempt_does_not_seed_sse_state():
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request.headers.get("x-codex-turn-state"))
            return httpx.Response(
                503 if len(requests) == 1 else 200,
                headers={"x-codex-turn-state": "error-state" if len(requests) == 1 else "success"},
            )

        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client,
            model_http_stream(
                client,
                "https://fixture.invalid/responses",
                headers={},
                payload={},
                max_retries=1,
                base_seconds=0,
            ),
        ):
            pass
        assert requests == [None, None]

    asyncio.run(scenario())
