"""Scheduling contracts: calendar semantics, atomic admission and crash recovery."""

import asyncio
import concurrent.futures
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from corki.config.settings import CorkiSettings
from corki.cron.locking import FileLock
from corki.cron.schedule import following, parse
from corki.cron.service import CronService
from corki.cron.store import CronStore
from corki.cron.worker import supervise
from corki.protocol.events import TurnCompleted, TurnFailed, UserInputRequested


def values(**overrides):
    return dict(
        name="Daily note",
        prompt="Summarize the supplied notes.",
        schedule="every 2h",
        timezone="Asia/Shanghai",
        **overrides,
    )


def test_schedule_semantics_and_dst():
    now = datetime(2026, 3, 7, 9, tzinfo=ZoneInfo("America/New_York")).timestamp()
    interval = parse("24h", "America/New_York", now)
    assert following(interval, now) - now == 86400
    assert parse("in 30m", "UTC", now)["at"] == now + 1800
    cron = parse("0 9 * * *", "America/New_York", now)
    assert following(cron, now) - now == 23 * 3600
    assert datetime.fromtimestamp(following(cron, now), ZoneInfo("America/New_York")).hour == 9
    for bad in ("0m", "x", "0 0 31 2 *", "2020-01-01T09:00", "* * * * * *"):
        with pytest.raises(ValueError):
            parse(bad, "UTC", now)
    with pytest.raises(ValueError):
        parse("1h", "Invalid/Timezone", now)
    with pytest.raises(ValueError):
        parse("2026-03-08T02:30", "America/New_York", now)
    # Fall-back computations must remain strictly future, never a tight loop.
    fold = datetime(2026, 11, 1, 1, 45, fold=1, tzinfo=ZoneInfo("America/New_York")).timestamp()
    assert following(parse("30 1 * * *", "America/New_York", fold), fold) > fold


def test_claim_atomic_concurrency_and_catchup(tmp_path):
    store = CronStore(tmp_path)
    identity = store.save(values(), now=100)
    assert store.claim(now=100) is None
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        claims = list(pool.map(lambda _: store.claim(now=100000), range(8)))
    attempts = [c for c in claims if c]
    assert len(attempts) == 1
    row = store.run(attempts[0])
    assert row["scheduled"] == 7300
    assert store.list()["jobs"][0]["next_run"] > 100000
    assert store.start_run(row["id"])
    assert not store.start_run(row["id"])
    with pytest.raises(ValueError, match="已在执行"):
        store.claim(identity)
    store.finish(row["id"], "completed", "done")
    store.finish(row["id"], "failed", error="late failure")
    assert store.run(row["id"])["status"] == "completed"
    assert store.claim(now=100000) is None


def test_versions_pause_once_manual_and_history_retention(tmp_path):
    store = CronStore(tmp_path)
    now = time.time()
    v = values()
    v["schedule"] = "in 1m"
    identity = store.save(v, now=now)
    store.change(identity, "pause", 1)
    assert store.claim(now=now + 100) is None
    with pytest.raises(ValueError, match="更新"):
        store.change(identity, "resume", 1)
    store.change(identity, "resume", 2)
    run = store.claim(now=now + 100)
    assert not store.list()["jobs"][0]["enabled"]
    store.finish(run, "completed")
    assert store.claim(now=now + 1000) is None
    for _ in range(52):
        run = store.claim(identity)
        store.finish(run, "completed", "manual")
    assert len(store.history(identity)) == 50
    job = store.list()["jobs"][0]
    assert job["next_run"] is None
    store.change(identity, "delete", job["version"])
    assert not store.history(identity)


def test_recovery_proves_worker_absent_and_never_replays(tmp_path):
    service = CronService(tmp_path, launch=lambda run: None)
    store = service.store
    identity = store.save(values())
    run = store.claim(identity)
    lock = FileLock(store.root / "locks" / (run + ".lock"))
    assert lock.acquire()
    service.recover(now=time.time() + 100)
    assert store.run(run)["status"] == "claimed"
    lock.close()
    service.recover(now=time.time() + 100)
    assert store.run(run)["status"] == "unknown"
    job = store.list()["jobs"][0]
    assert not job["enabled"] and job["pause_reason"]
    assert store.claim(now=time.time() + 10000) is None
    assert not store.start_run(run)  # Late worker must not execute a retired admission.


