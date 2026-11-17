"""Real aiohttp sockets and isolated models; never use the user's gateway or data."""

import asyncio
import json
import socket
import time
from contextlib import closing

import httpx
import pytest
from test_applications import (  # reuse the existing disposable manager fixture
    AppError,
    Applications,
    AssistantMessageItem,
    ModelCompleted,
    ModelTextDelta,
    ModelUsage,
    values,
    wait,
)
from test_applications import (
    manager as application_manager_fixture,
)

manager = application_manager_fixture


def settled(gateway):
    for _ in range(500):
        state = gateway.status()
        if state["state"] not in ("starting", "stopping"):
            return state
        time.sleep(0.02)
    raise AssertionError("gateway transition did not finish")


def start(manager, **config):
    gateway = manager.applications.gateway
    gateway.change(dict(enabled=True, **config))
    state = settled(gateway)
    assert state["state"] == "running", state
    return f"http://127.0.0.1:{state['port']}", {
        "Authorization": "Bearer " + gateway.status(include_key=True)["api_key"]
    }


def fake_model(manager, delay=0.03, infinite=False):
    captured = []

    class Model:
        async def stream(self, request):
            captured.append(request)
            yield ModelTextDelta("你好")
            await asyncio.sleep(delay if not infinite else 100)
            yield ModelTextDelta("，测试成功")
            yield ModelCompleted(
                (AssistantMessageItem(content="你好，测试成功", turn_id="test", step_id="test"),),
                ModelUsage(input_tokens=12, output_tokens=8),
            )

        async def aclose(self):
            pass

    manager.applications.execution.model_factory = lambda _: Model()
    return captured


def test_public_json_context_idempotency_auth_and_validation(manager):
    captured = fake_model(manager)
    store = manager.applications.store
    app = store.save(values())
    base, headers = start(manager)
    url = f"/app?appid={app['public_id']}"
    with httpx.Client(base_url=base, headers=headers, timeout=10) as client:
        assert client.get("/health", headers={"Authorization": ""}).json()["version"] == 1
        assert (
            client.post(url, headers={"Authorization": ""}, json={"input": "hi"}).status_code == 401
        )
        assert (
            client.post(
                url, headers={"Authorization": "Bearer wrong"}, json={"input": "hi"}
            ).status_code
            == 401
        )
        one = client.post(url, json={"input": "你好", "request_id": "stable"}).json()
        assert one["status"] == "completed" and one["output"] == "你好，测试成功", one
        assert (
            client.post(url, json={"input": "你好", "request_id": "stable"}).json()["run_id"]
            == one["run_id"]
        )
        assert len(captured) == 1
        assert client.get(url + "&request_id=stable").json()["run_id"] == one["run_id"]
        assert (
            client.post(url, json={"input": "changed", "request_id": "stable"}).status_code == 409
        )
        two = client.post(url, json={"input": "继续", "session_id": one["session_id"]}).json()
        assert two["status"] == "completed" and len(captured[-1].items) == 3
        another = store.save({**values(), "name": "另一个应用"})
        other_url = f"/app?appid={another['public_id']}"
        assert client.get(other_url + "&run_id=" + one["run_id"]).status_code == 404
        assert (
            client.post(
                other_url, json={"input": "继续", "session_id": one["session_id"]}
            ).status_code
            == 404
        )
        assert (
            client.post(other_url, json={"input": "你好", "request_id": "stable"}).json()["status"]
            == "completed"
        )
        for suffix in ("", "abc", "-1", "100000&appid=100001"):
            assert client.post("/app?appid=" + suffix, json={"input": "hi"}).status_code == 400
        for body in (
            {"input": " "},
            {"input": "x", "stream": "false"},
            {"input": "x", "wait": 1},
            {"input": "x", "request_id": ""},
            {"input": "x", "tools": ["exec"]},
        ):
            assert client.post(url, json=body).status_code == 400
        assert client.post(url, content="{}").status_code == 415
        assert (
            client.post(url, content="{", headers={"Content-Type": "application/json"}).status_code
            == 400
        )
        assert (
            client.post(
                url,
                content=" " * (2 * 1024 * 1024 + 1),
                headers={"Content-Type": "application/json"},
            ).status_code
            == 413
        )
        assert (
            client.post(
                url, json={"input": "x", "request_id": "one"}, headers={"Idempotency-Key": "two"}
            ).status_code
            == 400
        )
        store.set_enabled(app["id"], False)
        assert client.post(url, json={"input": "新的问题"}).status_code == 409
        assert (
            client.post(url, json={"input": "你好", "request_id": "stable"}).json()["run_id"]
            == one["run_id"]
        )
        assert "api_key" not in json.dumps(one) and app["prompt"] not in json.dumps(
            one, ensure_ascii=False
        )


