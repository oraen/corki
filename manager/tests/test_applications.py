"""External application contracts against disposable data and real adapters."""

import asyncio
import importlib.util
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "application_manager_test", Path(__file__).parents[1] / "server.py"
)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
from corki_manager_applications.models import (  # noqa: E402
    AppError,  # noqa: E402 - bootstraps the standalone Manager module
)
from corki_manager_applications.service import (  # noqa: E402
    Applications,  # noqa: E402 - bootstraps the standalone Manager module
)

from corki.models.types import (  # noqa: E402 - bootstraps the standalone Manager module
    ModelCompleted,
    ModelTextDelta,
    ModelUsage,
)
from corki.protocol.items import (  # noqa: E402
    AssistantMessageItem,  # noqa: E402 - bootstraps the standalone Manager module
)


@pytest.fixture
def manager(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.json").write_text(
        json.dumps(
            {
                "default": {"provider": "local", "model": "test"},
                "providers": {
                    "local": {
                        "base_url": "http://127.0.0.1:1",
                        "api_mode": "responses",
                        "models": {"test": {"context_window": 131072}},
                    }
                },
            }
        )
    )
    store = server.Store(home, tmp_path / "work", tmp_path / "compat")
    yield store
    store.applications.close()


def values(**kw):
    return dict(name="写作助手", prompt="请简洁回答", type="llm", **kw)


def wait(apps, run):
    for _ in range(300):
        row = apps.store.run(run)
        if row["status"] not in ("submitted", "running", "waiting", "stopping"):
            return row
        time.sleep(0.02)
    raise AssertionError("task did not settle")


def test_public_ids_survive_edits_deletion_and_concurrent_creation(manager):
    from concurrent.futures import ThreadPoolExecutor

    store = manager.applications.store
    first = store.save(values())
    assert first["public_id"] == 100000 and first["enabled"] is True
    updated = store.save(values(description="更新"), first["id"], 1)
    assert updated["public_id"] == 100000
    with ThreadPoolExecutor(max_workers=4) as workers:
        created = list(workers.map(lambda i: store.save({**values(), "name": f"应用{i}"}), range(8)))
    assert sorted(app["public_id"] for app in created) == list(range(100001, 100009))
    for app in [first, *created]:
        manager.applications.mutate(dict(action="delete", app=app["id"], stop=True))
    reopened = type(store)(manager.home)
    assert reopened.save(values())["public_id"] == 100009


def test_legacy_public_id_backfill_preserves_history(manager):
    store = manager.applications.store
    first = store.save(values())
    session = store.new_session(first["id"], str(manager.workspace), "已有会话")
    # Reproduce the pre-ID schema, without replacing any existing content.
    with store.db() as db:
        db.execute("DROP INDEX app_public_id")
        db.execute("ALTER TABLE apps DROP COLUMN public_id")
        db.execute("ALTER TABLE apps DROP COLUMN enabled")
        db.execute("DROP TABLE app_sequence")
    migrated = type(store)(manager.home)
    app = migrated.app(first["id"])
    assert app["public_id"] == 100000 and app["enabled"] is True
    assert app["prompt"] == first["prompt"] and app["version"] == 1
    assert migrated.session(first["id"], session["id"])["title"] == "已有会话"
    assert type(store)(manager.home).app(first["id"])["public_id"] == 100000


def test_application_search_by_public_id_and_name_respects_type(manager):
    store = manager.applications.store
    first = store.save({**values(), "name": "Writing 助手"})
    second = store.save({**values(), "name": "开发助手", "type": "agent"})
    assert [row["id"] for row in store.listing(str(first["public_id"]))["items"]] == [first["id"]]
    assert [row["id"] for row in store.listing("writing")["items"]] == [first["id"]]
    assert [row["id"] for row in store.listing("开发")["items"]] == [second["id"]]
    assert store.listing(str(first["public_id"]), "agent")["items"] == []
    assert store.listing("不存在的名称")["items"] == []


