"""Incremental protocol parsers shared by synchronous tests and async transports."""

NEED_INPUT = object()


class Parser:
    def __init__(self, coroutine):
        self.coroutine = coroutine
        self.done = False
        if next(coroutine) is not NEED_INPUT:
            raise ValueError("Protocol parser did not request input")

    def feed(self, value):
        if self.done:
            return
        try:
            output = self.coroutine.send(value)
            while output is not NEED_INPUT:
                yield output
                output = next(self.coroutine)
        except StopIteration:
            self.done = True

    def close(self):
        self.coroutine.close()


def transform(coroutine, inputs):
    parser = Parser(coroutine)
    try:
        for value in inputs:
            yield from parser.feed(value)
            if parser.done:
                return
        yield from parser.feed(None)
    finally:
        parser.close()


async def atransform(coroutine, inputs):
    parser = Parser(coroutine)
    try:
        async for value in inputs:
            for output in parser.feed(value):
                yield output
            if parser.done:
                return
        for output in parser.feed(None):
            yield output
    finally:
        parser.close()