def test_max_workers_and_failed_launch(tmp_path):
    def fail(_):
        raise OSError("fixture failure")

    service = CronService(tmp_path, launch=fail)
    ids = [service.store.save(values()) for _ in range(3)]
    run = service.run_now(ids[0])
    assert service.store.run(run)["status"] == "failed"
    service.store.claim(ids[0])
    service.store.claim(ids[1])
    with pytest.raises(ValueError, match="两个"):
        service.store.claim(ids[2])


@pytest.mark.parametrize("outcome", ["completed", "failed", "timeout", "input", "cancel"])
def test_worker_runtime_outcomes_and_cleanup(tmp_path, outcome):
    (tmp_path / "settings.json").write_text(
        '{"providers":{"fixture":{"api_key":"fake-secret-must-not-escape"}}}'
    )
    store = CronStore(tmp_path)
    job = store.save(values())
    identity = store.claim(job)
    store.start_run(identity)
    seen = {}

    class Runtime:
        closed = False

        async def stream(self, prompt):
            seen["prompt"] = prompt
            if outcome == "timeout":
                await asyncio.sleep(1)
            if outcome == "cancel":
                store.change(job, "stop", 1)
                await asyncio.sleep(10)
            if outcome == "input":
                yield UserInputRequested(identity, "turn", "input", ())
            if outcome == "failed":
                yield TurnFailed(identity, "turn", "fake-secret-must-not-escape")
            else:
                yield TurnCompleted(identity, "turn", "Result from the fresh session")

        async def aclose(self):
            self.closed = True

    runtime = Runtime()

    async def factory(**kwargs):
        seen.update(kwargs)
        return runtime

    def settings_factory(path, **kwargs):
        return CorkiSettings(working_directory=path)

    async def scenario():
        await supervise(
            store,
            identity,
            factory=factory,
            settings_factory=settings_factory,
            idle_timeout=0.05 if outcome == "timeout" else 5,
        )

    asyncio.run(scenario())
    row = store.run(identity)
    assert row["status"] == {"completed": "completed", "cancel": "cancelled"}.get(outcome, "failed")
    assert runtime.closed
    assert "fake-secret" not in row["error"]
    assert "fake-secret" not in row["error_log"]
    if outcome == "failed":
        assert "TurnFailed" in row["error_log"] and "[已隐藏]" in row["error_log"]
    if outcome == "timeout":
        assert "TimeoutError" in row["error_log"]
    assert seen["thread_id"] == identity
    assert seen["home_path"] == tmp_path
    assert not seen["settings"].model_unbounded_connection_retries
    assert "unattended" in seen["prompt"]


def test_service_tick_and_lock_failover(tmp_path):
    launches = []
    first = CronService(tmp_path, launch=launches.append)
    second = CronService(tmp_path, launch=launches.append)
    first.store.save(values(), now=time.time() - 7201)
    assert first.lock.acquire()
    assert not second.lock.acquire()
    first.tick()
    assert len(launches) == 1
    first.lock.close()
    assert second.lock.acquire()
    try:
        second.tick()
        assert len(launches) == 1
        assert second.store.list()["online"]
    finally:
        second.lock.close()


def test_long_run_does_not_queue_missed_occurrences(tmp_path, monkeypatch):
    store = CronStore(tmp_path)
    job = store.save(values(), now=0)
    run = store.claim(now=7200)
    monkeypatch.setattr(time, "time", lambda: 50000)
    store.finish(run, "completed")
    assert store.list()["jobs"][0]["next_run"] > 50000
    assert store.claim() is None
    # Manual invocation does not move the recurring schedule.
    before = store.list()["jobs"][0]["next_run"]
    manual = store.claim(job)
    store.finish(manual, "completed")
    assert store.list()["jobs"][0]["next_run"] == before


