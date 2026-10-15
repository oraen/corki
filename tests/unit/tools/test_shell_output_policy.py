import asyncio
import shlex
import sys
from types import SimpleNamespace

import pytest

from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import ToolCall
from corki.protocol.truncation import TruncationPolicy
from corki.tools.base import ToolContext
from corki.tools.builtin.process import ProcessManager, _ProcessSession
from corki.tools.builtin.shell import ExecCommandTool, WriteStdinTool


def render(
    tmp_path,
    text,
    *,
    policy=None,
    requested=None,
    omitted=0,
    count=123,
    poll=False,
):
    policy = policy or TruncationPolicy("tokens", 10000)
    observation = SimpleNamespace(
        output=text,
        exit_code=0,
        session_id=None,
        timed_out=False,
        wall_time_seconds=1.25,
        chunk_id="abc123",
        original_token_count=count,
        output_omitted_bytes=omitted,
        terminal_info=None,
    )

    class Manager:
        async def execute(self, *args, **kwargs):
            assert "max_output_bytes" not in kwargs
            return observation

        async def write_stdin(self, *args, **kwargs):
            return observation

    tool = WriteStdinTool(Manager(), 0.1) if poll else ExecCommandTool(Manager(), 0.1, 2)
    args = {"session_id": "session"} if poll else {"cmd": "fixture"}
    if requested is not None:
        args["max_output_tokens"] = requested
    return asyncio.run(
        tool.execute(
            ToolCall(new_tool_call_id(), tool.spec.name, args),
            ToolContext(tmp_path, model_output_policy=policy),
        )
    )


@pytest.mark.parametrize("poll", [False, True])
def test_shell_explicit_budget_does_not_truncate_log(tmp_path, poll):
    raw = "token one token two token three token four token five"
    result = render(tmp_path, raw, requested=4, count=10, poll=poll)
    header = (
        "Chunk ID: abc123\nWall time: 1.2500 seconds\nProcess exited with code 0\n"
        "Original token count: 10\nOutput:\n"
    )
    assert result.display_content == header + raw
    assert result.content.startswith(header + "Warning: truncated output")
    assert result.content.endswith("token on…10 tokens truncated…ken five")
    assert result.code_mode_output.value["output"] == result.content[len(header) :]


@pytest.mark.parametrize(
    "policy,unit",
    [(TruncationPolicy("bytes", 200), "chars"), (TruncationPolicy("tokens", 50), "tokens")],
)
def test_shell_metadata_reserves_history_allowance(tmp_path, policy, unit):
    raw = "".join(f"{i}\n" for i in range(1, 151))
    result = render(tmp_path, raw, policy=policy)
    assert len(result.content.encode()) <= policy.history_allowance().byte_budget
    assert result.content.count(f"{unit} truncated") == 1
    assert "Total output lines: 150" in result.content
    assert "\n1\n2\n3\n" in result.content and result.content.endswith("149\n150\n")
    assert result.code_mode_output.value["output"] == raw


def test_shell_collection_omission_survives_model_truncation(tmp_path):
    marker = "... 123456 bytes omitted ..."
    raw = "HEAD-" + "a" * 100 + "\n" + marker + "\nTAIL-" + "z" * 100
    result = render(tmp_path, raw, requested=4, count=42000, omitted=123456)
    assert result.content.count(marker) == 1
    assert "Warning: truncated output (original token count: 42000)" in result.content
    assert result.display_content.endswith(raw)


def test_nested_explicit_budget_can_exceed_model_budget(tmp_path):
    raw = "HEAD" + "x" * 1000 + "TAIL"
    result = render(tmp_path, raw, policy=TruncationPolicy("tokens", 100), requested=1000)
    assert result.code_mode_output.value["output"] == raw
    assert len(result.content.encode()) <= 480 and "tokens truncated" in result.content
    assert result.display_content.endswith(raw)


def test_process_collection_preserves_head_and_tail_and_resets():
    session = _ProcessSession("fixture", None, 5)
    session.append(b"abc")
    session.append(b"defgh")
    assert session.take_output() == "ab\n... 3 bytes omitted ...\nfgh"
    session.append(b"next")
    assert session.take_output() == "next"


@pytest.mark.parametrize("limit", [1, 5, 64])
@pytest.mark.parametrize("chunk_size", [1, 7, 100])
def test_collection_result_is_independent_of_reader_chunking(limit, chunk_size):
    raw = b"0123456789" * 10
    session = _ProcessSession("fixture", None, limit)
    for start in range(0, len(raw), chunk_size):
        session.append(raw[start : start + chunk_size])
        assert len(session.head) + session.buffered_bytes <= limit
    head, tail = limit // 2, limit - limit // 2
    assert session.dropped_bytes == len(raw) - limit
    assert session.take_output() == (
        raw[:head].decode() + f"\n... {len(raw) - limit} bytes omitted ...\n" + raw[-tail:].decode()
    )


def test_real_process_collection_cap_reports_original_bytes(tmp_path):
    async def scenario():
        processes = ProcessManager()
        try:
            script = "import sys; sys.stdout.write('HEAD'+'x'*1100000+'TAIL')"
            observation = await processes.execute(
                shlex.join([sys.executable, "-c", script]),
                cwd=tmp_path,
                yield_seconds=2,
                timeout_seconds=5,
                login=False,
            )
            omitted = 1100008 - 1024 * 1024
            assert observation.output.startswith("HEAD") and observation.output.endswith("TAIL")
            assert observation.output.count(f"... {omitted} bytes omitted ...") == 1
            assert observation.output_omitted_bytes == omitted
            assert observation.original_token_count == 275002
            assert len(observation.output) < 1024 * 1024 + 100
            assert observation.exit_code == 0 and not processes._sessions
        finally:
            await processes.terminate_all()

    asyncio.run(scenario())


def test_observation_wall_time_is_per_call_not_process_age(monkeypatch):
    async def scenario():
        processes = ProcessManager()
        session = _ProcessSession("fixture", SimpleNamespace(returncode=0), 100, started_at=10)
        session.append(b"12345")

        async def terminate(_):
            pass

        monkeypatch.setattr(processes, "_terminate", terminate)
        monkeypatch.setattr("corki.tools.builtin.process.time.monotonic", lambda: 101)
        observation = await processes._observe(session, 100)
        assert observation.wall_time_seconds == 1
        assert observation.original_token_count == 2

    asyncio.run(scenario())
