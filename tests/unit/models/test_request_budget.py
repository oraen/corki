import asyncio

import httpx
import pytest

from corki.models.base import ModelError
from corki.models.capabilities import ApiMode, ProviderCapabilities
from corki.models.openai_compatible import OpenAICompatibleModel
from corki.models.types import ModelRequest


@pytest.mark.parametrize("base", ["https://api.example.invalid/qoder", "http://localhost:17001/openai"])
def test_other_transports_never_serialize_or_contact_a_budget_endpoint(base):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("no request"))) as client:
            model = OpenAICompatibleModel(api_key="fixture-key", base_url=base, client=client,
                capabilities=ProviderCapabilities("fixture", ApiMode.CHAT_COMPLETIONS))
            model._build_payload = lambda _: pytest.fail("no serialization")
            assert await model.request_byte_budget(ModelRequest("fixture", "", (), (), ())) is None
    asyncio.run(run())


@pytest.mark.parametrize("invalid", [{"limit_bytes": 0}, {"request_bytes": True},
                                   {"fixed_bytes": 300}, {"protocol_version": 2}])
def test_invalid_gateway_budget_is_rejected(invalid):
    async def run():
        def handle(request):
            assert request.url.path == "/qoder/v1/request-budget"
            assert request.headers["Authorization"] == "Bearer fixture-key"
            return httpx.Response(200, json={"protocol_version": 1, "request_bytes": 200,
                "fixed_bytes": 50, "limit_bytes": 100, **invalid})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            model = OpenAICompatibleModel(api_key="fixture-key", base_url="http://127.0.0.1:17001/qoder/v1",
                client=client, capabilities=ProviderCapabilities("fixture", ApiMode.CHAT_COMPLETIONS))
            with pytest.raises(ModelError, match="无效的请求大小"):
                await model.request_byte_budget(ModelRequest("fixture", "", (), (), ()))
    asyncio.run(run())


def test_old_gateway_reports_upgrade_without_sending_a_model_request():
    async def run():
        paths = []
        def handle(request):
            paths.append(request.url.path)
            return httpx.Response(404)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            model = OpenAICompatibleModel(api_key="fixture-key", base_url="http://localhost:17001/qoder",
                client=client, capabilities=ProviderCapabilities("fixture", ApiMode.CHAT_COMPLETIONS))
            with pytest.raises(ModelError, match="更新并重启"):
                await model.request_byte_budget(ModelRequest("fixture", "", (), (), ()))
        assert paths == ["/qoder/request-budget"]
    asyncio.run(run())
