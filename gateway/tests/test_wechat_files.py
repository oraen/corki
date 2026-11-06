"""Video/file receive, scoped downloads, manual replies and permission-bound tools."""

import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest
from corki_gateway.history import history
from corki_gateway.service import Host
from corki_gateway.wechat_file_tools import SendWeChatFile
from corki_gateway.wechat_files import asset_info

from corki.config.permissions import parse_execution_permissions
from corki.protocol.tools import ToolCall
from corki.tools.base import ToolContext

VIDEO = Path(__file__).with_name("fixtures") / "wechat-video.mp4"


@pytest.mark.parametrize("kind", ["file", "video"])
def test_receive_then_reply_video_or_file_keeps_history_and_native_read_policy(
    tmp_path, monkeypatch, kind
):
    monkeypatch.setattr("corki_gateway.wechat_inbox.PAIR_WAIT", 0.02)

    async def scenario():
        raw = VIDEO.read_bytes() if kind == "video" else "你好，文件内容".encode()
        name = "演示.mp4" if kind == "video" else "说明.txt"
        inputs, sent, statuses = [], [], []

        async def run(thread, text, *_args, **_kwargs):
            inputs.append(text)
            path = host.store.wechat_files.file(channel, hashlib.sha256(raw).hexdigest())
            assert path.read_bytes() == raw and str(path) in text
            context = ToolContext(
                cwd=tmp_path,
                execution_permissions=parse_execution_permissions(
                    {"profile": {"type": "danger-full-access"}}, tmp_path
                ),
            )
            tool = SendWeChatFile(host, channel, thread, video=kind == "video")
            call = ToolCall(
                "file-reply", tool.spec.name, {"path": str(path), "caption": "附件回复"}
            )
            assert json.loads((await tool.execute(call, context)).content)["sent"]
            return "已发送"

        host = Host(tmp_path, runner=run, online=False)
        channel = host.store.save_connection({"channel": "wechat", "name": "Files", "values": {}})
        host.store.enable(channel, True)

        class Transport:
            async def call(self, method, params, **_):
                if method == "download_files":
                    identity = hashlib.sha256(raw).hexdigest()
                    directory = host.store.wechat_files.directory(channel)
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory / (identity + ".bin")).write_bytes(raw)
                    return {"images": [{"id": identity}]}
                if method == "typing":
                    statuses.append(params["active"])
                    return {"supported": True}
                sent.append(params)
                return {"sent": True}

        host.connectors[channel] = Transport()
        try:
            message = {
                "id": "received",
                "sender": "alice",
                "conversation": "alice",
                "kind": "direct",
                "text": "[File #1]",
                "file_positions": [0],
                "file_refs": [{"query": "private-query", "kind": kind, "name": name}],
            }
            await host.inbound(channel, message)
            await host.inbound(
                channel,
                {
                    **message,
                    "id": "question",
                    "text": "发回给我",
                    "file_refs": [],
                    "file_positions": [],
                },
            )
            await asyncio.gather(*list(host.tasks.values()))
            assert len(inputs) == 1 and "发回给我" in inputs[0]
            assert statuses == [True, False]
            assert sent[0]["files"][0]["kind"] == kind and len(sent) == 2
            view = history(host.store, channel, {}, {})
            files = [m["files"][0] for m in view["messages"] if m["files"]]
            assert len(files) == 2 and all(f["status"] == "ready" for f in files)
            assert files[0]["url"].startswith("/api/channel-file?")
            assert "private-query" not in json.dumps(view)
            assert "path" not in files[0]
            assert Path(asset_info(tmp_path, channel, files[0]["id"])["path"]).read_bytes() == raw
            other = host.store.save_connection({"channel": "wechat", "name": "Other", "values": {}})
            with pytest.raises(ValueError):
                host.store.wechat_files.info(other, files[0]["id"])
            with pytest.raises(ValueError):
                asset_info(tmp_path, other, files[0]["id"])
        finally:
            await host.close()

    asyncio.run(scenario())


def test_manual_files_upload_idempotency_bad_video_and_size_validation(tmp_path):
    async def scenario():
        host = Host(tmp_path, online=False)
        channel = host.store.save_connection({"channel": "wechat", "name": "Files", "values": {}})
        host.store.enable(channel, True)
        message = {
            "id": "one",
            "sender": "alice",
            "conversation": "alice",
            "kind": "direct",
            "text": "hello",
        }
        host.store.wechat_messages.inbound(host.store.connection(channel), message)
        calls = []

        class Transport:
            async def call(self, method, params, **_):
                calls.append(params)
                return {"sent": True}

        host.connectors[channel] = Transport()
        try:
            data = {
                "action": "file-upload",
                "id": channel,
                "name": "../说明.txt",
                "type": "file",
                "file": base64.b64encode(b"fixture-document").decode(),
            }
            file = (await host.change(data))["file"]
            assert "/" not in file["name"]
            thread = history(host.store, channel, {}, {})["thread"]
            request = {
                "action": "manual-send",
                "id": channel,
                "session": thread,
                "request_id": "fixture-request-0001",
                "text": "",
                "files": [file["id"]],
            }
            first = await host.change(request)
            assert first["message"]["status"] == "sent"
            await host.change(request)
            assert len(calls) == 1
            with pytest.raises(ValueError):
                await host.change({**request, "files": [], "text": "changed"})
            for invalid in ({**data, "type": "video"}, {**data, "file": "bad-base64"}):
                with pytest.raises(ValueError):
                    await host.change(invalid)
            with pytest.raises(ValueError):
                host.store.wechat_files.save_bytes(channel, b"x" * (32 * 1024 * 1024 + 1), "large")
        finally:
            await host.close()

    asyncio.run(scenario())


def test_file_tool_denied_session_read_never_sends(tmp_path, monkeypatch):
    selected = object()
    reads = []

    async def deny(permissions, *_args, **_kwargs):
        reads.append(permissions)
        raise PermissionError("session read denied")

    monkeypatch.setattr("corki_gateway.wechat_file_tools.file_operation", deny)

    async def scenario():
        host = Host(tmp_path, online=False)
        channel = host.store.save_connection({"channel": "wechat", "name": "Files", "values": {}})
        host.store.enable(channel, True)
        host.deliveries["thread"] = (channel, {"id": "one", "sender": "alice"})
        try:
            tool = SendWeChatFile(host, channel, "thread")
            with pytest.raises(PermissionError):
                await tool.execute(
                    ToolCall("denied", tool.spec.name, {"path": "private.txt"}),
                    ToolContext(cwd=tmp_path, execution_permissions=selected),
                )
            assert reads == [selected]
            assert not list(tmp_path.rglob("*.bin"))
        finally:
            await host.close()

    asyncio.run(scenario())