def test_sse_disconnect_lookup_cancel_and_gateway_stop_keep_task(manager):
    fake_model(manager, infinite=True)
    app = manager.applications.store.save(values())
    base, headers = start(manager)
    url = f"/app?appid={app['public_id']}"
    with httpx.Client(base_url=base, headers=headers, timeout=10) as client:
        with client.stream(
            "POST", url, json={"input": "test", "stream": True, "request_id": "disconnect"}
        ) as response:
            assert response.status_code == 200 and response.headers["Content-Type"].startswith(
                "text/event-stream"
            )
            for line in response.iter_lines():
                if line.startswith("data: ") and "run_id" in line:
                    run = json.loads(line[6:])["run_id"]
                if line.startswith("event: message.delta"):
                    break
        snapshot = client.get(url + "&request_id=disconnect").json()
        assert snapshot["run_id"] == run and snapshot["status"] == "running"
        assert snapshot["output"] == "你好"

        # Parallel sockets must not be serialized behind a long model request.
        async def burst():
            async with httpx.AsyncClient(base_url=base, headers=headers, timeout=5) as concurrent:
                responses = await asyncio.gather(
                    *[concurrent.get(url + "&run_id=" + run) for _ in range(48)]
                )
                assert all(r.status_code == 200 and r.json()["run_id"] == run for r in responses)

        asyncio.run(burst())
        cancelled = client.post("/app/cancel?appid=" + str(app["public_id"]), json={"run_id": run})
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["output"] == "你好"
        receipt = client.post(url, json={"input": "second", "wait": False}).json()
        assert receipt["status"] in ("submitted", "running")
    manager.applications.gateway.change(dict(enabled=False))
    assert settled(manager.applications.gateway)["state"] == "stopped"
    assert manager.applications.store.run(receipt["run_id"])["status"] == "running"
    manager.applications.mutate(
        dict(action="stop", app=app["id"], session=receipt["session_id"], run=receipt["run_id"])
    )


def test_sse_completion_is_explicit_and_matches_json(manager):
    fake_model(manager, delay=0.3)
    app = manager.applications.store.save(values())
    base, headers = start(manager)
    response = httpx.post(
        base + f"/app?appid={app['public_id']}",
        headers=headers,
        json={"input": "test", "stream": True},
        timeout=10,
    )
    assert response.status_code == 200
    events = []
    for packet in response.text.strip().split("\n\n"):
        lines = packet.splitlines()
        kind = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = next((json.loads(line[6:]) for line in lines if line.startswith("data: ")), None)
        if kind:
            events.append((kind, data))
    assert events[0][0] == "run.created" and events[-1][0] == "run.completed"
    assert (
        "".join(data["content"] for kind, data in events if kind == "message.delta")
        == "你好，测试成功"
    )
    assert events[-1][1]["output"] == "你好，测试成功"


