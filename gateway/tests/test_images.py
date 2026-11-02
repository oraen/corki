"""Authorized image input reaches the existing runtime; previews remain account-owned."""

import asyncio
import hashlib
import io
import json

import pytest
from corki_gateway.history import history
from corki_gateway.images import validate_refs
from corki_gateway.service import Host
from corki_gateway.store import Store
from PIL import Image


def account(store, name="Fixture", **values):
    channel = store.save_connection(
        {
            "channel": "dingtalk",
            "name": name,
            "values": {
                "client_id": "fixture-" + name,
                "client_secret": "fixture-secret",
                "session_validation": "false",
                **values,
            },
        }
    )
    store.enable(channel, True)
    return channel


def message():
    text = "请分析🦀表格\n[Image #1]\n"
    return {
        "id": "fixture-message",
        "sender": "fixture-sender",
        "name": "Fixture user",
        "staff_id": "fixture-user",
        "conversation": "fixture-room",
        "kind": "direct",
        "text": text,
        "image_refs": [{"download_code": "fixture-private-code"}],
        "image_positions": [text.index("[Image #1]")],
    }


class Transport:
    def __init__(self, host, channel, raw=None):
        self.calls = []
        self.host = host
        self.channel = channel
        self.error = None
        if raw is None:
            stream = io.BytesIO()
            with Image.new("RGB", (200, 80), "orange") as image:
                image.save(stream, format="PNG")
            raw = stream.getvalue()
        self.raw = raw

    async def call(self, method, params, **_):
        self.calls.append((method, params))
        if method == "download_images":
            if self.error:
                return {"images": [{"error": self.error}]}
            identity = hashlib.sha256(self.raw).hexdigest()
            root = self.host.store.images.directory(self.channel)
            root.mkdir(parents=True, exist_ok=True)
            (root / (identity + ".bin")).write_bytes(self.raw)
            return {"images": [{"id": identity, "size": len(self.raw)}]}
        return {"sent": True}


def test_authorized_images_pass_attachment_objects_and_previews_persist(tmp_path, monkeypatch):
    async def scenario():
        inputs = []

        async def run(thread, text, workdir, channel, **kwargs):
            inputs.append((text, kwargs))
            return "Image understood"

        h = Host(tmp_path, runner=run, online=False)
        channel = account(h.store)
        transport = Transport(h, channel)
        h.connectors[channel] = transport
        try:
            await h.inbound(channel, message())
            await asyncio.gather(*h.tasks.values())
            assert len(inputs) == 1
            assert inputs[0][1]["attachments"][0].data_url.startswith("data:image/png;base64,")
            assert inputs[0][1]["image_positions"] == message()["image_positions"]
            view = history(Store(tmp_path), channel, {}, {})
            image = view["messages"][0]["images"][0]
            assert (
                image["status"] == "ready" and image["position"] == message()["image_positions"][0]
            )
            assert image["url"].startswith("/api/channel-image?")
            assert "fixture-private-code" not in json.dumps(view)
            assert "base64" not in json.dumps(view)
            info = h.store.images.info(channel, image["id"])
            thumb = h.store.images.info(channel, image["id"], True)
            from corki_gateway.control import control

            with monkeypatch.context() as patch:

                def forbidden_init(*_args, **_kwargs):
                    raise AssertionError("Preview reads must not initialize or migrate the DB")

                patch.setattr(Store, "__init__", forbidden_init)
                assert (
                    control(
                        tmp_path, {"action": "image-info", "id": channel, "asset": image["id"]}
                    )["mime"]
                    == "image/png"
                )
            assert info["mime"] == "image/png" and thumb["mime"] == "image/jpeg"
            with Image.open(thumb["path"]) as preview:
                assert preview.width <= 360
            other = account(h.store, "Other")
            with pytest.raises(ValueError):
                h.store.images.info(other, image["id"])
            with pytest.raises(ValueError):
                h.store.images.info(channel, "../settings.json")
        finally:
            await h.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["invalid-bytes", "download-denied", "unauthorized"])
