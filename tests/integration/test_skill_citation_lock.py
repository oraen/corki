"""Standalone citation helpers: native locks, crash recovery, Unicode pipes."""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/grounded-citations/scripts"


def test_ledger_lock_timeout_does_not_steal_and_crash_releases(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    from _ledger_lock import LedgerLock

    ledger = tmp_path / "资料 ledger.json"
    ready = tmp_path / "ready"
    child = subprocess.Popen([
        sys.executable, "-c",
        "import sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
        "from _ledger_lock import LedgerLock;"
        "lock=LedgerLock(Path(sys.argv[2]));lock.__enter__();"
        "Path(sys.argv[3]).write_text('ready');time.sleep(120)",
        str(SCRIPTS), str(ledger), str(ready),
    ])
    import time

    import pytest
    try:
        deadline = time.monotonic() + 60
        while not ready.exists():
            assert child.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.05)
        with pytest.raises(TimeoutError), LedgerLock(ledger, timeout=0.1):
            pytest.fail("must not steal the live writer's lock")
        assert ledger.with_suffix(".json.lock").exists()
    finally:
        child.kill()
        child.wait(timeout=30)
    with LedgerLock(ledger, timeout=1):
        ledger.write_text("{}", encoding="utf-8")


def test_citation_cli_utf8_stdin_stdout(tmp_path):
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", str(SCRIPTS / "sources.py"), "ingest", "-"],
        input=json.dumps({"url": "https://example.invalid/资料", "title": "测试资料"},
                         ensure_ascii=False).encode("utf-8"),
        capture_output=True, timeout=60,
        env={**os.environ, "CORKI_HOME": str(tmp_path), "CORKI_CITATION_LEDGER": "",
             "PYTHONIOENCODING": "ascii"},
    )
    assert result.returncode == 0, result.stderr
    assert "资料" in result.stdout.decode("utf-8")
    ledger = json.loads((tmp_path / "cache/citations/ledger.json").read_text(encoding="utf-8"))
    assert ledger["sources"][0]["title"] == "测试资料"
