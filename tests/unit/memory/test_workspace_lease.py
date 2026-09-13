import subprocess
import sys

import pytest

from corki.memory.workspace_lease import MemoryWorkspaceBusy, WorkspaceLeases


def test_workspace_lock_crosses_process_boundary_and_releases(tmp_path):
    root = tmp_path / "memories"
    program = """
import sys
from pathlib import Path
from corki.memory.workspace_lease import MemoryWorkspaceBusy, WorkspaceLeases
try:
    lease = WorkspaceLeases((Path(sys.argv[1]),))
except MemoryWorkspaceBusy:
    print('busy')
else:
    lease.close()
    print('free')
"""

    def check():
        return subprocess.check_output(
            [sys.executable, "-c", program, str(root)],
            text=True,
            timeout=10,
        ).strip()

    owned = WorkspaceLeases((root,))
    try:
        assert check() == "busy"
    finally:
        owned.close()
    assert check() == "free"


def test_partial_acquisition_releases_earlier_roots(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    owned = WorkspaceLeases((second,))
    try:
        with pytest.raises(MemoryWorkspaceBusy):
            WorkspaceLeases((first, second))
        WorkspaceLeases((first,)).close()
    finally:
        owned.close()
    WorkspaceLeases((first, second)).close()
