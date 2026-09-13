import os
import sys
from pathlib import Path

import pexpect
import pytest


@pytest.mark.parametrize("columns", [40, 100])
@pytest.mark.parametrize("exit_key", ["c", "d"])
def test_corki_command_local_status_restores_composer_and_exits(
    tmp_path: Path,
    columns: int,
    exit_key: str,
) -> None:
    environment = dict(os.environ)
    environment["CORKI_HOME"] = str(tmp_path / ".corki")
    environment["TERM"] = "xterm-256color"
    environment["PROMPT_TOOLKIT_NO_CPR"] = "1"
    environment.pop("CORKI_API_KEY", None)
    environment.pop("OPENAI_API_KEY", None)
    child = pexpect.spawn(
        sys.executable,
        ["-m", "corki"],
        cwd=tmp_path,
        env=environment,
        encoding="utf-8",
        timeout=10,
        dimensions=(30, columns),
    )
    try:
        child.expect("Corki")
        child.expect("Ask Corki to do anything")
        child.send("/status\r")
        child.expect("Model:")
        child.expect("Directory:")
        child.expect("Corki home:")
        child.expect("Ask Corki to do anything")
        child.sendcontrol(exit_key)
        child.expect("Session ended")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
