"""The real Agent runtime reports categorized failures without exposing model payloads."""

import asyncio
import json

import pytest
from corki_gateway.agent import Agent
from corki_gateway.service import Host

from corki.core.runtime import LangGraphRuntime
from corki.models import ModelError, ModelErrorKind


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ModelErrorKind.TRANSPORT, "模型通信失败"),
        (ModelErrorKind.AUTHENTICATION, "模型认证失败"),
        (ModelErrorKind.SERVER, "模型服务暂时不可用"),
    ],
)
def test_agent_failure_has_a_safe_actionable_reason(tmp_path, monkeypatch, kind, expected):
    monkeypatch.setenv("CORKI_HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "default": {"provider": "fixture", "model": "fixture"},
                "providers": {
                    "fixture": {
                        "base_url": "https://fixture.invalid",
                        "api_key": "fixture",
                        "models": {"fixture": {"context_window": 262144}},
                    }
                },
                "skills": {"enabled": False},
                "features": {"goals": False, "plugins": False},
                "memories": {"generate": False, "use": False},
            }
        )
    )

    class Model:
        async def stream(self, _request):
            raise ModelError("fixture-private-error-payload", kind=kind, retryable=False)
            yield  # Async iterator contract; the failure happens before any output.

        async def aclose(self):
            pass

    factory = LangGraphRuntime.acreate

    async def create(**kwargs):
        return await factory(**kwargs, model=Model(), compatibility_home=tmp_path)

    monkeypatch.setattr(LangGraphRuntime, "acreate", create)

    async def scenario():
        host = Host(tmp_path, online=False)
        agent = Agent(host)
        try:
            with pytest.raises(ValueError, match=expected) as caught:
                await agent.run("fixture-thread", "hello", str(tmp_path), "fixture-channel")
            assert "fixture-private-error-payload" not in str(caught.value)
            assert not host.runtimes
        finally:
            await host.close()

    asyncio.run(scenario())
