import importlib.util
import sqlite3
import threading
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "manager_health", Path(__file__).parents[1] / "health.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def checks(tmp_path, monkeypatch):
    monkeypatch.setenv("CORKI_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text('{"agent":{"memories_enabled":false}}')
    return module.HealthChecks(tmp_path, tmp_path)


def finish(checks):
    checks.worker.join(10)
    assert not checks.worker.is_alive()
    return checks.snapshot()


def test_real_sqlite_and_config_are_checked_without_writes(checks):
    directory = checks.home / "sessions"
    directory.mkdir()
    database = directory / "corki.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE sample(value TEXT)")
        db.execute("INSERT INTO sample VALUES ('retained')")
    before = database.read_bytes()
    config = (checks.home / "settings.json").read_bytes()
    checks.start()
    result = finish(checks)
    assert not result["running"]
    assert all(item["status"] == "normal" for item in result["items"]), result
    assert database.read_bytes() == before
    assert (checks.home / "settings.json").read_bytes() == config
    assert not (directory / "checkpoints.db").exists()
    assert not (checks.home / "app/desktop.db").exists()


@pytest.mark.parametrize("content", ["", '{"api_key":"private-test-secret",',
                                     '{"agent":{"context_window_tokens":"wrong-type"}}'])
def test_bad_config_does_not_hide_database_results_or_expose_content(checks, content):
    (checks.home / "settings.json").write_text(content)
    checks.start()
    result = finish(checks)
    assert result["items"][1]["status"] == "abnormal", result
    assert all(item["status"] == "normal" for item in result["items"][2:])
    assert "private-test-secret" not in str(result)


def test_corrupt_database_is_reported_and_other_checks_continue(checks):
    (checks.home / "sessions").mkdir()
    (checks.home / "sessions/corki.db").write_bytes(b"not a sqlite database")
    checks.start()
    result = finish(checks)
    assert result["items"][2]["status"] == "abnormal"
    assert result["items"][-1]["status"] == "normal"


def test_missing_config_is_reported(checks):
    (checks.home / "settings.json").unlink()
    checks.start()
    result = finish(checks)
    assert result["items"][0]["status"] == "abnormal"


def test_concurrent_start_joins_one_job_and_snapshots_are_isolated(checks):
    entered, release = threading.Event(), threading.Event()

    def slow():
        entered.set()
        assert release.wait(5)
        return "checked"

    checks._syntax = slow
    first = checks.start()
    assert entered.wait(2)
    try:
        second = checks.start()
        assert first["id"] == second["id"]
        assert second["items"][0]["status"] == "running"
        second["items"].clear()
        assert len(checks.snapshot()["items"]) == 5
    finally:
        release.set()
        finish(checks)
    assert checks.start()["id"] != first["id"]
    finish(checks)
