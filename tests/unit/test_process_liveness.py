"""Runs the host's actual process API, including on Windows CI/VM."""

import os
import subprocess
import sys

from corki.platforms.liveness import process_alive


def test_current_and_exited_processes():
    assert process_alive(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert process_alive(child.pid)
    finally:
        child.terminate()
        child.wait(timeout=10)
    assert not process_alive(child.pid)
    assert not process_alive(0)
    assert not process_alive(-1)
