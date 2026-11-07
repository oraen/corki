"""Telegram/WhatsApp contacts, media, isolation, approvals and durable manual sends."""

import asyncio
import base64
import io

import pytest
from corki_gateway.history import history
from corki_gateway.service import Host
from PIL import Image


def account(store, platform, *, validation=False):
    values = {"session_validation": "true" if validation else "false"}
    if platform == "telegram":
        values["token"] = "fixture-token"
    identity = store.save_connection({"channel": platform, "name": platform, "values": values})
    store.enable(identity, True)
    store.account(identity, "fixture-bot")
    return identity


def message(identity="one", sender="1001", text="Hello"):
    return {
        "id": identity,
        "sender": sender,
        "conversation": sender,
        "kind": "direct",
        "name": "Alice" if sender == "1001" else "Bob",
        "text": text,
    }


class Transport:
    def __init__(self):
        self.sent = []
        self.error = False

    async def call(self, method, params, **_):
        if method in ("download_images", "download_files"):
            return {"images": params.get("images", params.get("files"))}
        if method == "send":
            self.sent.append(params)
            if self.error:
                raise ValueError("fixture failure")
        return {"sent": True, "supported": True}


@pytest.mark.parametrize("platform", ["telegram", "whatsapp"])
def test_private_media_and_two_user_threads_manual_send_reset_and_rebind(
    tmp_path, monkeypatch, platform
):
    monkeypatch.setattr("corki_gateway.wechat_inbox.PAIR_WAIT", 0.01)

    async def scenario():
        inputs = []

        async def runner(thread, text, *_args, **kwargs):
            inputs.append((thread, text, kwargs))
            return "Answer " + text

        host = Host(tmp_path, runner=runner, online=False)
        channel = account(host.store, platform)
        transport = Transport()
        host.connectors[channel] = transport
        buffer = io.BytesIO()
        Image.new("RGB", (20, 10), "blue").save(buffer, "PNG")
        image = host.store.images.save_bytes(channel, buffer.getvalue())
        file = host.store.wechat_files.save_bytes(channel, b"fixture-document", "说明.txt")
        try:
            await host.inbound(
                channel,
                {
                    **message("image", text="A😀\n[Image #1]"),
                    "image_refs": [{"id": image["id"]}],
                    "image_positions": [3],
                },
            )
            await host.inbound(
                channel,
                {
                    **message("file", "1002", "B\n[File #1]"),
                    "file_refs": [{"id": file["id"], "kind": "file", "name": "说明.txt"}],
                    "file_positions": [2],
                },
            )
            await asyncio.gather(*list(host.tasks.values()))
            assert len(inputs) == 2 and inputs[0][0] != inputs[1][0]
            alice_input = next(r for r in inputs if r[1].startswith("A"))
            bob_input = next(r for r in inputs if r[1].startswith("B"))
            assert alice_input[2]["attachments"] and alice_input[2]["image_positions"] == [3]
            assert str(host.store.wechat_files.file(channel, file["id"])) in bob_input[1]
            contacts = host.store.applications(channel)["requests"]
            assert {r["sender_id"] for r in contacts} == {"1001", "1002"}
            old_contact_id = next(
                r["conversation_id"] for r in contacts if r["sender_id"] == "1001"
            )
            alice = history(host.store, channel, {"sender": "1001"}, {})
            bob = history(host.store, channel, {"sender": "1002"}, {})
            assert alice["can_send"] and bob["can_send"]
            assert all("B\n" not in m["content"] for m in alice["messages"])
            assert alice["messages"][0]["images"][0]["status"] == "ready"
            assert bob["messages"][0]["files"][0]["status"] == "ready"
            request = {
                "action": "manual-send",
                "id": channel,
                "session": alice["thread"],
                "request_id": "fixture-manual-request",
                "text": "",
                "images": [image["id"]],
                "files": [file["id"]],
            }
            before = len(transport.sent)
            result = await host.change(request)
            await host.change(request)
            assert len(transport.sent) == before + 1
            assert transport.sent[-1]["message"]["sender"] == "1001"
            assert result["message"]["status"] == "sent" and result["message"]["files"]
            assert len(inputs) == 2
            host.store.reset_current_session(channel, alice["thread"])
            with pytest.raises(ValueError):
                await host.change({**request, "request_id": "after-reset-request"})
            assert history(host.store, channel, {"sender": "1002"}, {})["thread"] == bob["thread"]
            host.store.account(channel, "new-bot")
            assert not host.store.applications(channel)["requests"]
            assert not history(host.store, channel, {}, {})["can_send"]
            host.store.direct_messages.inbound(
                host.store.connection(channel), message("new-bot", "1003")
            )
            with pytest.raises(ValueError):
                history(host.store, channel, {"conversation_id": str(old_contact_id)}, {})
        finally:
            await host.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("platform", ["telegram", "whatsapp"])
