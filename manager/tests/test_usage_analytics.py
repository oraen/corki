"""Usage projections: local dates, attribution, reset-safe ownership and run totals."""

import importlib.util
import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "usage_analytics_test", Path(__file__).parents[1] / "usage_analytics.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def usage(tmp_path):
    home = tmp_path / "home"
    for folder in ["sessions", "cron", "gateway"]:
        (home / folder).mkdir(parents=True)
    with closing(sqlite3.connect(home / "sessions/corki.db")) as db, db:
        db.executescript("""CREATE TABLE threads(id TEXT PRIMARY KEY, source TEXT);
        CREATE TABLE model_steps(thread_id TEXT,input_tokens INTEGER,output_tokens INTEGER,metadata_json TEXT,created_at TEXT);
        CREATE INDEX model_steps_created_jd ON model_steps(julianday(created_at));
        CREATE INDEX model_steps_thread_usage ON model_steps(thread_id,input_tokens,output_tokens);""")
        for thread, source in [
            ("chat", "cli"),
            ("old-chat", "cli"),
            ("bot-old", '{"custom":"gateway"}'),
            ("bot-new", '{"custom":"gateway"}'),
            ("run-a", '{"custom":"cron"}'),
            ("run-b", "vscode"),
        ]:
            db.execute("INSERT INTO threads VALUES (?,?)", (thread, source))
        for thread, provider, tokens, day in [
            ("chat", "alpha", 100, "2026-10-03"),
            ("chat", "beta", 200, "2026-11-01"),
            ("old-chat", None, 50, "2026-11-01"),
            ("bot-old", "alpha", 300, "2026-11-01"),
            ("bot-new", "beta", 400, "2026-11-01"),
            ("run-a", "alpha", 500, "2026-11-01"),
            ("run-a", "alpha", 100, "2026-11-01"),
            ("run-b", "beta", 600, "2026-11-01"),
            ("chat", "alpha", 9999, "2026-10-02"),
            ("chat", "alpha", 9999, "2026-11-02"),
        ]:
            metadata = {"_corki_usage": {"provider": provider}} if provider else {"model": "shared"}
            db.execute(
                "INSERT INTO model_steps VALUES (?,?,?, ?,datetime(?,'utc'))",
                (thread, tokens, 10, json.dumps(metadata), day + " 12:00:00"),
            )
    with closing(sqlite3.connect(home / "gateway/gateway.db")) as db, db:
        db.executescript("""CREATE TABLE connections(id TEXT PRIMARY KEY,type_id TEXT);
        CREATE TABLE user_usage_threads(thread TEXT PRIMARY KEY,channel TEXT,sender TEXT);
        INSERT INTO connections VALUES ('account','dingtalk');
        INSERT INTO user_usage_threads VALUES ('bot-old','account','sender'),('bot-new','account','sender');""")
    with closing(sqlite3.connect(home / "cron/cron.sqlite")) as db, db:
        db.executescript("""CREATE TABLE runs(id TEXT PRIMARY KEY,job_id TEXT,session_id TEXT);
        INSERT INTO runs VALUES ('run-a','job','run-a'),('run-b','job',NULL),('duplicate-reference','job','run-a'),('empty','other','empty');""")
    return module.UsageAnalytics(home)


def test_dashboard_same_period_provider_switch_types_and_unknown(usage):
    result = usage.dashboard(date(2026, 11, 1))
    assert len(result["days"]) == 30
    assert result["days"][0] == dict(day="2026-10-03", input=100, output=10, steps=1)
    assert result["days"][1]["steps"] == 0
    assert {r["name"]: r["tokens"] for r in result["providers"]} == {
        "alpha": 1040,
        "beta": 1230,
        "未记录供应商": 60,
    }
    assert {r["name"]: r["tokens"] for r in result["types"]} == {
        "会话": 380,
        "钉钉": 720,
        "定时任务": 1230,
    }
    total = sum(r["input"] + r["output"] for r in result["days"])
    assert (
        total
        == sum(r["tokens"] for r in result["providers"])
        == sum(r["tokens"] for r in result["types"])
    )


def test_cron_cumulative_unique_threads_and_single_run(usage):
    jobs = usage.cron({"jobs": [{"id": "job"}, {"id": "other"}]}, jobs=True)["jobs"]
    assert [r["token_usage"] for r in jobs] == [1230, 0]
    runs = usage.cron({"records": [{"id": "run-a"}, {"id": "run-b"}, {"id": "empty"}]})["records"]
    assert [r["token_usage"] for r in runs] == [620, 610, 0]


def test_missing_home_and_missing_gateway_schema(tmp_path, usage):
    assert module.UsageAnalytics(tmp_path).dashboard(date(2026, 11, 1))["providers"] == []
    with closing(sqlite3.connect(usage.home / "gateway/gateway.db")) as db, db:
        db.execute("DROP TABLE user_usage_threads")
    assert sum(r["tokens"] for r in usage.dashboard(date(2026, 11, 1))["types"]) == 2330


def test_corrupt_usage_is_unknown_not_zero(usage):
    with closing(sqlite3.connect(usage.database)) as db, db:
        db.execute("DROP TABLE model_steps")
    assert usage.cron({"jobs": [{"id": "job"}]}, jobs=True)["jobs"][0]["token_usage"] is None


def test_queries_use_range_and_thread_indexes(usage, monkeypatch):
    sql = []
    original = usage.connect

    def connect():
        db = original()
        db.set_trace_callback(sql.append)
        return db

    monkeypatch.setattr(usage, "connect", connect)
    usage.dashboard(date(2026, 11, 1))
    usage.cron({"jobs": [{"id": "job"}]}, jobs=True)
    with closing(original()) as db:
        module.UsageAnalytics.attach(
            db, usage.home / "gateway/gateway.db", "gateway_usage", ["connections"]
        )
        module.UsageAnalytics.attach(db, usage.home / "cron/cron.sqlite", "cron_usage", ["runs"])
        queries = [s for s in sql if "FROM model_steps s LEFT JOIN" in s or "WITH owners AS" in s]
        plans = [
            "\n".join(row["detail"] for row in db.execute("EXPLAIN QUERY PLAN " + query))
            for query in queries
        ]
    assert "model_steps_created_jd" in plans[0]
    assert "COVERING INDEX model_steps_thread_usage" in plans[1]