def test_disabled_app_blocks_new_work_but_keeps_accepted_work(manager):
    class Model:
        async def stream(self, request):
            yield ModelTextDelta("进行中")
            await asyncio.sleep(0.1)
            yield ModelCompleted((AssistantMessageItem(content="已完成", turn_id="test", step_id="test"),))

        async def aclose(self):
            pass

    apps = manager.applications
    apps.execution.model_factory = lambda _: Model()
    app = apps.store.save(values())
    request = dict(action="send", app=app["id"], request="accepted", text="你好")
    receipt = apps.mutate(request)
    disabled = apps.mutate(dict(action="set-enabled", app=app["id"], enabled=False))
    assert disabled["enabled"] is False
    assert apps.mutate(request) == receipt  # Lost receipt retries do not re-execute.
    with pytest.raises(AppError, match="应用已关闭"):
        apps.mutate({**request, "request": "new"})
    assert wait(apps, receipt["run"])["status"] == "completed"
    persisted = type(apps.store)(manager.home).app(app["id"])
    assert persisted["enabled"] is False and persisted["version"] == 1
    assert len(apps.store.sessions(app["id"])["items"]) == 1
    edited = apps.store.save(values(description="禁用时编辑"), app["id"], 1)
    assert edited["enabled"] is False
    with pytest.raises(AppError, match="参数无效"):
        apps.mutate(dict(action="set-enabled", app=app["id"], enabled="false"))
    apps.mutate(dict(action="set-enabled", app=app["id"], enabled=True))
    assert wait(apps, apps.mutate({**request, "request": "enabled-again"})["run"])["status"] == "completed"


def test_definition_versions_conflicts_and_retention(manager):
    store = manager.applications.store
    app = store.save(values())
    assert store.save(values(), app["id"], 1)["version"] == 1
    with pytest.raises(AppError, match="已存在"):
        store.save(values())
    for i in range(24):
        app = store.save(values(description=str(i)), app["id"], app["version"])
    assert len(app["versions"]) == 20
    with pytest.raises(AppError, match="其他页面"):
        store.save(values(), app["id"], 1)
    with pytest.raises(AppError, match="类型"):
        store.save({**values(), "type": "agent"}, app["id"], app["version"])
    restored = manager.applications.mutate(
        dict(
            action="restore",
            app=app["id"],
            version=app["versions"][-1]["version"],
            expected=app["version"],
        )
    )
    assert restored["version"] == app["version"] + 1


def test_prompt_restore_preserves_current_configuration_and_conflicts(manager):
    apps = manager.applications
    original = dict(name="原始应用", type="agent", prompt="第一版提示词", context=True,
                    provider="local", model="test", directory="", permissions="", skills=[])
    app = apps.mutate(dict(action="save", values=original))
    changed = {**original, "name": "更新后的名称", "description": "保留当前描述",
               "prompt": "第二版提示词", "context": False, "permissions": ":workspace",
               "directory": str(manager.workspace), "skills": ["example-skill"]}
    app = apps.mutate(dict(action="save", app=app["id"], values=changed,
                           expected=1, section="prompt"))
    assert app["versions"][0]["reason"] == "编辑提示词"
    before_info_change = apps.store.app(app["id"])
    assert "prompt_version" not in before_info_change  # Runtime reads skip history work.
    info_only = {**changed, "description": "只修改基本信息"}
    app = apps.mutate(dict(action="save", app=app["id"], values=info_only, expected=2))
    changed = info_only
    assert app["prompt_version"] == 2
    prior_history = apps.store.prompt_history(before_info_change)
    assert all(version["version"] <= 2 for version in prior_history["versions"])
    assert app["versions"][0]["prompt_changed"] is False
    restored = apps.mutate(dict(action="restore-prompt", app=app["id"], version=1, expected=3))
    assert restored["version"] == 4
    assert restored["prompt"] == original["prompt"]
    for key in changed.keys() - {"prompt"}:
        assert restored[key] == changed[key]
    assert restored["versions"][0]["reason"] == "回滚提示词"
    assert restored["prompt_version"] == 4
    assert apps.store.app(app["id"], 2)["prompt"] == changed["prompt"]
    with pytest.raises(AppError, match="其他页面"):
        apps.mutate(dict(action="restore-prompt", app=app["id"], version=2, expected=2))
    # Restoring identical prompt content does not create a redundant version.
    assert apps.mutate(dict(action="restore-prompt", app=app["id"], version=1,
                            expected=4))["version"] == 4
    for invalid in (None, True, 0, -1, "1"):
        with pytest.raises(AppError, match="有效"):
            apps.mutate(dict(action="restore-prompt", app=app["id"], version=invalid, expected=3))


