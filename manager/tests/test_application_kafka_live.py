"""Actual Kafka wire tests. Set CORKI_TEST_KAFKA_HOME to an unpacked Apache distribution.

Only disposable KRaft brokers, local fake models and temporary Corki homes are used.
"""

import json
import os
import socket
import subprocess
import time
import uuid
from contextlib import closing
from pathlib import Path

import pytest
from test_application_gateway import fake_model, start
from test_application_gateway import manager as application_manager
from test_applications import values

manager = application_manager


def eventually(check, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Kafka operation did not finish")


@pytest.fixture(scope="module")
def clusters(tmp_path_factory):
    kafka = os.environ.get("CORKI_TEST_KAFKA_HOME")
    if not kafka:
        pytest.skip("CORKI_TEST_KAFKA_HOME not configured; actual broker test is opt-in")
    root = tmp_path_factory.mktemp("kafka-brokers")
    brokers = []
    processes = []
    logs = []
    try:
        for i in range(2):
            ports = []
            # Hold both reservations until ports have been chosen, then start immediately.
            with closing(socket.socket()) as first, closing(socket.socket()) as second:
                for sock in (first, second):
                    sock.bind(("127.0.0.1", 0))
                    ports.append(sock.getsockname()[1])
            folder = root / str(i)
            folder.mkdir()
            config = folder / "server.properties"
            config.write_text(
                f"""process.roles=broker,controller
node.id=1
controller.quorum.voters=1@127.0.0.1:{ports[1]}
listeners=PLAINTEXT://127.0.0.1:{ports[0]},CONTROLLER://127.0.0.1:{ports[1]}
advertised.listeners=PLAINTEXT://127.0.0.1:{ports[0]}
controller.listener.names=CONTROLLER
listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
inter.broker.listener.name=PLAINTEXT
log.dirs={folder / "data"}
num.partitions=2
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
group.initial.rebalance.delay.ms=0
auto.create.topics.enable=true
message.max.bytes=8388608
num.network.threads=2
num.io.threads=2
log.retention.hours=1
"""
            )
            env = {**os.environ, "KAFKA_HEAP_OPTS": "-Xms64m -Xmx256m"}
            subprocess.run(
                [
                    str(Path(kafka) / "bin/kafka-storage.sh"),
                    "format",
                    "--standalone",
                    "-t",
                    uuid.uuid4().hex[:22],
                    "-c",
                    str(config),
                ],
                env=env,
                check=True,
                capture_output=True,
                timeout=30,
            )
            log = (folder / "broker.log").open("wb")
            logs.append(log)
            process = subprocess.Popen(
                [str(Path(kafka) / "bin/kafka-server-start.sh"), str(config)],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            processes.append(process)
            broker = f"127.0.0.1:{ports[0]}"
            from confluent_kafka.admin import AdminClient

            admin = AdminClient({"bootstrap.servers": broker, "log_level": 0})

            def ready(process=process, folder=folder, admin=admin):
                if process.poll() is not None:
                    raise AssertionError((folder / "broker.log").read_text()[-5000:])
                try:
                    return bool(admin.list_topics(timeout=1).brokers)
                except Exception:
                    return False

            eventually(ready, 40)
            brokers.append(broker)
        yield brokers
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
        for log in logs:
            log.close()


class Wire:
    def __init__(self, broker):
        from confluent_kafka import Consumer, Producer
        from confluent_kafka.admin import AdminClient, NewTopic

        self.input = "questions-" + uuid.uuid4().hex
        self.other = "followup-" + uuid.uuid4().hex
        self.output = "answers-" + uuid.uuid4().hex
        self.broker = broker
        admin = AdminClient({"bootstrap.servers": broker, "log_level": 0})
        for future in admin.create_topics(
            [
                NewTopic(t, num_partitions=2, replication_factor=1)
                for t in (self.input, self.other, self.output)
            ]
        ).values():
            future.result(10)
        self.consumer = Consumer(
            dict(
                **{
                    "bootstrap.servers": broker,
                    "group.id": uuid.uuid4().hex,
                    "auto.offset.reset": "earliest",
                    "log_level": 0,
                }
            )
        )
        self.consumer.subscribe([self.output])
        self.producer = Producer({"bootstrap.servers": broker, "log_level": 0})

    def config(self, **kw):
        return dict(
            brokers=[self.broker],
            input_topics=[self.input, self.other],
            output_topic=self.output,
            enabled=True,
            **{"name": "本地集群", **kw},
        )

    def send(self, value, *, other=False):
        errors = []
        self.producer.produce(
            self.other if other else self.input,
            json.dumps(value).encode(),
            on_delivery=lambda error, _: errors.append(error) if error else None,
        )
        assert self.producer.flush(5) == 0 and not errors

    def receive(self, count=1, seconds=30):
        deadline = time.monotonic() + seconds
        result = []
        while len(result) < count and time.monotonic() < deadline:
            message = self.consumer.poll(0.2)
            if message is not None:
                assert not message.error(), message.error()
                result.append(json.loads(message.value()))
        assert len(result) == count, result
        return result

    def close(self):
        self.consumer.close()


def test_two_clusters_topics_optional_correlation_and_http_independence(manager, clusters):
    apps = manager.applications
    captured = fake_model(manager)
    app = apps.store.save(values())
    apps.access.save_app(app["id"], dict(expected=0, http_enabled=False, global_kafka=False))
    wires = [Wire(broker) for broker in clusters]
    try:
        configs = [
            apps.kafka.save(app["id"], wire.config(name=f"集群{i}")) for i, wire in enumerate(wires)
        ]
        for wire in wires:
            wire.send(dict(input="你好", id=0))
            wire.send(dict(input="另一个 topic", id=None), other=True)
            wire.send(dict(input="不带标识"))
        for wire in wires:
            result = wire.receive(3)
            assert all(
                r["status"] == "completed" and r["appid"] == app["public_id"] for r in result
            ), result
            assert sorted("id" in r for r in result) == [False, True, True]
            assert {json.dumps(r["id"]) for r in result if "id" in r} == {"0", "null"}
            assert all(r["output"] == "你好，测试成功" for r in result)
        assert len(captured) == 6
        eventually(lambda: all(not m["queued"] for m in apps.access.metrics().values()))
        assert all(apps.kafka.status()[c["id"]]["state"] == "connected" for c in configs)
        # Actual HTTP socket is blocked while both dedicated Kafka clusters work.
        import httpx

        base, headers = start(manager)
        response = httpx.post(
            base + f"/app?appid={app['public_id']}", headers=headers, json={"input": "HTTP"}
        )
        assert response.status_code == 403
        assert f"appid {app['public_id']} 不支持http调用" in response.json()["error"]["message"]
        assert len(captured) == 6
    finally:
        for wire in wires:
            wire.close()


def test_global_opt_in_controls_and_restart_do_not_reexecute(manager, clusters):
    from corki_manager_applications.kafka_service import KafkaService

    apps = manager.applications
    captured = fake_model(manager, delay=0.4)
    app = apps.store.save(values())
    wire = Wire(clusters[0])
    try:
        config = apps.kafka.save("", wire.config())
        apps.kafka.global_enabled(True, 0)
        wire.send(dict(appid=app["public_id"], input="不接入应忽略", id="ignore"))

        def ignored():
            with apps.access.db() as db:
                return db.execute(
                    "SELECT count(*) FROM kafka_offsets WHERE connection=? AND next_offset=1",
                    (config["id"],),
                ).fetchone()[0]

        eventually(ignored)
        assert not captured
        apps.access.save_app(app["id"], dict(expected=0, http_enabled=True, global_kafka=True))
        wire.send(dict(appid=app["public_id"], input="已接入", id={"original": "请求标识"}))
        eventually(lambda: len(captured) == 1)
        # Restart just the transport while a model is working. Receipt/run remain authoritative.
        apps.kafka.close()
        apps.kafka = KafkaService(apps)
        result = wire.receive()[0]
        assert result["id"] == {"original": "请求标识"} and result["status"] == "completed"
        assert len(captured) == 1
        wire.send(dict(appid=999999, input="不存在", id="bad"))
        rejected = wire.receive()[0]
        assert rejected["id"] == "bad" and rejected["status"] == "rejected"
        # Control messages travel through the same broker; they must not start another model.
        fake_model(manager, infinite=True)
        wire.send(dict(appid=app["public_id"], input="等待停止", id="stop"))
        job = eventually(lambda: next(iter(apps.access.jobs(("running",))), None))
        wire.send(dict(appid=app["public_id"], action="cancel", run_id=job["run"], id="cancel"))
        responses = wire.receive(2)
        assert {r["event"] for r in responses} == {"control", "result"}
        assert next(r for r in responses if r["event"] == "result")["status"] == "cancelled"
    finally:
        wire.close()


def test_agent_question_and_reply_use_real_runtime_over_kafka(manager, clusters):
    from dataclasses import replace

    from corki.core.runtime import LangGraphRuntime
    from corki.models.types import ModelCompleted
    from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
    from corki.protocol.tools import ToolCall

    calls = []

    class Model:
        async def stream(self, request):
            calls.append(request)
            turn = request.items[-1].turn_id
            if len(calls) == 1:
                call = ToolCall(
                    "scope",
                    "request_user_input",
                    {
                        "questions": [
                            {
                                "id": "scope",
                                "header": "范围",
                                "question": "请选择范围",
                                "options": [
                                    {"label": "当前", "description": "当前对象"},
                                    {"label": "全部", "description": "全部对象"},
                                ],
                            }
                        ]
                    },
                )
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
            else:
                yield ModelCompleted((AssistantMessageItem("已确认范围", turn, new_step_id()),))

        async def aclose(self):
            pass

    async def factory(**kw):
        kw["settings"] = replace(kw["settings"], default_mode_request_user_input=True)
        return await LangGraphRuntime.acreate(**kw, model=Model())

    apps = manager.applications
    apps.execution.runtime_factory = factory
    app = apps.store.save({**values(), "type": "agent"})
    wire = Wire(clusters[0])
    try:
        apps.kafka.save(app["id"], wire.config())
        wire.send(dict(input="请确认范围", id="question"))
        interaction = wire.receive()[0]
        assert interaction["event"] == "interaction" and interaction["status"] == "waiting"
        assert interaction["interaction"]["kind"] == "question"
        wire.send(
            dict(
                action="respond",
                run_id=interaction["run_id"],
                interaction_id=interaction["interaction"]["id"],
                decision="accept",
                response={"scope": ["当前"]},
                id="answer",
            )
        )
        responses = wire.receive(2)
        assert {r["event"] for r in responses} == {"control", "result"}
        result = next(r for r in responses if r["event"] == "result")
        assert result["id"] == "question" and result["output"] == "已确认范围"
        assert result["status"] == "completed" and len(calls) == 2
    finally:
        wire.close()
