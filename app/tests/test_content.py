"""Desktop attachment and bounded canonical-history contracts."""

import asyncio
import base64
import hashlib
import io
import json
import os
import sys
import time
import uuid
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from content import image_preview, message_images, prune_unused_assets, read_asset
from protocol import MAX_FRAME, dumps
from service import Service

from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.protocol.tools import ImageAttachment


def save_asset(home, raw, mime):
    identity = hashlib.sha256(mime.encode() + b"\0" + raw).hexdigest()
    root = home / "app/attachments"
    root.mkdir(parents=True, exist_ok=True)
    (root / (identity + ".bin")).write_bytes(raw)
    (root / (identity + ".json")).write_text(
        json.dumps({"version": 1, "mime": mime, "size": len(raw)})
    )
    return identity


def test_incompressible_image_preview_fits_protocol_frame():
    image = Image.frombytes("RGBA", (640, 640), os.urandom(640 * 640 * 4))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    data = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
    item = UserMessageItem("", TurnId(str(uuid.uuid4())), attachments=(ImageAttachment(data),))
    preview = image_preview(item, 0)
    assert len(dumps({"data": preview}).encode()) < 1_300_000
    assert item.attachments[0].data_url == data
    normalized = UserMessageItem("", item.turn_id, content_items=item.attachments)
    assert image_preview(normalized, 0) == preview
    assert len(message_images(normalized)) == 1


def test_content_addressed_assets_integrity_scope_and_collection(tmp_path):
    async def check():
        async def send(message):
            pass

        service = Service(tmp_path, send)
        try:
            kept = save_asset(tmp_path, b"kept", "text/plain")
            discarded = save_asset(tmp_path, b"discarded", "text/plain")
            new = save_asset(tmp_path, b"new", "text/plain")
            for identity in (kept, discarded):
                file = tmp_path / "app/attachments" / (identity + ".json")
                os.utime(file, (0, 0))
            service.state.put("draft:new", {"request": {"attachments": [{"assetId": kept}]}})
            assert (
                prune_unused_assets(tmp_path, service.state.path, older_than=time.time() - 86400)
                == 1
            )
            assert read_asset(tmp_path, kept)[0] == b"kept"
            assert read_asset(tmp_path, new)[0] == b"new"
            assert not (tmp_path / "app/attachments" / (discarded + ".bin")).exists()
            with pytest.raises(ValueError):
                read_asset(tmp_path, "../../settings")
            (tmp_path / "app/attachments" / (kept + ".bin")).write_bytes(b"tampered")
            with pytest.raises(ValueError, match="内容已改变"):
                read_asset(tmp_path, kept)
        finally:
            await service.close()

    asyncio.run(check())


def test_long_history_byte_pages_original_text_and_lazy_images(tmp_path):
    async def check():
        async def send(message):
            pass

        service = Service(tmp_path, send)
        thread, turn = ThreadId(str(uuid.uuid4())), TurnId(str(uuid.uuid4()))
        try:
            await service.repository.create_thread(thread, tmp_path)
            original = "中文🦀" * 14000
            items = [AssistantMessageItem(original, turn, new_step_id()) for _ in range(66)]
            image = Image.new("RGB", (40, 30), "orange")
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            data = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
            user = UserMessageItem("", turn, attachments=(ImageAttachment(data),))
            await service.repository.append_items(thread, (*items, user))
            page = await service.read_thread({"threadId": str(thread)})
            assert len(dumps(page).encode()) < MAX_FRAME
            assert "base64" not in dumps(page)
            assert page["messages"][-1]["imageCount"] == 1
            seen = set()
            while True:
                assert not seen.intersection(row["id"] for row in page["messages"])
                seen.update(row["id"] for row in page["messages"])
                if page["next"] is None:
                    break
                page = await service.read_thread({"threadId": str(thread), "before": page["next"]})
                assert len(dumps(page).encode()) < MAX_FRAME
            assert len(seen) == 67
            text, offset = "", 0
            while offset is not None:
                result = await service.read_message_text(
                    {"threadId": str(thread), "messageId": str(items[0].id), "offset": offset}
                )
                text += result["content"]
                offset = result["next"]
            assert text == original
            with pytest.raises(ValueError):
                await service.read_message_text(
                    {"threadId": "other", "messageId": str(items[0].id)}
                )
            preview = await service.read_message_image(
                {"threadId": str(thread), "messageId": str(user.id), "index": 0}
            )
            assert preview["data"].startswith("data:image/png;base64,")
            remote = UserMessageItem(
                "remote", turn, attachments=(ImageAttachment("https://example.invalid/image.png"),)
            )
            await service.repository.append_items(thread, (remote,))
            with pytest.raises(ValueError, match="本机"):
                await service.read_message_image(
                    {"threadId": str(thread), "messageId": str(remote.id)}
                )
        finally:
            await service.close()

    asyncio.run(check())