def test_ports_reconfiguration_recovery_and_owner_lock(manager, monkeypatch):
    import corki_manager_applications.gateway as module

    gateway = manager.applications.gateway
    with closing(socket.socket()) as occupied:
        occupied.bind(("0.0.0.0", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        monkeypatch.setattr(module, "DEFAULT_PORT", port)
        base, headers = start(manager)
        fallback = gateway.status()["port"]
        assert fallback != port
        gateway.change(dict(enabled=True, mode="manual", port=port))
        state = settled(gateway)
        assert state["state"] == "failed" and not state["running"] and state["port"] is None
        assert str(port) in state["error"]
        # Original port owner is never stopped or killed by fallback/reconfiguration.
        assert occupied.fileno() >= 0
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        manual = probe.getsockname()[1]
    start(manager, mode="manual", port=manual)
    saved = json.loads(gateway.path.read_text())
    assert saved["mode"] == "manual" and saved["enabled"] is True and saved["port"] == manual
    assert len(saved["api_key"]) == 32
    assert all(character in "0123456789abcdef" for character in saved["api_key"])
    second = Applications(manager)
    try:
        assert settled(second.gateway)["state"] == "failed"
        assert "另一个管理服务" in second.gateway.status()["error"]
    finally:
        second.close()
    gateway.close()
    # Persisted settings restore automatically on service restart, using the same key.
    from corki_manager_applications.gateway import ApplicationGateway

    restored = ApplicationGateway(manager.applications)
    try:
        assert settled(restored)["state"] == "running"
        assert restored.status()["port"] == manual
        assert restored.status(include_key=True)["api_key"] == saved["api_key"]
        restored.change(dict(enabled=False))
        assert settled(restored)["state"] == "stopped"
    finally:
        restored.close()
    for port in (manual, fallback):
        with closing(socket.socket()) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("0.0.0.0", port))
    for bad in (0, -1, 65536, 1.5, "17004", True):
        with pytest.raises(AppError):
            restored.validate("manual", bad)


def test_agent_gateway_real_tool_loop_and_usage(manager):
    from corki.core.runtime import LangGraphRuntime
    from corki.protocol.items import ToolCallItem, new_step_id
    from corki.protocol.tools import ToolCall

    captured = []

    class Model:
        async def stream(self, request):
            captured.append(request)
            turn = request.items[-1].turn_id
            if len(captured) == 1:
                call = ToolCall(
                    "plan-test",
                    "update_plan",
                    {"plan": [{"step": "验证网关工具", "status": "completed"}]},
                )
                yield ModelCompleted(
                    (ToolCallItem(call, turn, new_step_id()),),
                    ModelUsage(input_tokens=10, output_tokens=5),
                )
            else:
                yield ModelTextDelta("Agent 工具完成")
                yield ModelCompleted(
                    (AssistantMessageItem("Agent 工具完成", turn, new_step_id()),),
                    ModelUsage(input_tokens=10, output_tokens=5),
                )

        async def aclose(self):
            pass

    async def factory(**kw):
        return await LangGraphRuntime.acreate(**kw, model=Model())

    manager.applications.execution.runtime_factory = factory
    app = manager.applications.store.save({**values(), "type": "agent"})
    base, headers = start(manager)
    response = httpx.post(
        base + f"/app?appid={app['public_id']}",
        headers=headers,
        json={"input": "验证工具"},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    assert (
        response.json()["status"] == "completed" and response.json()["output"] == "Agent 工具完成"
    )
    assert len(captured) == 2
    with manager.applications.store.db() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_agent_question_can_be_answered_through_protocol(manager):
    from dataclasses import replace

    from corki.core.runtime import LangGraphRuntime
    from corki.protocol.items import ToolCallItem, new_step_id
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
                                    {"label": "全部", "description": "所有对象"},
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

    manager.applications.execution.runtime_factory = factory
    app = manager.applications.store.save({**values(), "type": "agent"})
    base, headers = start(manager)
    with httpx.Client(base_url=base, headers=headers, timeout=15) as client:
        result = client.post(f"/app?appid={app['public_id']}", json={"input": "确认范围"})
        view = result.json()
        assert result.status_code == 202 and view["status"] == "waiting", view
        assert view["interaction"]["kind"] == "question"
        response = client.post(
            f"/app/respond?appid={app['public_id']}",
            json={
                "run_id": view["run_id"],
                "interaction_id": view["interaction"]["id"],
                "decision": "accept",
                "response": {"scope": ["当前"]},
            },
        )
        assert response.status_code == 200, response.text
        assert wait(manager.applications, view["run_id"])["status"] == "completed"
        assert (
            client.get(f"/app?appid={app['public_id']}&run_id={view['run_id']}").json()["output"]
            == "已确认范围"
        )


def test_stale_run_is_reconciled_without_model_replay(manager):
    calls = fake_model(manager)
    app = manager.applications.store.save(values())
    store = manager.applications.store
    session = store.new_session(app["id"], str(manager.workspace), "旧任务")
    run, _ = store.accept(session["id"], "old-run", app, "旧任务")
    base, headers = start(manager)
    response = httpx.get(
        base + f"/app?appid={app['public_id']}&run_id={run['id']}", headers=headers
    )
    assert response.status_code == 200 and response.json()["status"] == "failed"
    assert calls == []


def test_model_failure_and_non_prefix_stream_replacement(manager):
    class Model:
        async def stream(self, request):
            yield ModelTextDelta("草稿")
            await asyncio.sleep(0.25)
            if request.items[-1].content == "fail":
                raise RuntimeError("可控模型异常")
            yield ModelCompleted((AssistantMessageItem("最终内容", "test", "test"),))

        async def aclose(self):
            pass

    manager.applications.execution.model_factory = lambda _: Model()
    app = manager.applications.store.save(values())
    base, headers = start(manager)
    with httpx.Client(base_url=base, headers=headers, timeout=10) as client:
        response = client.post(f"/app?appid={app['public_id']}", json={"input": "fail"})
        assert response.status_code == 502
        assert response.json()["status"] == "failed" and response.json()["output"] == "草稿"
        response = client.post(
            f"/app?appid={app['public_id']}", json={"input": "replace", "stream": True}
        )
        assert "event: message.replace" in response.text
        assert "event: run.completed" in response.text
        response = client.post(
            f"/app?appid={app['public_id']}", json={"input": "fail", "stream": True}
        )
        assert "event: run.failed" in response.text and "event: run.completed" not in response.text


def test_existing_gateway_key_is_preserved(manager):
    import secrets

    from corki_manager_applications.gateway import ApplicationGateway

    original = manager.applications.gateway
    legacy_key = "sk-app-" + secrets.token_urlsafe(32)
    original.path.write_text(
        json.dumps(dict(enabled=False, mode="auto", port=17004, api_key=legacy_key))
    )
    restored = ApplicationGateway(manager.applications)
    try:
        assert restored.status(include_key=True)["api_key"] == legacy_key
    finally:
        restored.close()
