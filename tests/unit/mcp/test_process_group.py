import signal

import pytest

from corki.mcp import process_group as module


@pytest.mark.parametrize("result", [True, False, "denied"])
def test_termination_is_once_and_escalates_only_after_successful_group_signal(monkeypatch, result):
    calls, timers = [], []

    def send(group, sig):
        calls.append((group, sig))
        if result == "denied":
            raise PermissionError("fixture denied")
        return result

    class Timer:
        def __init__(self, seconds, callback, args):
            assert seconds == 2
            self.callback, self.args = callback, args
            timers.append(self)

        def start(self):
            assert self.daemon

    monkeypatch.setattr(module, "signal_owned_group", send)
    monkeypatch.setattr(module.threading, "Timer", Timer)
    group = module.MCPProcessGroup(12345)
    group.terminate()
    group.terminate()
    assert calls == [(12345, signal.SIGTERM)]
    assert len(timers) == (1 if result is True else 0)
    if timers:
        timers[0].callback(*timers[0].args)
        assert calls == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]


@pytest.mark.parametrize("group_id", [0, -1, True, 2**31, "123"])
def test_invalid_group_cannot_be_owned(group_id):
    with pytest.raises(ValueError):
        module.MCPProcessGroup(group_id)


def test_escalation_failure_is_logged_without_unhandled_thread_exception(monkeypatch, caplog):
    def denied(*args):
        raise PermissionError("fixture kill denied")

    monkeypatch.setattr(module, "signal_owned_group", denied)
    module._kill(12345, denied)
    assert "Failed to kill MCP process group 12345" in caplog.text
