"""WeChat image input, private previews, manual images and Agent-generated replies."""

import asyncio
import base64
import hashlib
import io
import json
import sys

import pytest
from corki_gateway.agent import Agent
from corki_gateway.history import history
from corki_gateway.service import Host
from corki_gateway.store import Store
from PIL import Image


def png(color="orange"):
    out = io.BytesIO()
    with Image.new("RGB", (40, 20), color) as image:
        image.save(out, "PNG")
    return out.getvalue()


def account(store, name="WeChat"):
    channel = store.save_connection({"channel": "wechat", "name": name, "values": {}})
    store.enable(channel, True)
    return channel


def incoming():
    prefix = "看看这个😀\n"
    return {
        "id": "image-one",
        "sender": "alice",
        "conversation": "alice",
        "kind": "direct",
        "text": prefix + "[Image #1]",
        "context_token": "private-context",
        "image_refs": [{"query": "private-cdn-reference", "aes_hex": "11" * 16}],
        "image_positions": [len(prefix)],
    }


class Transport:
    def __init__(self, host, channel, raw=None):
        self.host, self.channel, self.raw = host, channel, png() if raw is None else raw
        self.calls = []

    async def call(self, method, params, **_):
        self.calls.append((method, params))
        if method == "download_images":
            identity = hashlib.sha256(self.raw).hexdigest()
            root = self.host.store.images.directory(self.channel)
            root.mkdir(parents=True, exist_ok=True)
            (root / (identity + ".bin")).write_bytes(self.raw)
            return {"images": [{"id": identity}]}
        return {"sent": True}


@pytest.mark.parametrize("valid", [True, False])
def test_receive_images_reaches_agent_and_preserves_previews(tmp_path, valid):
    async def scenario():
        inputs = []

        async def run(_thread, text, *_args, **kwargs):
            inputs.append((text, kwargs))
            return "识别完成"

        host = Host(tmp_path, runner=run, online=False)
        channel = account(host.store)
        transport = Transport(host, channel, None if valid else b"not an image")
        host.connectors[channel] = transport
        try:
            await host.inbound(channel, incoming())
            await asyncio.gather(*host.tasks.values())
            view = history(host.store, channel, {}, {})
            image = view["messages"][0]["images"][0]
            if valid:
                assert inputs[0][1]["attachments"][0].data_url.startswith("data:image/png;base64,")
                assert inputs[0][1]["image_positions"] == incoming()["image_positions"]
                assert image["status"] == "ready"
                assert image["url"].startswith("/api/channel-image?")
                assert host.store.images.info(channel, image["id"])["mime"] == "image/png"
                restored = history(Store(tmp_path), channel, {}, {})
                assert restored["messages"][0]["images"][0]["id"] == image["id"]
                other = account(host.store, "Other")
                with pytest.raises(ValueError):
                    host.store.images.info(other, image["id"])
            else:
                assert not inputs and image["status"] == "failed"
                assert "图片" in image["error"]
            public = json.dumps(view)
            assert "private-cdn-reference" not in public and "private-context" not in public
        finally:
            await host.close()

    asyncio.run(scenario())


def test_image_tool_preserves_permissions_and_does_not_send_on_denial(tmp_path, monkeypatch):
    from corki_gateway.wechat_tools import SendWeChatImage

    from corki.protocol.tools import ToolCall
    from corki.tools.base import ToolContext

    permissions = object()
    observed = []

    async def deny(selected, cwd, operation, arguments, **_):
        observed.append((selected, cwd, operation, arguments))
        raise PermissionError("image read denied by session policy")

    monkeypatch.setattr("corki_gateway.wechat_tools.file_operation", deny)

    async def scenario():
        host = Host(tmp_path, online=False)
        channel = account(host.store)
        host.store.wechat_messages.inbound(host.store.connection(channel), incoming())
        thread = history(host.store, channel, {}, {})["thread"]
        transport = Transport(host, channel)
        host.connectors[channel] = transport
        host.deliveries[thread] = (channel, incoming())
        try:
            tool = SendWeChatImage(host, channel, thread)
            context = ToolContext(cwd=tmp_path, execution_permissions=permissions)
            call = ToolCall("denied-image", "send_wechat_image", {"path": "private.png"})
            with pytest.raises(PermissionError, match="session policy"):
                await tool.execute(call, context)
            assert observed == [
                (
                    permissions,
                    tmp_path,
                    "image",
                    {"path": str(tmp_path / "private.png"), "max_bytes": 16 * 1024 * 1024},
                )
            ]
            assert not transport.calls
            assert not list(tmp_path.rglob("*.bin"))
        finally:
            await host.close()

    asyncio.run(scenario())


