"""Real Corki runtime, fake model only. No external messages or user data."""

import asyncio
import json

from corki_gateway.service import Host


def test_real_runtime_tools_and_history(tmp_path, monkeypatch):
    from corki.core.runtime import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import (
        AssistantMessageItem,
        ToolCallItem,
        ToolResultItem,
        UserMessageItem,
        new_step_id,
    )
    from corki.protocol.tools import ToolCall

    monkeypatch.setenv("CORKI_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    config = {
        "default": {"provider": "fixture", "model": "fixture"},
        "providers": {
            "fixture": {
                "base_url": "http://127.0.0.1:1/v1",
                "api_mode": "chat_completions",
                "api_key": "fixture-not-a-credential",
                "models": {"fixture": {"context_window": 262144}},
            }
        },
        "skills": {"enabled": False},
        "features": {"goals": False},
    }
    (tmp_path / "settings.json").write_text(json.dumps(config))
    config["providers"]["chosen"] = {
        **config["providers"]["fixture"],
        "models": {"custom-model": {"context_window": 131072}},
    }
    (tmp_path / "settings.json").write_text(json.dumps(config))
    settings_before = (tmp_path / "settings.json").read_bytes()
    requests, selected_models = [], []

    class Model:
        calls = 0

        async def stream(self, request):
            requests.append(request)
            turn, step = request.items[-1].turn_id, new_step_id()
            self.calls += 1
            if self.calls == 1:
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(
                                "plan-" + turn,
                                "update_plan",
                                {"plan": [{"step": "Check gateway", "status": "completed"}]},
                            ),
                            turn,
                            step,
                        ),
                    )
                )
            else:
                assert any(
                    isinstance(i, ToolResultItem) and i.turn_id == turn for i in request.items
                )
                yield ModelCompleted((AssistantMessageItem("Gateway result", turn, step),))

        async def aclose(self):
            pass

    factory = LangGraphRuntime.acreate

    async def create(**kwargs):
        selected_models.append((kwargs["settings"].provider_id, kwargs["settings"].model))
        return await factory(**kwargs, model=Model(), compatibility_home=tmp_path)

    monkeypatch.setattr(LangGraphRuntime, "acreate", create)

    async def scenario():
        host = Host(tmp_path)
        await host.start()
        assert (
            await host.runner("fixture-thread", "First question", str(tmp_path), "fixture")
            == "Gateway result"
        )
        assert (
            await host.runner("fixture-thread", "Second question", str(tmp_path), "fixture")
            == "Gateway result"
        )
        texts = [i.content for i in requests[-1].items if isinstance(i, UserMessageItem)]
        assert "First question" in texts and "Second question" in texts
        identity = host.store.save_connection(
            {"channel": "wechat", "name": "Reader", "values": {}}
        )
        with host.store.db() as db:
            db.execute(
                "INSERT INTO user_sessions(binding,sender,name,thread) VALUES(?,?,?,?)",
                (host.store.connection(identity)["conversation_id"], "reader", "Reader", "fixture-thread"),
            )
        view = await host.change({"action": "conversation", "id": identity})
        assert [m["content"] for m in view["messages"] if m["role"] == "user"] == [
            "First question",
            "Second question",
        ]
        assert [m["content"] for m in view["messages"] if m["role"] == "assistant"] == [
            "Gateway result",
            "Gateway result",
        ]
        repository = host.runner.__self__.repository
        await repository.append_items(
            "fixture-thread",
            tuple(UserMessageItem(f"history-{i}", "fixture-turn") for i in range(130)),
        )
        latest = await host.change({"action": "conversation", "id": identity})
        assert latest["before"] is not None and len(latest["messages"]) == 100
        older = await host.change(
            {
                "action": "conversation",
                "id": identity,
                "thread": latest["thread"],
                "before": latest["before"],
            }
        )
        assert not ({m["id"] for m in latest["messages"]} & {m["id"] for m in older["messages"]})
        await host.change({"action": "reset-conversation", "id": identity})
        assert not (await host.change({"action": "conversation", "id": identity}))["messages"]
        assert len(await repository.load_items("fixture-thread")) > 130
        import pytest

        with pytest.raises(ValueError, match="重置"):
            await host.change(
                {"action": "conversation", "id": identity, "thread": "fixture-thread", "before": 1}
            )
        with pytest.raises(ValueError, match="不存在"):
            await host.change({"action": "conversation", "id": "unknown"})
        assert selected_models == [("fixture", "fixture"), ("fixture", "fixture")]
        binding = host.store.connection(identity)["conversation_id"]
        await host.change({"action": "save-connection", "id": identity, "name": "Reader",
                           "values": {"agent_provider": "chosen", "agent_model": "custom-model"}})
        assert host.store.connection(identity)["conversation_id"] == binding
        await host.runner("model-thread", "Selected model question", str(tmp_path), identity)
        assert selected_models[-1] == ("chosen", "custom-model")
        await host.change({"action": "save-connection", "id": identity, "name": "Reader",
                           "values": {"agent_provider": "", "agent_model": ""}})
        await host.runner("model-thread", "Global model question", str(tmp_path), identity)
        assert selected_models[-1] == ("fixture", "fixture")
        assert "Selected model question" in [
            i.content for i in requests[-1].items if isinstance(i, UserMessageItem)
        ]
        assert (tmp_path / "settings.json").read_bytes() == settings_before
        assert not host.runtimes
        await host.close()

    asyncio.run(scenario())


def test_latest_connection_failure_keeps_bounded_response_details(tmp_path):
    host = Host(tmp_path, runner=lambda *args: None)
    host.connector_status(
        "fixture",
        {
            "state": "error",
            "code": "http",
            "detail": {
                "status": 403,
                "url": "https://example.com/login",
                "method": "POST",
                "response": "blocked" * 2000,
                "headers": "must not be copied",
            },
        },
    )
    host.connector_status("fixture", {"state": "error", "code": "reconnect"})
    detail = host.statuses["fixture"]["detail"]
    assert detail["status"] == 403 and len(detail["response"]) == 8192
    assert "headers" not in detail
    host.connector_status(
        "fixture",
        {
            "state": "error",
            "code": "network",
            "detail": {"error": "TimeoutError"},
        },
    )
    assert host.statuses["fixture"]["detail"]["error"] == "TimeoutError"
    assert "status" not in host.statuses["fixture"]["detail"]
