"""Async HTTPX injection for existing synchronous protocol fixtures only."""

import httpx


class Stream(httpx.AsyncByteStream):
    def __init__(self, source):
        self.source = source

    async def __aiter__(self):
        for chunk in self.source:
            yield chunk

    async def aclose(self):
        self.source.close()


def mock_client(handler):
    async def respond(request):
        response = handler(request)
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=Stream(response.stream),
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))
