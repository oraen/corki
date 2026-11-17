"""Access flags, Kafka resource boundaries and durable queue contracts."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from test_applications import AppError, values
from test_applications import manager as application_manager

# isort: split
from corki_manager_applications.access_store import AccessStore
from corki_manager_applications.kafka_protocol import decode_message

manager = application_manager


def connection(apps, owner="", **changes):
    return apps.access.save_connection(
        owner,
        dict(
            name="测试接入",
            brokers=["localhost:19092"],
            input_topics=["questions", "more-questions"],
            output_topic="answers",
            **changes,
        ),
    )


def message(offset=0, topic="questions"):
    value = Mock()
    value.topic.return_value = topic
    value.partition.return_value = 0
    value.offset.return_value = offset
    return value


def test_http_and_global_flags_are_separate_from_prompt_versions(manager):
    apps = manager.applications
    app = apps.store.save(values())
    assert apps.get(dict(action="access", app=app["id"]))["http_enabled"] is True
    config = apps.mutate(
        dict(action="save-access", app=app["id"], http_enabled=False, global_kafka=True, expected=0)
    )
    assert config["revision"] == 1
    with pytest.raises(AppError, match=f"appid {app['public_id']} 不支持http调用"):
        apps.access.authorize(app, "http")
    apps.access.authorize(app, None)
    assert apps.store.app(app["id"])["version"] == 1
    with pytest.raises(AppError, match="其他页面"):
        apps.mutate(
            dict(
                action="save-access",
                app=app["id"],
                http_enabled=True,
                global_kafka=False,
                expected=0,
            )
        )
    assert AccessStore(apps.store.root).app(app["id"])["global_kafka"] is True


def test_connection_validation_ownership_and_secret_projection(manager):
    apps = manager.applications
    app = apps.store.save(values())
    config = connection(
        apps,
        app["id"],
        security_protocol="SASL_SSL",
        sasl_username="test-user",
        sasl_password="test-only-password",
    )
    assert "sasl_password" not in config and config["has_sasl_password"] is True
    assert (
        apps.access.connection(config["id"], secrets=True)["sasl_password"] == "test-only-password"
    )
    assert (
        apps.access.save_connection(app["id"], {"name": "改名"}, config["id"], 1)["revision"] == 2
    )
    assert (
        apps.access.connection(config["id"], secrets=True)["sasl_password"] == "test-only-password"
    )
    with pytest.raises(AppError):
        apps.access.save_connection("", {"name": "跨应用"}, config["id"], 2)
    with pytest.raises(AppError):
        apps.access.save_connection(app["id"], {"name": "过期"}, config["id"], 1)
    for changes in [
        dict(output_topic="questions"),
        dict(brokers=["https://example.com:9092"]),
        dict(input_topics=["bad topic"]),
        dict(enabled="yes"),
    ]:
        with pytest.raises(AppError):
            apps.access.save_connection(app["id"], changes, config["id"], 2)


def test_global_opt_in_and_dedicated_routing(manager):
    apps = manager.applications
    app = apps.store.save(values())
    global_conn = connection(apps)
    raw = json.dumps({"appid": app["public_id"], "id": 0, "input": "问题"}).encode()
    assert decode_message(apps, global_conn, raw)["ignore"] is True
    apps.access.save_app(app["id"], dict(expected=0, http_enabled=False, global_kafka=True))
    parsed = decode_message(apps, global_conn, raw)
    assert parsed["app"] == app["id"] and parsed["id"] == 0 and "error" not in parsed
    assert "error" in decode_message(apps, global_conn, b'{"input":"no app"}')
    own = connection(apps, app["id"])
    parsed = decode_message(apps, own, b'{"input":"own","id":""}')
    assert parsed["appid"] == app["public_id"] and parsed["id"] == ""
    mismatch = decode_message(apps, own, b'{"appid":999999,"input":"bad"}')
    assert mismatch["appid"] == app["public_id"] and "error" in mismatch
    for invalid in (b"not json", b"[]", b'{"input":""}', b'{"input":"x","action":"delete"}'):
        assert "error" in decode_message(apps, own, invalid)


def test_durable_inbox_watermark_outbox_and_topic_snapshot(manager):
    apps = manager.applications
    app = apps.store.save(values())
    conn = connection(apps, app["id"])
    envelope = decode_message(apps, conn, b'{"input":"test","id":0}')
    identity = apps.access.ingest(conn, message(), envelope)
    assert identity and apps.access.ingest(conn, message(), envelope) is None
    job = apps.access.jobs(("queued",))[0]
    apps.access.bind(job, dict(run="a" * 32, session="b" * 32))
    apps.access.save_connection(app["id"], dict(output_topic="new-answers"), conn["id"], 1)
    running = apps.access.jobs(("running",))[0]
    assert running["payload"] == "" and running["payload_bytes"] == 0
    apps.access.emit(running, "result", {"status": "completed", "output": "OK"}, finish=True)
    output = apps.access.next_delivery(conn["id"])
    value = json.loads(output["payload"])
    assert value["id"] == 0 and value["appid"] == app["public_id"] and output["topic"] == "answers"
    assert value["event_id"] == output["id"]
    apps.access.delivered(output["id"])
    with apps.access.db() as db:
        row = db.execute("SELECT payload,payload_bytes FROM kafka_outbox").fetchone()
        assert row["payload"] == "" and row["payload_bytes"] == 0
        db.execute("DELETE FROM kafka_outbox WHERE state='sent'")
    apps.access.prune()
    assert apps.access.ingest(conn, message(), envelope) is None  # no unbounded receipt log needed
    assert apps.access.ingest(conn, message(1), envelope)


def test_id_absent_stays_absent_and_failed_delivery_does_not_block_next(manager):
    apps = manager.applications
    app = apps.store.save(values())
    conn = connection(apps, app["id"])
    for offset in range(2):
        envelope = decode_message(apps, conn, b'{"input":"test"}')
        apps.access.ingest(conn, message(offset), envelope)
    jobs = apps.access.jobs(("queued",))
    for job in jobs:
        apps.access.emit(job, "result", {"status": "completed", "output": "answer"}, finish=True)
    first = apps.access.next_delivery(conn["id"])
    assert "id" not in json.loads(first["payload"])
    for _ in range(3):
        apps.access.delivered(first["id"], "temporary failure")
    second = apps.access.next_delivery(conn["id"])
    assert second["id"] != first["id"]
    apps.access.delivered(second["id"])
    assert apps.access.metrics()[conn["id"]]["failed"] == 1
    apps.access.retry(conn["id"])
    assert apps.access.next_delivery(conn["id"])["id"] == first["id"]


def test_backpressure_allows_dispatch_and_does_not_advance_unstored_offsets(manager, monkeypatch):
    import corki_manager_applications.access_store as module

    monkeypatch.setattr(module, "MAX_QUEUE_ROWS", 1)
    apps = manager.applications
    app = apps.store.save(values())
    conn = connection(apps, app["id"])
    envelope = decode_message(apps, conn, b'{"input":"test"}')
    identity = apps.access.ingest(conn, message(), envelope)
    assert apps.access.capacity() is False and apps.access.dispatch_capacity() is True
    assert apps.access.ingest(conn, message(1), envelope) is False
    apps.access.mark(identity, "done")
    assert apps.access.ingest(conn, message(1), envelope)


def test_cluster_identity_change_pending_is_rejected_and_deletion_cascades(manager):
    apps = manager.applications
    app = apps.store.save(values())
    conn = connection(apps, app["id"])
    apps.access.ingest(conn, message(), decode_message(apps, conn, b'{"input":"test"}'))
    with pytest.raises(AppError, match="未完成"):
        apps.access.save_connection(app["id"], dict(brokers=["localhost:29092"]), conn["id"], 1)
    apps.access.remove_app(app["id"])
    with apps.access.db() as db:
        for table in ("kafka_connections", "kafka_inbox", "kafka_outbox", "kafka_offsets"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_unknown_admission_receipt_and_duplicate_result_do_not_rerun_model(manager):
    from test_application_gateway import fake_model
    from test_applications import wait

    apps = manager.applications
    captured = fake_model(manager)
    app = apps.store.save(values())
    conn = connection(apps, app["id"], enabled=True)
    envelope = decode_message(apps, conn, b'{"input":"test","id":"same"}')
    apps.access.ingest(conn, message(), envelope)
    job = apps.access.jobs(("queued",))[0]
    asyncio.run(apps.kafka.admit(job, envelope))
    original = apps.access.jobs(("running",))[0]
    assert wait(apps, original["run"])["status"] == "completed"
    # Simulate a crash between submit acceptance and receipt persistence.
    with apps.access.db() as db:
        db.execute(
            "UPDATE kafka_inbox SET state='admitting',payload=? WHERE id=?",
            (json.dumps(envelope), job["id"]),
        )
    apps.access.recover()
    asyncio.run(apps.kafka.admit(job, envelope))
    restored = apps.access.jobs(("running",))[0]
    assert restored["run"] == original["run"] and len(captured) == 1
    asyncio.run(apps.kafka.monitor(restored))
    asyncio.run(apps.kafka.monitor(restored))
    with apps.access.db() as db:
        assert db.execute("SELECT count(*) FROM kafka_outbox").fetchone()[0] == 1


def test_delivery_waits_for_ack_and_skips_exhausted_failure(manager, monkeypatch):
    apps = manager.applications
    app = apps.store.save(values())
    conn = connection(apps, app["id"])
    for offset in range(2):
        apps.access.ingest(conn, message(offset), decode_message(apps, conn, b'{"input":"test"}'))
    for job in apps.access.jobs(("queued",)):
        apps.access.emit(job, "result", {"status": "completed", "output": "answer"}, finish=True)
    first = apps.access.next_delivery(conn["id"])
    original = apps.access.delivered
    sent = []

    def delivered(identity, error=""):
        original(identity, error)
        with apps.access.db() as db:
            db.execute("UPDATE kafka_outbox SET retry_at=0 WHERE id=?", (identity,))
        if not error:
            sent.append(identity)

    monkeypatch.setattr(apps.access, "delivered", delivered)

    class Producer:
        async def produce(self, topic, *, value, key):
            payload = json.loads(value)
            future = asyncio.get_running_loop().create_future()
            if payload["event_id"] == first["id"]:
                future.set_exception(RuntimeError("test delivery failure"))
            else:
                # Retain the body until a real acknowledgement arrives.
                with apps.access.db() as db:
                    row = db.execute(
                        "SELECT state,payload FROM kafka_outbox WHERE id=?", (payload["event_id"],)
                    ).fetchone()
                assert row["state"] == "ready" and row["payload"]
                future.set_result(None)
            return future

    async def run():
        task = asyncio.create_task(apps.kafka.deliver(conn, Producer()))
        try:
            for _ in range(100):
                if sent:
                    break
                await asyncio.sleep(0.01)
            assert len(sent) == 1 and sent[0] != first["id"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    with apps.access.db() as db:
        failed = db.execute(
            "SELECT attempts,state,payload FROM kafka_outbox WHERE id=?", (first["id"],)
        ).fetchone()
        assert failed["attempts"] == 3 and failed["state"] == "failed" and failed["payload"]


def test_delete_connection_cancels_accepted_task_and_preserves_history(manager):
    from test_application_gateway import fake_model
    from test_applications import wait

    apps = manager.applications
    fake_model(manager, infinite=True)
    app = apps.store.save(values())
    conn = connection(apps, app["id"], enabled=True)
    envelope = decode_message(apps, conn, b'{"input":"test"}')
    apps.access.ingest(conn, message(), envelope)
    job = apps.access.jobs(("queued",))[0]
    asyncio.run(apps.kafka.admit(job, envelope))
    run = apps.access.jobs(("running",))[0]
    with pytest.raises(AppError, match="未完成"):
        apps.kafka.delete(conn["id"], app["id"], 1)
    assert apps.kafka.delete(conn["id"], app["id"], 1, stop=True) == {"ok": True}
    assert wait(apps, run["run"])["status"] == "cancelled"
    assert apps.store.session(app["id"], run["session"])
    assert not apps.access.connections(app["id"])


def test_queue_indexes_and_second_owner_are_effective(manager):
    from corki_manager_applications.kafka_service import KafkaService

    apps = manager.applications
    apps.kafka.ensure_owner()
    other = KafkaService(apps)
    try:
        with pytest.raises(AppError, match="另一个管理服务"):
            other.ensure_owner()
        with apps.access.db() as db:
            plan = db.execute(
                "EXPLAIN QUERY PLAN SELECT error FROM kafka_outbox "
                "WHERE connection=? AND state='failed' ORDER BY updated DESC LIMIT 1",
                ("connection",),
            ).fetchall()
            assert any("kafka_last_error" in row["detail"] for row in plan)
            plan = db.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM kafka_outbox WHERE connection=? "
                "AND state='ready' AND retry_at<=? ORDER BY created LIMIT 1",
                ("connection", 1),
            ).fetchall()
            assert any("kafka_delivery" in row["detail"] for row in plan)
    finally:
        other.close()