def test_manual_image_upload_only_and_caption_idempotency(tmp_path):
    async def scenario():
        host = Host(tmp_path, online=False)
        channel = account(host.store)
        host.store.wechat_messages.inbound(
            host.store.connection(channel),
            {**incoming(), "image_refs": [], "image_positions": [], "text": "Hello"},
        )
        transport = Transport(host, channel)
        host.connectors[channel] = transport
        try:
            image = (
                await host.change(
                    {
                        "action": "image-upload",
                        "id": channel,
                        "image": base64.b64encode(png()).decode(),
                    }
                )
            )["image"]
            thread = history(host.store, channel, {}, {})["thread"]
            for index, text in enumerate(("", "看看这张图")):
                request = {
                    "action": "manual-send",
                    "id": channel,
                    "session": thread,
                    "request_id": "image-request-00000" + str(index),
                    "text": text,
                    "images": [image["id"]],
                }
                result = await host.change(request)
                assert result["message"]["status"] == "sent"
                assert result["message"]["images"][0]["id"] == image["id"]
                await host.change(request)
                assert len(transport.calls) == index + 1
                assert transport.calls[-1][1]["images"][0]["id"] == image["id"]
                with pytest.raises(ValueError, match="其他消息"):
                    await host.change({**request, "text": "different content", "images": []})
            with pytest.raises(ValueError):
                await host.change({"action": "image-upload", "id": channel, "image": "bad-base64"})
            other = account(host.store, "Other")
            with pytest.raises(ValueError):
                host.store.images.send_assets(other, [image["id"]])
            assert not host.tasks
        finally:
            await host.close()

    asyncio.run(scenario())


def test_real_agent_can_create_then_send_image_with_configured_permissions(tmp_path, monkeypatch):
    from corki.core.runtime import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
    from corki.protocol.tools import ToolCall

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
                "default_permissions": ":danger-full-access",
                "approval_policy": "never",
                "skills": {"enabled": False},
                "features": {"goals": False, "plugins": False},
                "memories": {"generate": False, "use": False},
            }
        )
    )
    image = tmp_path / "generated screenshot.png"
    script = tmp_path / "generate.py"
    encoded_png = base64.b64encode(png()).decode()
    script.write_text(
        "from pathlib import Path\nimport base64\n"
        + f"Path({str(image)!r}).write_bytes(base64.b64decode({encoded_png!r}))\n"
    )
    command = (
        "& " + " ".join("'" + str(p).replace("'", "''") + "'" for p in (sys.executable, script))
        if sys.platform == "win32"
        else __import__("shlex").join([sys.executable, str(script)])
    )
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            turn, step = request.items[-1].turn_id, new_step_id()
            assert any(t.name == "send_wechat_image" for t in request.tools)
            if len(requests) == 1:
                call = ToolCall("create-image", "exec_command", {"cmd": command, "login": False})
                yield ModelCompleted((ToolCallItem(call, turn, step),))
            elif len(requests) == 2:
                assert image.exists()
                call = ToolCall("send-image", "send_wechat_image", {"path": str(image)})
                yield ModelCompleted((ToolCallItem(call, turn, step),))
            else:
                result = next(
                    i
                    for i in request.items
                    if isinstance(i, ToolResultItem) and i.tool_name == "send_wechat_image"
                )
                assert json.loads(result.content)["sent"] is True
                yield ModelCompleted((AssistantMessageItem("图片已发送", turn, step),))

        async def aclose(self):
            pass

    factory = LangGraphRuntime.acreate

    async def create(**kwargs):
        return await factory(**kwargs, model=Model(), compatibility_home=tmp_path)

    monkeypatch.setattr(LangGraphRuntime, "acreate", create)

    async def scenario():
        host = Host(tmp_path, online=False)
        host.runner = Agent(host).run
        channel = account(host.store)
        host.connectors[channel] = Transport(host, channel)
        try:
            message = {**incoming(), "image_refs": [], "image_positions": [], "text": "截图发给我"}
            await host.inbound(channel, message)
            await asyncio.wait_for(asyncio.gather(*host.tasks.values()), 30)
            calls = host.connectors[channel].calls
            assert any(params.get("images") for method, params in calls if method == "send")
            view = history(host.store, channel, {}, {})
            assert len([m for m in view["messages"] if m["images"]]) == 1
            assert image.exists() and len(requests) == 3
            assert not host.interactions
        finally:
            await host.close()

    asyncio.run(scenario())