@pytest.mark.parametrize(
    "patch",
    [
        {"name": "  "},
        {"prompt": "\n"},
        {"context": 0},
        {"type": "other"},
        {"api_key": "no"},
        {"model": "missing"},
    ],
)
def test_invalid_definition(manager, patch):
    with pytest.raises(AppError):
        manager.applications.store.save({**values(), **patch})


def test_direct_llm_isolation_stop_idempotency_and_usage(manager):
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            yield ModelTextDelta("一部分")
            await asyncio.sleep(0.08)
            yield ModelCompleted(
                (AssistantMessageItem(content="完整回答", turn_id="test", step_id="test"),),
                ModelUsage(input_tokens=1234, output_tokens=12),
            )

        async def aclose(self):
            pass

    apps = manager.applications
    apps.execution.model_factory = lambda settings: Model()
    app = apps.store.save(values())
    data = dict(action="send", app=app["id"], request="request-1", text="你好")
    one = apps.mutate(data)
    assert apps.mutate(data) == one
    receipt = apps.get({"action": "submission", "app": app["id"], "request": "request-1"})
    assert receipt["run"] == one["run"] and receipt["session"] == one["session"]
    first = wait(apps, one["run"])
    assert first["status"] == "completed", first
    assert first["answer"] == "完整回答"
    assert requests[0].instructions == app["prompt"] and requests[0].tools == ()
    second = apps.mutate(
        {**data, "request": "request-2", "session": one["session"], "text": "继续"}
    )
    assert wait(apps, second["run"])["status"] == "completed"
    assert len(requests[1].items) == 3
    with closing(sqlite3.connect(apps.execution.ledger)) as db:
        assert db.execute("SELECT sum(input_tokens) FROM usage_events").fetchone()[0] == 2468
    apps.mutate(dict(action="delete", app=app["id"], stop=True))
    with closing(sqlite3.connect(apps.execution.ledger)) as db:
        assert db.execute("SELECT count(*) FROM usage_events").fetchone()[0] == 2


def test_cancel_keeps_partial_and_non_context_keeps_transcript(manager):
    captured = []

    class Slow:
        async def stream(self, request):
            captured.append(request)
            yield ModelTextDelta("保留部分")
            await asyncio.sleep(100)

        async def aclose(self):
            pass

    apps = manager.applications
    apps.execution.model_factory = lambda _: Slow()
    app = apps.store.save(values(context=False))
    submitted = apps.mutate(dict(action="send", app=app["id"], request="stop", text="你好"))
    for _ in range(300):
        if (
            apps.execution.snapshot(submitted["run"])
            and apps.execution.snapshot(submitted["run"])["answer"]
        ):
            break
        time.sleep(0.01)
    apps.mutate(
        dict(action="stop", app=app["id"], session=submitted["session"], run=submitted["run"])
    )
    row = wait(apps, submitted["run"])
    assert row["status"] == "cancelled" and row["answer"] == "保留部分"
    assert len(captured[0].items) == 1
    assert (
        apps.get(dict(action="history", app=app["id"], session=submitted["session"]))["items"][0][
            "input"
        ]
        == "你好"
    )


def test_explicit_model_does_not_fallback(manager):
    apps = manager.applications
    app = apps.store.save(values(provider="local", model="removed"))
    original = manager.config.read_bytes()
    with pytest.raises(AppError, match="模型不可用"):
        apps.mutate(dict(action="send", app=app["id"], request="missing", text="hi"))
    assert manager.config.read_bytes() == original


def test_session_ownership_and_two_workers(manager):
    apps = manager.applications
    app = apps.store.save(values())
    other = apps.store.save({**values(), "name": "other"})
    session = apps.store.new_session(app["id"], "/unused", "hi")
    with pytest.raises(AppError, match="不属于"):
        apps.get(dict(action="history", app=other["id"], session=session["id"]))
    apps.execution.start()
    second = Applications(manager)
    try:
        with pytest.raises(AppError, match="另一个"):
            second.execution.start()
    finally:
        second.close()


