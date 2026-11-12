"""Streaming aiohttp transport with provider-neutral response accessors."""

import codecs
import re
from contextlib import asynccontextmanager


class Response:
    def __init__(self, response):
        self.response = response
        self.status_code = response.status
        self.headers = response.headers

    async def aiter_bytes(self):
        async for chunk in self.response.content.iter_any():
            yield chunk

    async def aiter_lines(self):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        async for chunk in self.aiter_bytes():
            pending += decoder.decode(chunk)
            # Hold a trailing CR so a split CRLF remains one line boundary.
            end = len(pending) - 1 if pending.endswith("\r") else len(pending)
            lines = re.split(r"\r\n|\r|\n", pending[:end])
            pending = lines.pop() + pending[end:]
            for line in lines:
                if len(line) > 32 * 1024 * 1024:
                    raise ValueError("Upstream event exceeds buffering limit")
                yield line
            if len(pending) > 32 * 1024 * 1024:
                raise ValueError("Upstream event exceeds buffering limit")
        pending += decoder.decode(b"", final=True)
        if pending:
            for line in re.split(r"\r\n|\r|\n", pending):
                yield line


class AiohttpClient:
    def __init__(self, session):
        self.session = session

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        async with self.session.request(method, url, allow_redirects=False, **kwargs) as response:
            yield Response(response)