def test_failed_or_unauthorized_images_are_not_silently_sent_to_model(tmp_path, failure):
    async def scenario():
        calls = []

        async def run(*_args, **_kwargs):
            calls.append(True)
            return "unexpected"

        h = Host(tmp_path, runner=run, online=False)
        channel = account(
            h.store,
            session_validation="true" if failure == "unauthorized" else "false",
            ai_card_enabled="true",
            ai_card_template="fixture-template.schema",
        )
        transport = Transport(h, channel, b"not an image" if failure == "invalid-bytes" else None)
        if failure == "download-denied":
            transport.error = "图片下载失败（HTTP 403） Forbidden.AccessDenied"
        h.connectors[channel] = transport
        try:
            await h.inbound(channel, message())
            await asyncio.gather(*h.tasks.values(), *h.access_notifier.tasks.values())
            assert not calls
            view = history(h.store, channel, {}, {})
            image = view["messages"][0]["images"][0]
            assert image["status"] == "failed"
            assert any(r["role"] == "assistant" for r in view["messages"])
            if failure == "unauthorized":
                assert not any(c[0] == "download_images" for c in transport.calls)
            if failure == "download-denied":
                assert "403" in image["error"]
                assert not any(c[0] == "progress" for c in transport.calls)
            if failure == "invalid-bytes":
                assert not (
                    h.store.images.directory(channel)
                    / (hashlib.sha256(transport.raw).hexdigest() + ".bin")
                ).exists()
        finally:
            await h.close()

    asyncio.run(scenario())


def test_image_marker_validation_and_cancelled_download_state(tmp_path):
    with pytest.raises(ValueError):
        validate_refs({**message(), "image_positions": [0]})

    async def scenario():
        h = Host(tmp_path, runner=lambda *_: None, online=False)
        channel = account(h.store)
        started = asyncio.Event()

        class Slow:
            async def call(self, method, params, **_):
                if method == "download_images":
                    started.set()
                    await asyncio.Future()
                return {"sent": True}

        h.connectors[channel] = Slow()
        await h.inbound(channel, message())
        await started.wait()
        await h.close()
        image = history(h.store, channel, {}, {})["messages"][0]["images"][0]
        assert image["status"] == "failed" and "停止" in image["error"]

    asyncio.run(scenario())


@pytest.mark.parametrize("supports_images", [True, False])
def test_real_runtime_receives_images_in_existing_input_protocol(
    tmp_path, monkeypatch, supports_images
):
    from corki.core.runtime import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
    from corki.protocol.tools import ImageAttachment

    monkeypatch.setenv("CORKI_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "default": {"provider": "fixture", "model": "fixture"},
                "providers": {
                    "fixture": {
                        "base_url": "http://127.0.0.1:1/v1",
                        "api_key": "fixture-not-a-key",
                        "supports_image_input": supports_images,
                        "models": {
                            "fixture": {
                                "context_window": 262144,
                                "supports_image_input": supports_images,
                            }
                        },
                    }
                },
                "skills": {"enabled": False},
                "features": {"goals": False},
            }
        )
    )
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            user = next(i for i in reversed(request.items) if isinstance(i, UserMessageItem))
            assert any(isinstance(p, ImageAttachment) for p in user.content_items)
            yield ModelCompleted(
                (AssistantMessageItem("Visual input received", user.turn_id, new_step_id()),)
            )

        async def aclose(self):
            pass

    factory = LangGraphRuntime.acreate

    async def create(**kwargs):
        return await factory(**kwargs, model=Model(), compatibility_home=tmp_path)

    monkeypatch.setattr(LangGraphRuntime, "acreate", create)

    async def scenario():
        h = Host(tmp_path, online=False)
        await h.start()
        channel = account(h.store)
        h.connectors[channel] = Transport(h, channel)
        try:
            await h.inbound(channel, message())
            await asyncio.gather(*h.tasks.values())
            if not supports_images:
                assert not requests
                assert (
                    "未声明支持图片" in history(h.store, channel, {}, {})["messages"][-1]["content"]
                )
                return
            assert len(requests) == 1
            assert (
                history(h.store, channel, {}, {})["messages"][-1]["content"]
                == "Visual input received"
            )
        finally:
            await h.close()

    asyncio.run(scenario())
