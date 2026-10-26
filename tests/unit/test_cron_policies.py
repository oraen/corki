"""Overlap admissions and worker deadlines use isolated ledgers and fake runtimes."""

import asyncio
import concurrent.futures

import pytest

from corki.config.settings import CorkiSettings
from corki.cron.service import CronService
from corki.cron.store import CronStore
from corki.cron.worker import supervise
from corki.protocol.events import TurnCompleted


def values(**options):
    return dict(
        name="Policy fixture",
        prompt="No side effects",
        schedule="every 1h",
        timezone="UTC",
        **options,
    )


@pytest.mark.parametrize("policy", ["skip", "allow", "restart"])
def test_settings_roundtrip_preserved_on_legacy_update(tmp_path, policy):
    store = CronStore(tmp_path)
    job = store.save(values(overlap_policy=policy, timeout_seconds=123), now=0)
    row = store.list()["jobs"][0]
    assert row["overlap_policy"] == policy and row["timeout_seconds"] == 123
    store.save(values(), job, row["version"], now=1)
    row = store.list()["jobs"][0]
    assert row["overlap_policy"] == policy and row["timeout_seconds"] == 123


def test_invalid_policy_settings_rejected(tmp_path):
    with pytest.raises(ValueError):
        CronStore(tmp_path).save(values(overlap_policy="bad"))


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 0),
        ("", 0),
        ("  ", 0),
        (0, 0),
        (-1, 0),
        (True, 0),
        (1.5, 0),
        ("1.5", 0),
        ("abc", 0),
        ("10", 10),
        (" 90 ", 90),
        (123, 123),
    ],
)
def test_timeout_optional_input_normalization(tmp_path, value, expected):
    store = CronStore(tmp_path)
    store.save(values(timeout_seconds=value))
    assert store.list()["jobs"][0]["timeout_seconds"] == expected


def test_skip_due_occurrence_is_not_delayed_or_hidden_running(tmp_path):
    store = CronStore(tmp_path)
    job = store.save(values(), now=0)
    first = store.claim(now=3600)
    store.start_run(first)
    assert store.claim(now=7200) is None
    rows = store.history(job)
    assert rows[0]["status"] == "skipped"
    assert len(rows) == 2
    assert store.list()["jobs"][0]["active_run"]["id"] == first
    assert store.list()["jobs"][0]["next_run"] == 10800
    assert store.claim(now=7200) is None


def test_parallel_policy_admission_is_atomic_and_stop_cancels_every_run(tmp_path):
    store = CronStore(tmp_path)
    job = store.save(values(overlap_policy="allow"))

    def attempt(_):
        try:
            return store.claim(job)
        except ValueError:
            return None

    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        runs = list(filter(None, pool.map(attempt, range(6))))
    assert len(runs) == 2 and len(set(runs)) == 2
    for run in runs:
        assert store.start_run(run)
    store.change(job, "stop", 1)
    assert all(store.run(run)["cancel"] for run in runs)


def test_restart_waits_for_terminal_and_survives_new_service(tmp_path):
    service = CronService(tmp_path, launch=lambda _: None)
    store = service.store
    job = store.save(values(overlap_policy="restart"))
    first = store.claim(job)
    assert store.start_run(first)
    replacement = service.run_now(job)
    assert store.run(first)["cancel"]
    assert store.run(replacement)["status"] == "waiting"
    assert not store.start_run(replacement)
    assert store.claim() is None
    store.finish(first, "cancelled")
    launched = []
    restored = CronService(tmp_path, launch=launched.append)
    restored.tick()
    assert launched == [replacement]
    assert store.start_run(replacement)


def test_restart_cancels_superseded_waiter_and_manual_stop_cancels_all(tmp_path):
    store = CronStore(tmp_path)
    job = store.save(values(overlap_policy="restart"))
    first = store.claim(job)
    waiting = store.claim(job)
    latest = store.claim(job)
    assert store.run(waiting)["status"] == "cancelled"
    assert store.run(latest)["status"] == "waiting"
    store.change(job, "stop", 1)
    assert store.run(latest)["status"] == "cancelled"
    assert store.run(first)["cancel"]
    assert store.claim() is None


def test_unknown_previous_result_cannot_start_waiting_replacement(tmp_path):
    store = CronStore(tmp_path)
    job = store.save(values(overlap_policy="restart"))
    first = store.claim(job)
    waiting = store.claim(job)
    store.finish(first, "unknown")
    assert store.run(waiting)["status"] == "cancelled"
    assert store.claim() is None
    assert not store.list()["jobs"][0]["enabled"]


@pytest.mark.parametrize("budget", [0, 1])
def test_total_deadline_cancels_and_closes_runtime(tmp_path, budget):
    store = CronStore(tmp_path)
    job = store.save(values(timeout_seconds=budget))
    identity = store.claim(job)
    store.start_run(identity)

    class Runtime:
        closed = False

        async def stream(self, prompt):
            await asyncio.sleep(2.2)
            yield TurnCompleted(identity, "fixture", "Complete")

        async def aclose(self):
            self.closed = True

    runtime = Runtime()

    async def factory(**kwargs):
        return runtime

    def settings_factory(path, **kwargs):
        return CorkiSettings(working_directory=path)

    asyncio.run(supervise(store, identity, factory=factory, settings_factory=settings_factory))
    result = store.run(identity)
    assert runtime.closed
    assert result["status"] == ("failed" if budget else "completed")
    if budget:
        assert "超时时间" in result["error"] and "1 秒" in result["error"]


def test_old_unique_index_migrates_and_active_queries_use_indexes(tmp_path):
    store = CronStore(tmp_path)
    job = store.save(values(overlap_policy="allow"))
    with store.transaction() as db:
        db.execute(
            "CREATE UNIQUE INDEX one_active_job ON runs(job_id) "
            "WHERE status IN ('claimed','running')"
        )
    assert store.claim(job) != store.claim(job)
    with store.transaction() as db:
        assert not any(
            row["name"] == "one_active_job" for row in db.execute("PRAGMA index_list(runs)")
        )
        plan = " ".join(
            row["detail"]
            for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT count(*) FROM runs WHERE status IN ('claimed','running')"
            )
        )
        assert "run_status" in plan
        plan = " ".join(
            row["detail"]
            for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM runs WHERE job_id=? "
                "AND status IN ('waiting','claimed','running')",
                (job,),
            )
        )
        assert "active_job_runs" in plan


def test_restart_dispatches_only_after_runtime_cleanup(tmp_path):
    service = CronService(tmp_path, launch=lambda _: None)
    store = service.store
    job = store.save(values(overlap_policy="restart"))
    first = store.claim(job)
    store.start_run(first)

    async def scenario():
        entered = asyncio.Event()

        class Runtime:
            closed = False

            async def stream(self, prompt):
                entered.set()
                await asyncio.sleep(10)
                yield TurnCompleted(first, "fixture", "unused")

            async def aclose(self):
                await asyncio.sleep(0.02)
                self.closed = True

        runtime = Runtime()

        async def factory(**kwargs):
            return runtime

        running = asyncio.create_task(
            supervise(
                store,
                first,
                factory=factory,
                settings_factory=lambda path, **kw: CorkiSettings(working_directory=path),
            )
        )
        await entered.wait()
        replacement = service.run_now(job)
        assert store.run(replacement)["status"] == "waiting"
        await running
        assert runtime.closed and store.run(first)["status"] == "cancelled"
        launched = []

        def launch(identity):
            assert runtime.closed
            launched.append(identity)

        service.launch = launch
        service.tick()
        assert launched == [replacement]

    asyncio.run(scenario())
