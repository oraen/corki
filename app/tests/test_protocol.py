"""Exercise actual stdio framing and failed parent pipes in disposable processes."""

import json
import os
import select
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

import pytest

BRIDGE = str(Path(__file__).resolve().parents[1] / "bridge")
SCRIPT = """
import asyncio, sys
sys.path.insert(0, sys.argv[1])
from protocol import Transport
if len(sys.argv) > 2:
    import protocol
    from types import SimpleNamespace
    protocol.os = SimpleNamespace(name="nt")
class Service:
    stopping = False
    async def dispatch(self, method, params):
        return params
    def safe_error(self, error):
        return str(error)
    async def close(self):
        pass
async def main():
    transport = Transport()
    await transport.serve(Service())
asyncio.run(main())
"""


def launch(thread_reader=False):
    return subprocess.Popen(
        [sys.executable, "-u", "-c", SCRIPT, BRIDGE, *(["thread-reader"] if thread_reader else [])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )


def read_frame(process):
    # No buffered reader: select must reflect every unread response byte.
    result = bytearray()
    while not result.endswith(b"\n"):
        assert select.select([process.stdout], [], [], 5)[0], "response timed out"
        byte = process.stdout.read(1)
        assert byte, process.stderr.read().decode()
        result.extend(byte)
    return json.loads(result)


@pytest.mark.skipif(os.name == "nt", reason="POSIX pipe readiness test")
def test_split_utf8_contiguous_frames_and_invalid_json():
    process = launch()
    try:
        frame = (
            json.dumps(
                {"jsonrpc": "2.0", "id": "one", "method": "echo", "params": {"text": "中文"}},
                ensure_ascii=False,
            ).encode()
            + b"\n"
        )
        split = frame.index("中".encode()) + 1
        process.stdin.write(frame[:split])
        process.stdin.write(frame[split:] + b"{invalid}\n" + frame.replace(b'"one"', b'"two"'))
        responses = [read_frame(process) for _ in range(3)]
        valid = {value["id"]: value for value in responses if value["id"]}
        assert valid["one"]["result"]["text"] == "中文"
        assert valid["two"]["result"]["text"] == "中文"
        assert next(v for v in responses if v["id"] is None)["error"]["code"] == -32700
        process.stdin.close()
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


@pytest.mark.parametrize("thread_reader", [False, True])
def test_closed_output_exits_even_when_parent_keeps_input_open(thread_reader):
    process = launch(thread_reader)
    try:
        process.stdout.close()
        process.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"echo"}\n')
        # stdin deliberately remains open: the reader must be cancellable when
        # the writer fails, and queue.join must not wait for a dead writer.
        assert process.wait(timeout=5) != 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdin.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX pipe readiness test")
def test_oversized_unterminated_frame_fails_without_waiting_for_eof():
    process = launch()
    try:
        with suppress(BrokenPipeError):
            process.stdin.write(b"x" * (2 * 1024 * 1024 + 1))
        assert process.wait(timeout=5) != 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdin.close()
