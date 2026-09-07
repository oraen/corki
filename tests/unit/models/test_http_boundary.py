"""HTTP status remains authoritative when an error body cannot be read."""

import asyncio

import httpx
import pytest

from corki.models import ModelError, ModelRequest, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem


@pytest.mark.parametrize(
    "status,broken,count,kind",
    [
        (302, False, 1, "protocol"),
        (429, True, 1, "retry_limit"),
        (503, True, 5, "server"),
        (503, "long_error", 5, "server_overloaded"),
    ],
)
def test_non_success_status_cannot_become_completion_or_transport_error(
    status, broken, count, kind
):
    async def scenario():
        requests, responses = [], []

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"error prefix"
                raise httpx.ReadError("failed to read error body")

        def handle(request):
            requests.append(request)
            if broken == "long_error":
                response = httpx.Response(
                    status,
                    json={"error": {"message": "x" * 10000, "code": "server_is_overloaded"}},
                )
                responses.append(response)
                return response
            response = (
                httpx.Response(status, stream=Body())
                if broken
                else httpx.Response(
                    status, text='data: {"type":"response.completed","response":{"id":"fake"}}\n\n'
                )
            )
            responses.append(response)
            return response

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
            retry_base_seconds=0.001,
        )
        request = ModelRequest(
            "fixture",
            "system",
            (),
            (UserMessageItem("run", new_turn_id()),),
            (),
            harness_managed_retries=True,
        )
        try:
            with pytest.raises(ModelError) as caught:
                async for _ in model.stream(request):
                    pytest.fail("an HTTP error cannot emit model output")
            assert (caught.value.kind.value, caught.value.status_code) == (kind, status)
            assert len(str(caught.value)) <= 4050
            assert len(requests) == count
            assert all(response.is_closed for response in responses)
        finally:
            await model.aclose()
            await client.aclose()

    asyncio.run(scenario())
