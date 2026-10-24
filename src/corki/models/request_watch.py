"""Optional host observation of real model I/O; no default timeout/retry policy."""

from contextvars import ContextVar

request_observer = ContextVar("corki_model_request_observer", default=None)


def request_started():
    if observer := request_observer.get():
        observer.begin()


async def request_wait(awaitable):
    observer = request_observer.get()
    return await observer.wait(awaitable) if observer else await awaitable


async def request_failed(error):
    if observer := request_observer.get():
        await observer.failure(error)


async def request_completed():
    if observer := request_observer.get():
        await observer.complete()


async def response_lines(response):
    lines = response.aiter_lines()
    try:
        while True:
            try:
                line = await request_wait(anext(lines))
            except StopAsyncIteration:
                return
            if line and (observer := request_observer.get()):
                await observer.activity()
            yield line
    finally:
        await lines.aclose()