def test_replay_byte_budget_and_uncached_event_gap(tmp_path):
    async def check():
        delivered = []

        async def send(message):
            assert len(dumps(message).encode()) < MAX_FRAME
            delivered.append(message)

        service = Service(tmp_path, send)
        try:
            # Many individually legal messages exceed one aggregate response.
            for _ in range(12):
                await service.emit("AssistantTextDelta", "thread", {"delta": "文" * 40000})
            assert service.event_bytes <= 1024 * 1024
            assert (await service.replay({"instance": service.instance, "sequence": 0}))["reset"]
            first = service.events[0][0]["sequence"]
            replay = await service.replay({"instance": service.instance, "sequence": first - 1})
            assert not replay["reset"]
            assert len(dumps({"jsonrpc": "2.0", "id": 1, "result": replay}).encode()) < MAX_FRAME
            assert [e["sequence"] for e in replay["events"]] == list(range(first, 13))
            # Legal live event, too large to retain. Empty cache still means gap.
            await service.emit("AssistantTextDelta", "thread", {"delta": "文" * 400000})
            assert delivered[-1]["params"]["sequence"] == 13
            assert not service.events and service.event_bytes == 0
            assert (await service.replay({"instance": service.instance, "sequence": 12}))["reset"]
            assert not (await service.replay({"instance": service.instance, "sequence": 13}))[
                "reset"
            ]
            await service.emit("TurnCompleted", "thread", {})
            assert (await service.replay({"instance": service.instance, "sequence": 12}))["reset"]
            assert (
                len(
                    (await service.replay({"instance": service.instance, "sequence": 13}))["events"]
                )
                == 1
            )
        finally:
            await service.close()

    asyncio.run(check())


def test_snapshot_reloads_if_completion_clears_live_during_history_read(tmp_path):
    async def check():
        async def send(message):
            pass

        service = Service(tmp_path, send)
        thread, turn = ThreadId(str(uuid.uuid4())), TurnId(str(uuid.uuid4()))
        try:
            await service.repository.create_thread(thread, tmp_path)
            await service.repository.append_items(thread, (UserMessageItem("question", turn),))
            service.live[str(thread)] = [{"type": "AssistantTextDelta", "data": {"delta": "final"}}]
            original = service.repository.load_display_items_page
            reads = 0

            async def race(*args, **kwargs):
                nonlocal reads
                page = await original(*args, **kwargs)
                reads += 1
                if reads == 1:
                    await service.repository.append_items(
                        thread, (AssistantMessageItem("final", turn, new_step_id()),)
                    )
                    service.live.pop(str(thread), None)
                    service.lifecycle_revision += 1
                    await service.emit("run.settled", str(thread))
                return page

            service.repository.load_display_items_page = race
            snapshot = await service.read_thread({"threadId": str(thread)})
            assert reads == 2
            assert [message["content"] for message in snapshot["messages"]] == ["question", "final"]
            assert snapshot["live"] == []
            assert snapshot["sequence"] == service.sequence
        finally:
            await service.close()

    asyncio.run(check())


def test_scheduler_preamble_hidden_only_in_desktop_projection(tmp_path):
    from content import SCHEDULED_PREAMBLE, display_user_text

    from corki.sessions.models import TurnRecord, TurnStatus

    async def check():
        async def send(message):
            pass

        service = Service(tmp_path, send)
        thread, turn = ThreadId(str(uuid.uuid4())), TurnId(str(uuid.uuid4()))
        task = "实际任务标题\n" + "文档内容🦀" * 9000
        original = SCHEDULED_PREAMBLE + task
        item = UserMessageItem(original, turn)
        try:
            await service.repository.create_thread(thread, tmp_path)
            await service.repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.COMPLETED, original)
            )
            await service.repository.append_items(thread, (item,))
            assert (await service.list_threads({}))["threads"][0]["title"] == "实际任务标题"
            page = await service.read_thread({"threadId": str(thread)})
            assert page["messages"][0]["content"] == task[:32000]
            text, offset = "", 0
            while offset is not None:
                part = await service.read_message_text(
                    {"threadId": str(thread), "messageId": str(item.id), "offset": offset}
                )
                text += part["content"]
                offset = part["next"]
            assert text == task
            assert (await service.repository.load_items(thread))[0].content == original
            assert display_user_text("Quoted: " + original) == "Quoted: " + original
            ordinary = "This is an unattended explanation"
            assert display_user_text(ordinary) == ordinary
        finally:
            await service.close()

    asyncio.run(check())


def test_tool_heavy_turn_keeps_question_visible_without_loading_all_history(tmp_path):
    async def check():
        async def send(event):
            pass

        service = Service(tmp_path, send)
        thread, turn = ThreadId(str(uuid.uuid4())), TurnId(str(uuid.uuid4()))
        try:
            await service.repository.create_thread(thread, tmp_path)
            user = UserMessageItem('Compare five years of public GDP data', turn)
            replies = [AssistantMessageItem(f'progress {i}', turn, new_step_id()) for i in range(170)]
            await service.repository.append_items(thread, (user, *replies))
            page = await service.read_thread({'threadId': str(thread)})
            assert len(page['messages']) == 61
            assert page['messages'][0]['id'] == str(user.id)
            assert page['messages'][0]['contextOnly']
            seen = set()
            while True:
                assert page['messages'][0]['id'] == str(user.id)
                persisted = [m for m in page['messages'] if not m.get('contextOnly')]
                assert not seen.intersection(m['id'] for m in persisted)
                seen.update(m['id'] for m in persisted)
                if page['next'] is None:
                    assert not page['messages'][0].get('contextOnly')
                    break
                page = await service.read_thread({'threadId': str(thread), 'before': page['next']})
            assert len(seen) == 171
        finally:
            await service.close()

    asyncio.run(check())