def test_real_agent_tool_cycle_history_and_prompt(manager):
    from corki.core.runtime import LangGraphRuntime
    from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
    from corki.protocol.tools import ToolCall

    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            turn = request.items[-1].turn_id
            if len(requests) == 1:
                call = ToolCall(
                    "plan-test",
                    "update_plan",
                    {"plan": [{"step": "验证运行时工具调用", "status": "completed"}]},
                )
                yield ModelCompleted(
                    (ToolCallItem(call, turn, new_step_id()),),
                    ModelUsage(input_tokens=50, output_tokens=5),
                )
            else:
                yield ModelTextDelta("工具执行完成")
                yield ModelCompleted(
                    (AssistantMessageItem("工具执行完成", turn, new_step_id()),),
                    ModelUsage(input_tokens=60, output_tokens=6),
                )

        async def aclose(self):
            pass

    async def factory(**kw):
        assert kw["settings"].memories_enabled is False
        return await LangGraphRuntime.acreate(**kw, model=Model())

    apps = manager.applications
    apps.execution.runtime_factory = factory
    app = apps.store.save({**values(), "type": "agent"})
    submitted = apps.mutate(dict(action="send", app=app["id"], request="agent-1", text="验证工具"))
    row = wait(apps, submitted["run"])
    assert row["status"] == "completed", row
    assert len(requests) == 2
    assert any(isinstance(i, ToolResultItem) for i in requests[-1].items)
    assert len([c for c in requests[-1].context_items if c.key == "host.application"]) == 1
    history = apps.get(dict(action="history", app=app["id"], session=submitted["session"]))
    assert history["items"][0]["input"] == "验证工具"
    assert history["items"][0]["messages"][-1]["content"] == "工具执行完成"
    assert history["items"][0]["tools"]["plan-test"]["status"] == "completed"
    with closing(sqlite3.connect(apps.execution.ledger)) as db:
        assert db.execute("SELECT sum(input_tokens) FROM usage_events").fetchone()[0] == 110
    apps.mutate(dict(action="delete", app=app["id"], stop=True))
    with closing(sqlite3.connect(apps.execution.database)) as db:
        assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 0


def test_real_agent_question_round_trip(manager):
    from dataclasses import replace

    from corki.core.runtime import LangGraphRuntime
    from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
    from corki.protocol.tools import ToolCall

    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            turn = request.items[-1].turn_id
            if len(requests) == 1:
                call = ToolCall(
                    "ask-scope",
                    "request_user_input",
                    {
                        "questions": [
                            {
                                "id": "scope",
                                "header": "范围",
                                "question": "选择处理范围？",
                                "options": [
                                    {"label": "仅当前", "description": "处理当前对象"},
                                    {"label": "全部", "description": "处理全部对象"},
                                ],
                            }
                        ]
                    },
                )
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
            else:
                yield ModelCompleted((AssistantMessageItem("收到补充信息", turn, new_step_id()),))

        async def aclose(self):
            pass

    async def factory(**kw):
        kw["settings"] = replace(kw["settings"], default_mode_request_user_input=True)
        return await LangGraphRuntime.acreate(**kw, model=Model())

    apps = manager.applications
    apps.execution.runtime_factory = factory
    app = apps.store.save({**values(), "type": "agent"})
    submitted = apps.mutate(
        dict(action="send", app=app["id"], request="question", text="帮助我确认范围")
    )
    for _ in range(400):
        view = apps.execution.snapshot(submitted["run"])
        if view and view["interaction"]:
            break
        time.sleep(0.01)
    assert view and view["interaction"], apps.store.run(submitted["run"])
    apps.mutate(
        dict(
            action="respond",
            app=app["id"],
            session=submitted["session"],
            run=submitted["run"],
            request="ask-scope",
            response={"answers": {"scope": {"answers": ["仅当前"]}}},
        )
    )
    assert wait(apps, submitted["run"])["status"] == "completed"
    result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
    assert "仅当前" in result.content


def test_live_snapshot_sequence_and_updated_prompt(manager):
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            yield ModelTextDelta("内容")
            await asyncio.sleep(0.15)
            yield ModelCompleted((AssistantMessageItem("回答", request.items[-1].turn_id, "step"),))

        async def aclose(self):
            pass

    apps = manager.applications
    apps.execution.model_factory = lambda _: Model()
    app = apps.store.save(values())
    submitted = apps.mutate(dict(action="send", app=app["id"], request="v1", text="第一轮"))
    revised = apps.store.save({**values(), "prompt": "新的系统指令"}, app["id"], app["version"])
    assert wait(apps, submitted["run"])["status"] == "completed"
    second = apps.mutate(
        dict(
            action="send", app=app["id"], session=submitted["session"], request="v2", text="第二轮"
        )
    )
    assert wait(apps, second["run"])["status"] == "completed"
    assert [r.instructions for r in requests] == [app["prompt"], revised["prompt"]]
    with pytest.raises(AppError, match="上次提交"):
        apps.mutate(
            dict(
                action="send",
                app=app["id"],
                session=submitted["session"],
                request="v2",
                text="篡改后的内容",
            )
        )