def test_cancelled_admission_never_opens_model(tmp_path):
    store = CronStore(tmp_path)
    job = store.save(values())
    run = store.claim(job)
    store.change(job, "stop", 1)
    store.start_run(run)

    async def forbidden(**kwargs):
        pytest.fail("cancelled admission constructed a model")

    asyncio.run(supervise(store, run, factory=forbidden))
    assert store.run(run)["status"] == "cancelled"


@pytest.mark.parametrize(
    "expression",
    [
        "2030-12-01 09:30",
        "2030/12/1 9:30:00",
        "2030.12.1 09:30",
        "2030年12月1日 9时30分",
        "2030年12月1日 9时30分00秒",
        "2030-12-01T09:30:00+08:00",
        "2030-12-01T01:30:00Z",
        "20301201093000",
        str(int(datetime(2030, 12, 1, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())),
        str(int(datetime(2030, 12, 1, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)),
    ],
)
def test_once_date_formats(expression):
    now = datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")).timestamp()
    expected = datetime(2030, 12, 1, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    assert parse(expression, "Asia/Shanghai", now, kind="once") == {"kind": "once", "at": expected}
    assert parse(expression, "Asia/Shanghai", now)["at"] == expected


def test_once_relative_dates_units_and_validation(tmp_path):
    now = datetime(2030, 12, 1, 8, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    for expression in ("30m", "in 30m", "30分钟后"):
        assert parse(expression, "Asia/Shanghai", now, kind="once")["at"] == now + 1800
    assert parse("今天 09:00", "Asia/Shanghai", now)["at"] == now + 3600
    assert parse("明天 09:00", "Asia/Shanghai", now)["at"] == now + 90000
    assert parse("后天 09:00", "Asia/Shanghai", now)["at"] == now + 176400
    assert parse("2030/12/2", "Asia/Shanghai", now)["at"] == now + 16 * 3600
    seconds = str(int(now + 60))
    assert parse(seconds + ".125", "Pacific/Auckland", now)["at"] == now + 60.125
    assert parse(str(int((now + 60) * 1000) + 125), "UTC", now)["at"] == now + 60.125
    for bad in (
        "2030/02/30 09:00",
        "12/01/2030 09:00",
        "2030-12-02 25:00",
        "NaN",
        "99999999999999999999",
        "今天 07:59",
        str(int(now)),
        "every 2h",
    ):
        with pytest.raises(ValueError):
            parse(bad, "Asia/Shanghai", now, kind="once")
    store = CronStore(tmp_path)
    v = values()
    v.update(schedule="30m", schedule_kind="interval")
    identity = store.save(v, now=now)
    v["schedule_kind"] = "once"
    store.save(v, identity, version=1, now=now)
    job = store.list()["jobs"][0]
    assert job["normalized"]["kind"] == "once"
    assert job["next_run"] == now + 1800
    store.save(v, identity, version=2, now=now + 10)
    assert store.list()["jobs"][0]["next_run"] == now + 1800  # Editing doesn't reset a delay.


def test_optional_description_roundtrip(tmp_path):
    store = CronStore(tmp_path)
    v = values()
    identity = store.save(v)
    assert store.list()["jobs"][0]["description"] == ""
    v["description"] = "  A short card description  "
    store.save(v, identity, version=1)
    job = store.list()["jobs"][0]
    assert job["description"] == "A short card description"
    run = store.claim(identity)
    definition = store.run(run)["definition"]
    assert definition["description"] == job["description"]
    assert definition["prompt"] == v["prompt"]
    store.finish(run, "completed")
    v["description"] = ""
    store.save(v, identity, version=2)
    assert store.list()["jobs"][0]["description"] == ""
    for invalid in (None, 12, "x" * 501):
        v["description"] = invalid
        with pytest.raises(ValueError, match="任务描述"):
            store.save(v)


def test_global_records_pagination_and_snapshot_names(tmp_path):
    store = CronStore(tmp_path)
    v = values()
    first = store.save(v)
    run = store.claim(first)
    store.finish(run, "completed", output="Result body should only be loaded in details")
    v["name"] = "Renamed task"
    store.save(v, first, version=1)
    second = store.save(values())
    for _ in range(3):
        attempt = store.claim(second)
        store.finish(attempt, "failed", error="Fixture failure")
    page = store.records(limit=2)
    assert page["total"] == 4 and len(page["records"]) == 2
    next_page = store.records(limit=2, offset=2)
    ids = [r["id"] for r in page["records"] + next_page["records"]]
    assert len(set(ids)) == 4 and ids[-1] == run
    oldest = next_page["records"][-1]
    assert oldest["name"] == "Daily note"
    assert "output" not in oldest and "definition" not in oldest
    assert store.records(query="Renamed")["total"] == 0
    assert store.records(query="DAILY")["total"] == 4
    assert store.records(offset=20)["records"] == []
    for invalid in (-1, "a", True):
        with pytest.raises(ValueError):
            store.records(offset=invalid)


def test_saved_model_validation_and_provider_alias(tmp_path):
    import json

    from corki.cron.models import validate_model

    store = CronStore(tmp_path)
    config = {
        "default": {"provider": "first", "model": "a"},
        "providers": {"first": {"models": {"a": {}}}, "second": {"models": {"b": {}, "c": {}}}},
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(config))
    v = values()
    v.update(provider="second", model="c")
    identity = store.save(v)
    saved = store.list()["jobs"][0]
    assert (saved["provider"], saved["model"]) == ("second", "c")
    config["default"] = {"provider": "first", "model": "a"}
    config["providers"]["renamed"] = config["providers"].pop("second")
    config["providers"]["renamed"]["aliases"] = ["second"]
    path.write_text(json.dumps(config))
    assert validate_model(tmp_path, "second", "c") == ("second", "c")
    run = store.claim(identity)
    assert store.run(run)["definition"]["model"] == "c"
    store.finish(run, "completed")
    for provider, model in [("", ""), ("first", "c"), ("missing", "a"), ("second", None)]:
        with pytest.raises(ValueError):
            store.save({**v, "provider": provider, "model": model})
    config["providers"]["renamed"]["models"].pop("c")
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="不可用"):
        validate_model(tmp_path, "second", "c")


def test_record_names_multiselect_exact_filter(tmp_path):
    store = CronStore(tmp_path)
    for name in ["日报", "日报备份", "Report ' & notes"]:
        v = values()
        v["name"] = name
        job = store.save(v)
        for _ in range(2):
            run = store.claim(job)
            store.finish(run, "completed")
    result = store.records(names=["日报", "Report ' & notes"], limit=3)
    assert result["total"] == 4 and len(result["records"]) == 3
    assert set(r["name"] for r in result["records"]) == {"日报", "Report ' & notes"}
    assert len(store.records(names=["日报", "Report ' & notes"], offset=3)["records"]) == 1
    assert set(result["task_names"]) == {"日报", "日报备份", "Report ' & notes"}
    assert store.records(names=["不存在"])["total"] == 0
    assert store.records(names=[])["total"] == 6
    for bad in ("日报", [None], [""], ["x"] * 201):
        with pytest.raises(ValueError):
            store.records(names=bad)


def test_unexpected_worker_failure_keeps_traceback(tmp_path):
    store = CronStore(tmp_path)
    identity = store.claim(store.save(values()))
    store.start_run(identity)

    async def factory(**kwargs):
        raise RuntimeError("fixture connection refused")

    asyncio.run(
        supervise(
            store,
            identity,
            factory=factory,
            settings_factory=lambda path, **kwargs: CorkiSettings(working_directory=path),
        )
    )
    row = store.run(identity)
    assert row["status"] == "failed"
    assert "Traceback" in row["error_log"]
    assert "RuntimeError: fixture connection refused" in row["error_log"]
    with store.transaction() as db:
        db.execute("ALTER TABLE runs DROP COLUMN error_log")
    # Older databases gain the new column without losing existing records.
    assert store.run(identity)["error_log"] == ""
    assert store.run(identity)["status"] == "failed"
