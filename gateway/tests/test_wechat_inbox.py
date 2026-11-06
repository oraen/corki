"""Nearby media/questions pair once; subsequent questions retain FIFO ordering."""

import asyncio
import hashlib
import io

from corki_gateway.history import history
from corki_gateway.service import Host
from PIL import Image


def test_question_image_pairs_keep_their_own_attachments_and_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr("corki_gateway.wechat_inbox.PAIR_WAIT", 0.04)
    monkeypatch.setattr("corki_gateway.wechat_inbox.MAX_WAIT", 0.1)

    async def scenario():
        runs, sent, indicators = [], [], []
        blobs = {}
        for color in ("red", "blue"):
            out = io.BytesIO()
            Image.new("RGB", (12, 12), color).save(out, "PNG")
            blobs[color] = out.getvalue()

        async def run(_thread, text, *_args, **kwargs):
            runs.append((text, kwargs))
            await asyncio.sleep(0.02)
            return "answer " + ("A" if "A" in text else "B")

        host = Host(tmp_path, runner=run, online=False)
        channel = host.store.save_connection({"channel": "wechat", "name": "Fixture", "values": {}})
        host.store.enable(channel, True)

        class Transport:
            async def call(self, method, params, **_):
                if method == "typing":
                    indicators.append(params["active"])
                    return {"supported": True}
                if method == "send":
                    sent.append(params["text"])
                    return {"sent": True}
                images = []
                directory = host.store.images.directory(channel)
                directory.mkdir(parents=True, exist_ok=True)
                for ref in params["images"]:
                    raw = blobs[ref["query"]]
                    identity = hashlib.sha256(raw).hexdigest()
                    (directory / (identity + ".bin")).write_bytes(raw)
                    images.append({"id": identity})
                return {"images": images}

        host.connectors[channel] = Transport()

        def message(identity, text, image=None):
            return {
                "id": identity,
                "text": text,
                "sender": "alice",
                "conversation": "alice",
                "kind": "direct",
                "context_token": "context-" + identity,
                **({"image_refs": [{"query": image}], "image_positions": [0]} if image else {}),
            }

        try:
            first = message("a", "Question A😀")
            await host.inbound(channel, first)
            await asyncio.sleep(0.01)
            await host.inbound(channel, message("image-a", "[Image #1]", "red"))
            await host.inbound(channel, message("image-b", "[Image #1]", "blue"))
            await host.inbound(channel, message("b", "Question B"))
            assert (await host.inbound(channel, first))["duplicate"]
            await asyncio.gather(*list(host.tasks.values()))
            assert [r[0] for r in runs] == ["Question A😀\n[Image #1]", "[Image #1]\nQuestion B"]
            assert runs[0][1]["image_positions"] == [len("Question A😀\n")]
            assert runs[1][1]["image_positions"] == [0]
            assert runs[0][1]["attachments"] != runs[1][1]["attachments"]
            assert sent == ["answer A", "answer B"]
            view = history(host.store, channel, {}, {})
            images = [m["images"][0] for m in view["messages"] if m["images"]]
            assert len(images) == 2 and all(i["status"] == "ready" for i in images)
            with host.store.db() as db:
                states = [r[0] for r in db.execute("SELECT status FROM receipts")]
            assert states == ["completed"] * 4
            assert indicators.count(True) == indicators.count(False) == 2
            assert not host.wechat_batches
        finally:
            await host.close()

    asyncio.run(scenario())


def test_batch_renumbers_multiple_images_and_seals_after_pair():
    from corki_gateway.images import validate_refs
    from corki_gateway.wechat_inbox import WeChatBatch

    async def scenario():
        image = {
            "id": "1",
            "text": "[Image #1]",
            "image_refs": [{"query": "one"}],
            "image_positions": [0],
        }
        batch = WeChatBatch(image)
        assert batch.append({**image, "id": "2"})
        assert batch.append({"id": "3", "text": "Which one?"})
        assert not batch.append({**image, "id": "4"})
        combined = await batch.collect()
        assert combined["text"] == "[Image #1]\n[Image #2]\nWhich one?"
        assert len(validate_refs(combined, wechat=True)) == 2

    asyncio.run(scenario())


def test_stop_during_pairing_cancels_all_receipts_and_attachment_placeholders(tmp_path):
    async def scenario():
        host = Host(tmp_path, online=False)
        channel = host.store.save_connection({"channel": "wechat", "name": "Stop", "values": {}})
        host.store.enable(channel, True)
        first = {
            "id": "picture",
            "sender": "alice",
            "conversation": "alice",
            "kind": "direct",
            "text": "[Image #1]",
            "image_refs": [{"query": "private"}],
            "image_positions": [0],
        }
        try:
            await host.inbound(channel, first)
            await host.inbound(
                channel,
                {
                    **first,
                    "id": "question",
                    "text": "What is this?",
                    "image_refs": [],
                    "image_positions": [],
                },
            )
            await host.stop_channel(channel)
            with host.store.db() as db:
                states = [r[0] for r in db.execute("SELECT status FROM receipts")]
            assert states == ["cancelled", "cancelled"]
            view = history(host.store, channel, {}, {})
            assert view["messages"][0]["images"][0]["status"] == "failed"
            assert not host.wechat_batches and not host.tasks
        finally:
            await host.close()

    asyncio.run(scenario())
