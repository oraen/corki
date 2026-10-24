"""Keep POSIX terminal harnesses distinct from native Windows acceptance."""

import os
from pathlib import Path

import pytest


def pytest_collection_modifyitems(items):
    if os.name == "posix":
        return
    marker = pytest.mark.skip(
        reason="POSIX pexpect PTY harness; native Windows ConPTY acceptance is separate"
    )
    for item in items:
        if item.path.parent == Path(__file__).parent and item.path.name.endswith("_pty.py"):
            item.add_marker(marker)