def test_access_validation_and_failed_sends_are_durable_and_never_replayed(
    tmp_path, monkeypatch, platform
):
    monkeypatch.setattr("corki_gateway.wechat_inbox.PAIR_WAIT", 0.01)

    async def scenario():
        inputs = []

        async def run(*args):
            inputs.append(args)
            return "Reply"

        host = Host(tmp_path, runner=run, online=False)
        channel = account(host.store, platform, validation=True)
        transport = Transport()
        host.connectors[channel] = transport
        try:
            denied = await host.inbound(channel, message())
            assert denied["access_denied"] and not inputs
            host.store.set_application_status(channel, "1001", "approved")
            assert (await host.inbound(channel, message()))["duplicate"]
            await host.inbound(channel, message("new"))
            await asyncio.gather(*list(host.tasks.values()))
            assert len(inputs) == 1
            thread = history(host.store, channel, {"sender": "1001"}, {})["thread"]
            transport.error = True
            request = {
                "action": "manual-send",
                "id": channel,
                "session": thread,
                "request_id": "failed-manual-request",
                "text": "manual",
            }
            result = await host.change(request)
            assert result["message"]["status"] == "failed"
            before = len(transport.sent)
            await host.change(request)
            assert len(transport.sent) == before
            with pytest.raises(ValueError):
                await host.change({**request, "text": "changed"})
        finally:
            await host.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("platform", ["telegram", "whatsapp"])
def test_uploads_are_scoped_and_history_paginates(tmp_path, platform):
    async def scenario():
        host = Host(tmp_path, online=False)
        channel = account(host.store, platform)
        try:
            uploaded = await host.change(
                {
                    "action": "file-upload",
                    "id": channel,
                    "type": "file",
                    "name": "说明.txt",
                    "file": base64.b64encode(b"text").decode(),
                }
            )
            other = host.store.save_connection(
                {
                    "channel": platform,
                    "name": "Other",
                    "values": {"token": "other"} if platform == "telegram" else {},
                }
            )
            with pytest.raises(ValueError):
                host.store.wechat_files.info(other, uploaded["file"]["id"])
            for i in range(105):
                host.store.direct_messages.inbound(host.store.connection(channel), message(str(i)))
            first = history(host.store, channel, {}, {})
            second = history(host.store, channel, {"before": first["before"]}, {})
            assert len(first["messages"]) == 100 and len(second["messages"]) == 5
            assert not (
                {m["id"] for m in first["messages"]} & {m["id"] for m in second["messages"]}
            )
        finally:
            await host.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("platform", ["telegram", "whatsapp"])
def test_channel_media_tools_send_to_active_user_and_preserve_read_permissions(tmp_path, platform):
    from corki_gateway.wechat_file_tools import SendWeChatFile
    from corki_gateway.wechat_tools import SendWeChatImage

    from corki.config.permissions import parse_execution_permissions
    from corki.protocol.tools import ToolCall
    from corki.tools.base import ToolContext

    async def scenario():
        host = Host(tmp_path, online=False)
        channel = account(host.store, platform)
        transport = Transport()
        host.connectors[channel] = transport
        inbound = message()
        host.store.direct_messages.inbound(host.store.connection(channel), inbound)
        thread = history(host.store, channel, {}, {})["thread"]
        host.deliveries[thread] = (channel, inbound)
        source = tmp_path / "reply.txt"
        source.write_text("reply file", encoding="utf-8")
        try:
            image_tool = SendWeChatImage(host, channel, thread)
            file_tool = SendWeChatFile(host, channel, thread)
            assert image_tool.spec.name == f"send_{platform}_image"
            assert file_tool.spec.name == f"send_{platform}_file"
            context = ToolContext(
                cwd=tmp_path,
                execution_permissions=parse_execution_permissions(
                    {"profile": {"type": "danger-full-access"}}, tmp_path
                ),
            )
            result = await file_tool.execute(
                ToolCall("file", file_tool.spec.name, {"path": str(source)}), context
            )
            assert '"sent": true' in result.content
            assert transport.sent[0]["message"]["sender"] == "1001"
            assert transport.sent[0]["files"][0]["name"] == "reply.txt"
        finally:
            await host.close()

    asyncio.run(scenario())