def test_incomplete_model_response_preserves_answer(manager):
    class Model:
        async def stream(self, request):
            yield ModelTextDelta("有效的部分回答")

        async def aclose(self):
            pass

    apps = manager.applications
    apps.execution.model_factory = lambda _: Model()
    app = apps.store.save(values())
    submitted = apps.mutate(dict(action="send", app=app["id"], request="incomplete", text="问题"))
    row = wait(apps, submitted["run"])
    assert row["status"] == "failed" and row["answer"] == "有效的部分回答"
    assert "完成标记" in row["error"]


@pytest.mark.parametrize("decision", ["accept", "decline"])
def test_real_execution_approval(manager, decision):
    import shlex

    from corki.core.runtime import LangGraphRuntime
    from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
    from corki.protocol.tools import ToolCall

    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            turn = request.items[-1].turn_id
            if len(requests) == 1:
                command = (
                    shlex.quote(sys.executable)
                    + " -c "
                    + shlex.quote('print("application-fixture")')
                )
                call = ToolCall(
                    "approval-command",
                    "exec_command",
                    {
                        "cmd": command,
                        "sandbox_permissions": "require_escalated",
                        "justification": "Run a harmless isolated fixture",
                    },
                )
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
            else:
                yield ModelCompleted((AssistantMessageItem("处理完成", turn, new_step_id()),))

        async def aclose(self):
            pass

    async def factory(**kw):
        return await LangGraphRuntime.acreate(**kw, model=Model())

    apps = manager.applications
    apps.execution.runtime_factory = factory
    app = apps.store.save({**values(), "type": "agent"})
    submitted = apps.mutate(
        dict(action="send", app=app["id"], request="approve", text="验证本地审批")
    )
    for _ in range(500):
        view = apps.execution.snapshot(submitted["run"])
        if view and view["interaction"]:
            break
        if apps.store.run(submitted["run"])["status"] not in ("running", "submitted", "waiting"):
            break
        time.sleep(0.01)
    assert view and view["interaction"], apps.store.run(submitted["run"])
    apps.mutate(
        dict(
            action="respond",
            app=app["id"],
            session=submitted["session"],
            run=submitted["run"],
            request=view["interaction"]["id"],
            decision=decision,
        )
    )
    row = wait(apps, submitted["run"])
    assert row["status"] == "completed", row
    result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
    assert result.is_error is (decision == "decline")


