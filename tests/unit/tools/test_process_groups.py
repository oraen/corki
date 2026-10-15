import os
import signal
import sys
from types import SimpleNamespace

import pytest

from corki.tools.builtin import process_groups as groups


@pytest.mark.parametrize("case", ["normal", "moved", "gone", "partial", "denied", "empty"])
def test_macos_fallback_rechecks_exact_group_and_preserves_errors(monkeypatch, case):
    calls = []
    monkeypatch.setattr(groups, "sys", SimpleNamespace(platform="darwin"))

    def denied_group(group, sig):
        assert group == 9 and sig == signal.SIGKILL
        raise PermissionError("group denied")

    def current_group(pid):
        if case == "gone":
            raise ProcessLookupError
        return 88 if case == "moved" else 9

    def send(pid, sig):
        calls.append(pid)
        if case == "denied" or (case == "partial" and pid == 9):
            raise PermissionError("member denied")

    monkeypatch.setattr(groups.os, "killpg", denied_group)
    monkeypatch.setattr(groups.os, "getpgid", current_group)
    monkeypatch.setattr(groups.os, "kill", send)
    monkeypatch.setattr(
        groups, "_group_members", lambda group: () if case == "empty" else (9, 12, 0, -1)
    )
    if case == "denied":
        with pytest.raises(PermissionError, match="member denied"):
            groups.signal_owned_group(9, signal.SIGKILL)
    else:
        assert groups.signal_owned_group(9, signal.SIGKILL) is (case in {"normal", "partial"})
    assert calls == ([12, 9] if case in {"normal", "partial", "denied"} else [])


@pytest.mark.parametrize("failure", [ProcessLookupError, PermissionError])
def test_non_macos_never_enumerates_group_members(monkeypatch, failure):
    monkeypatch.setattr(groups, "sys", SimpleNamespace(platform="linux"))

    def fail(*args):
        raise failure

    monkeypatch.setattr(groups.os, "killpg", fail)
    monkeypatch.setattr(groups, "_group_members", lambda _: pytest.fail("unexpected enumeration"))
    if failure is PermissionError:
        with pytest.raises(PermissionError):
            groups.signal_owned_group(9, signal.SIGTERM)
    else:
        assert groups.signal_owned_group(9, signal.SIGTERM) is False


@pytest.mark.parametrize("group", [-1, 0, True, 2**31])
def test_invalid_group_cannot_reach_signal_syscall(monkeypatch, group):
    monkeypatch.setattr(groups.os, "killpg", lambda *args: pytest.fail("unsafe signal target"))
    with pytest.raises(ValueError):
        groups.signal_owned_group(group, signal.SIGKILL)


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS libproc read-only check")
def test_native_group_member_adapter_finds_current_process():
    assert os.getpid() in groups._group_members(os.getpgrp())
