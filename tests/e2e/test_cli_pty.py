import os
import sys
from pathlib import Path

import pexpect


def test_corki_command_enters_interactive_composer_and_ctrl_c_exits(
    tmp_path: Path,
) -> None:
    environment = dict(os.environ)
    environment["CORKI_HOME"] = str(tmp_path / ".corki")
    environment.pop("CORKI_API_KEY", None)
    environment.pop("OPENAI_API_KEY", None)
    child = pexpect.spawn(
        sys.executable,
        ["-m", "corki"],
        cwd=tmp_path,
        env=environment,
        encoding="utf-8",
        timeout=10,
    )
    try:
        child.expect("Corki")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("c")
        child.expect("Session ended")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