def test_real_http_model_transport_and_management_boundary(manager):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import httpx

    requests = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            values = [
                {"choices": [{"delta": {"content": "真实适配器已接通"}, "finish_reason": None}]},
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 77, "completion_tokens": 8, "total_tokens": 85},
                },
            ]
            body = (
                "\n\n".join("data: " + json.dumps(v, ensure_ascii=False) for v in values)
                + "\n\ndata: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    http.daemon_threads = True
    http.store = manager
    http_worker = threading.Thread(target=http.serve_forever, daemon=True)
    http_worker.start()
    config = json.loads(manager.config.read_text())
    config["providers"]["local"].update(
        base_url=f"http://127.0.0.1:{upstream.server_port}",
        api_mode="chat_completions",
        api_key="fixture-only",
    )
    manager.config.write_text(json.dumps(config))
    url = f"http://127.0.0.1:{http.server_port}/api/applications"
    try:
        with httpx.Client(trust_env=False) as client:
            assert client.post(url, json={}).status_code == 403
            headers = {"X-Corki-Manager": "1"}
            app = client.post(
                url, headers=headers, json={"action": "save", "values": values()}
            ).json()
            accepted = client.post(
                url,
                headers=headers,
                json={
                    "action": "send",
                    "app": app["id"],
                    "request": "http-round-trip",
                    "text": "你好",
                },
            ).json()
            row = wait(manager.applications, accepted["run"])
            assert row["status"] == "completed", row
            history = client.get(
                url, params={"action": "history", "app": app["id"], "session": accepted["session"]}
            ).json()
            assert history["items"][0]["answer"] == "真实适配器已接通"
            assert len(requests) == 1
            assert not requests[0].get("tools")
            assert sum(m.get("content") == app["prompt"] for m in requests[0]["messages"]) == 1
            assert client.get(url, params={"action": "detail", "app": "missing"}).status_code == 404
        with closing(sqlite3.connect(manager.applications.execution.ledger)) as db:
            assert db.execute("SELECT input_tokens,output_tokens FROM usage_events").fetchone() == (
                77,
                8,
            )
    finally:
        http.shutdown()
        http.server_close()
        http_worker.join()
        upstream.shutdown()
        upstream.server_close()
        worker.join()


def test_crash_reconciliation_and_history_pagination(manager):
    apps = manager.applications
    app = apps.store.save(values())
    session = apps.store.new_session(app["id"], "/unused", "history")
    for i in range(45):
        run, _ = apps.store.accept(session["id"], f"request-{i}", app, f"问题{i}")
        apps.store.update_run(run["id"], status="completed", answer=f"回答{i}")
    newest = apps.get({"action": "history", "app": app["id"], "session": session["id"]})
    assert len(newest["items"]) == 20 and newest["has_more"]
    older = apps.get(
        {
            "action": "history",
            "app": app["id"],
            "session": session["id"],
            "before": newest["items"][0]["created"],
        }
    )
    assert len(older["items"]) == 20 and older["has_more"]
    oldest = apps.get(
        {
            "action": "history",
            "app": app["id"],
            "session": session["id"],
            "before": older["items"][0]["created"],
        }
    )
    assert len(oldest["items"]) == 5 and not oldest["has_more"]
    # Simulate persisted admission without a surviving execution owner.
    apps.close()
    run, _ = apps.store.accept(session["id"], "interrupted", app, "不能丢失的提问")
    replacement = Applications(manager)
    try:
        history = replacement.get({"action": "history", "app": app["id"], "session": session["id"]})
        last = history["items"][-1]
        assert last["id"] == run["id"] and last["status"] == "failed"
        assert last["input"] == "不能丢失的提问" and "未自动重发" in last["error"]
    finally:
        replacement.close()


def test_host_learning_ceiling_preserves_work_skills(manager):
    from corki.core.runtime import LangGraphRuntime

    config = json.loads(manager.config.read_text())
    config["features"] = {"self_evolution": True}
    manager.config.write_text(json.dumps(config))
    skill = manager.home / "skills/work/explicit-only/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: explicit-only\ndescription: Test explicit skill\n---\n"
        "Use only when requested.\n"
    )

    async def factory(**kw):
        runtime = await LangGraphRuntime.acreate(**kw, model=FinalModel())
        assert runtime._evolution is None
        snapshot = runtime._skill_service.snapshot(kw["settings"].working_directory)
        found = next(s for s in snapshot.skills if s.name == "explicit-only")
        assert found.work_only and not found.allow_implicit_invocation
        return runtime

    class FinalModel:
        async def stream(self, request):
            yield ModelCompleted((AssistantMessageItem("完成", request.items[-1].turn_id, "step"),))

        async def aclose(self):
            pass

    apps = manager.applications
    apps.execution.runtime_factory = factory
    app = apps.store.save({**values(), "type": "agent"})
    submitted = apps.mutate(
        {"action": "send", "app": app["id"], "request": "ceiling", "text": "hello"}
    )
    row = wait(apps, submitted["run"])
    assert row["status"] == "completed", row


def test_agent_construction_failure_keeps_input(manager):
    apps = manager.applications

    async def fail(**kw):
        raise RuntimeError("fixture initialization failure")

    apps.execution.runtime_factory = fail
    app = apps.store.save({**values(), "type": "agent"})
    submitted = apps.mutate(
        {"action": "send", "app": app["id"], "request": "init-failed", "text": "请不要丢失这个问题"}
    )
    assert wait(apps, submitted["run"])["status"] == "failed"
    view = apps.get({"action": "history", "app": app["id"], "session": submitted["session"]})
    assert view["items"][0]["input"] == "请不要丢失这个问题"


@pytest.mark.parametrize("content", ["{invalid", '{"providers":[]}', "{}"])
def test_invalid_or_missing_model_config_does_not_break_editing(manager, content):
    apps = manager.applications
    app = apps.store.save(values())
    manager.config.write_text(content)
    catalog = apps.get({"action": "catalog"})
    assert catalog["providers"] == []
    assert apps.get({"action": "detail", "app": app["id"]})["name"] == app["name"]
    with pytest.raises(AppError):
        apps.mutate({"action": "send", "app": app["id"], "request": "invalid-config", "text": "hi"})


def test_child_usage_recovery_and_delete_owned_descendants(manager):
    from corki.protocol.ids import new_thread_id
    from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource
    from corki.storage.usage_ledger import record_external_usage

    apps = manager.applications
    app = apps.store.save({**values(), "type": "agent"})
    session = apps.store.new_session(app["id"], str(manager.home), "parent")
    apps.execution.start()
    repo = apps.execution.repositories[0]
    apps.execution.call(
        repo.create_thread(
            session["id"],
            manager.home,
            session_source=SessionSource.from_startup_arg("application"),
        )
    )
    child = new_thread_id()
    apps.execution.call(
        repo.create_thread(
            child,
            manager.home,
            session_source=SessionSource.subagent(
                SubAgentSource("thread_spawn", ThreadSpawnSource(session["id"], 1))
            ),
        )
    )
    record_external_usage(
        apps.execution.database,
        step_id="child-fixture-step",
        thread_id=child,
        turn_id="child-turn",
        created_at=server.datetime.now(server.UTC).isoformat(),
        provider="local",
        model="test",
        usage=ModelUsage(input_tokens=99, output_tokens=9),
    )
    # Startup reconciliation copies facts left in the core store after a crash.
    apps.close()
    recovered = Applications(manager)
    try:
        recovered.execution.start()
        with closing(sqlite3.connect(recovered.execution.ledger)) as db:
            assert db.execute("SELECT sum(input_tokens) FROM usage_events").fetchone()[0] == 99
            assert db.execute(
                "SELECT kind,channel FROM usage_threads WHERE thread_id=?", (child,)
            ).fetchone() == ("application", app["id"])
        recovered.mutate({"action": "delete", "app": app["id"], "stop": True})
        with closing(sqlite3.connect(recovered.execution.database)) as db:
            assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
        with closing(sqlite3.connect(recovered.execution.ledger)) as db:
            assert db.execute("SELECT count(*) FROM usage_events").fetchone()[0] == 1
    finally:
        recovered.close()


def test_real_agent_stop_keeps_partial_then_accepts_next_turn(manager):
    from corki.core.runtime import LangGraphRuntime

    calls = []

    class Model:
        async def stream(self, request):
            calls.append(request)
            if len(calls) == 1:
                yield ModelTextDelta("这是已经生成的内容")
                await asyncio.sleep(100)
            else:
                yield ModelCompleted(
                    (AssistantMessageItem("新的回答", request.items[-1].turn_id, "step"),)
                )

        async def aclose(self):
            pass

    async def factory(**kw):
        return await LangGraphRuntime.acreate(**kw, model=Model())

    apps = manager.applications
    apps.execution.runtime_factory = factory
    app = apps.store.save({**values(), "type": "agent"})
    submitted = apps.mutate(
        {"action": "send", "app": app["id"], "request": "agent-stop", "text": "请生成内容"}
    )
    for _ in range(500):
        view = apps.execution.snapshot(submitted["run"])
        if view and view["messages"]:
            break
        time.sleep(0.01)
    assert view and view["messages"]
    apps.mutate(
        {
            "action": "stop",
            "app": app["id"],
            "session": submitted["session"],
            "run": submitted["run"],
        }
    )
    assert wait(apps, submitted["run"])["status"] == "cancelled"
    history = apps.get({"action": "history", "app": app["id"], "session": submitted["session"]})
    assert any("已经生成" in m["content"] for m in history["items"][0]["messages"])
    next_run = apps.mutate(
        {
            "action": "send",
            "app": app["id"],
            "session": submitted["session"],
            "request": "after-stop",
            "text": "继续",
        }
    )
    assert wait(apps, next_run["run"])["status"] == "completed"
