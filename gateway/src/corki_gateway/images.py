"""Gateway-owned image assets; bytes never cross the connector JSON boundary."""

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import re
import sqlite3
import uuid
from pathlib import Path

from PIL import ImageOps

from corki.media.images import ImagePreparation, joined_work, validated_image
from corki.protocol.input_images import validate_image_positions
from corki.protocol.tools import ImageAttachment

MAX_BYTES = 16 * 1024 * 1024
ID = re.compile(r"[a-f0-9]{64}\Z")


def asset_info(home, channel, identity, thumbnail=False):
    """Read-only preview metadata, without Store construction or DB migration."""
    if not isinstance(identity, str) or not ID.fullmatch(identity) or type(thumbnail) is not bool:
        raise ValueError("图片标识无效")
    root = Path(home) / "gateway"
    try:
        with contextlib.closing(
            sqlite3.connect((root / "gateway.db").as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT a.mime,c.state_key FROM dingtalk_assets a "
                "JOIN connections c ON c.id=a.channel WHERE a.channel=? AND a.id=? "
                "AND c.type_id IN ('dingtalk','wechat','telegram','whatsapp')",
                (channel, identity),
            ).fetchone()
    except sqlite3.Error:
        raise ValueError("图片不存在或已清理") from None
    if not row or row["mime"] not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        raise ValueError("图片不存在或已清理")
    file = (
        root
        / "accounts"
        / row["state_key"]
        / "images"
        / (identity + (".thumb.jpg" if thumbnail else ".bin"))
    )
    if file.is_symlink() or not file.resolve().is_relative_to((root / "accounts").resolve()):
        raise ValueError("图片路径无效")
    if not file.is_file() or file.stat().st_size > MAX_BYTES:
        raise ValueError("图片不存在或已清理")
    return {
        "path": str(file),
        "mime": "image/jpeg" if thumbnail else row["mime"],
        "size": file.stat().st_size,
    }


def validate_refs(message, *, wechat=False, direct=False):
    refs = message.get("image_refs", [])
    if not isinstance(refs, list) or len(refs) > 8:
        raise ValueError("图片数量无效")
    for ref in refs:
        if direct:
            if (
                not isinstance(ref, dict)
                or set(ref) - {"id", "error"}
                or not (
                    isinstance(ref.get("id"), str)
                    and ID.fullmatch(ref["id"])
                    or isinstance(ref.get("error"), str)
                    and len(ref["error"]) <= 300
                )
            ):
                raise ValueError("图片缓存信息无效")
            continue
        if wechat:
            if (
                not isinstance(ref, dict)
                or set(ref) - {"query", "url", "aes_key", "aes_hex"}
                or not (ref.get("query") or ref.get("url"))
                or any(not isinstance(v, str) or len(v) > 16384 for v in ref.values())
            ):
                raise ValueError("图片下载信息无效")
            continue
        if (
            not isinstance(ref, dict)
            or not isinstance(ref.get("download_code"), str)
            or not 1 <= len(ref["download_code"]) <= 16384
        ):
            raise ValueError("图片下载码无效")
    validate_image_positions(message["text"], message.get("image_positions", []), len(refs))
    return refs


class Images:
    def __init__(self, store):
        self.store = store
        self.preparation = ImagePreparation(cache_entries=8, cache_bytes=16 * 1024 * 1024)
        with store.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS dingtalk_assets(
                channel TEXT NOT NULL,id TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,
                PRIMARY KEY(channel,id))""")

    def directory(self, channel):
        row = self.store.connection(channel)
        if not row or row["type_id"] not in ("dingtalk", "wechat", "telegram", "whatsapp"):
            raise ValueError("图片不属于此接入")
        root = self.store.root / "accounts"
        directory = root / row["state_key"] / "images"
        if not directory.resolve().is_relative_to(root.resolve()):
            raise ValueError("图片目录无效")
        return directory

    def file(self, channel, identity, thumbnail=False):
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise ValueError("图片标识无效")
        root = self.directory(channel)
        file = root / (identity + (".thumb.jpg" if thumbnail else ".bin"))
        if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            raise ValueError("图片路径无效")
        return file

    def adopt(self, channel, result):
        if not isinstance(result, dict) or result.get("error"):
            raise ValueError(
                str(result.get("error", "图片下载失败"))
                if isinstance(result, dict)
                else "图片下载结果无效"
            )
        identity = result.get("id")
        file = self.file(channel, identity)
        if file.stat().st_size > MAX_BYTES:
            raise ValueError("图片超过 16 MB")
        with file.open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != identity:
            raise ValueError("图片内容校验失败")
        with validated_image(raw) as image:
            mime = {
                "PNG": "image/png",
                "JPEG": "image/jpeg",
                "GIF": "image/gif",
                "WEBP": "image/webp",
            }[image.format]
            thumb = self.file(channel, identity, True)
            if not thumb.exists():
                with ImageOps.exif_transpose(image) as pixels:
                    pixels.thumbnail((360, 240))
                    with pixels.convert("RGB") as preview:
                        output = io.BytesIO()
                        preview.save(output, format="JPEG", quality=82)
                temporary = thumb.with_name(uuid.uuid4().hex + ".tmp")
                try:
                    with temporary.open("xb") as target:
                        target.write(output.getvalue())
                    temporary.chmod(0o600)
                    temporary.replace(thumb)
                finally:
                    temporary.unlink(missing_ok=True)
        with self.store.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO dingtalk_assets VALUES(?,?,?,?)",
                (channel, identity, mime, len(raw)),
            )
        data = "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii")
        prepared = self.preparation.prepare_image(ImageAttachment(data))
        if not isinstance(prepared, ImageAttachment):
            raise ValueError("图片无法处理，请转换为 PNG 或 JPEG 后重发")
        return {"id": identity, "mime": mime, "size": len(raw), "status": "ready"}, prepared

    def update(self, channel, message, images):
        if message.get("_wechat_parts"):
            cursor = 0
            for part in message["_wechat_parts"]:
                count = len(part.get("image_refs", []))
                if count:
                    self.update(channel, part, images[cursor : cursor + count])
                cursor += count
            return
        positions = message.get("image_positions", [])
        images = [
            {**image, **({"position": positions[i]} if i < len(positions) else {})}
            for i, image in enumerate(images)
        ]
        with self.store.db() as db:
            row = self.store.connection(channel)
            table = (
                "direct_messages"
                if row and row["type_id"] in ("telegram", "whatsapp")
                else "wechat_messages"
                if row and row["type_id"] == "wechat"
                else "dingtalk_messages"
            )
            db.execute(
                f"UPDATE {table} SET images=? WHERE channel=? AND event_key=?",
                (json.dumps(images, ensure_ascii=False), channel, "inbound:" + message["id"]),
            )

    def fail_pending_wechat(self, channel, message):
        if not message.get("image_refs"):
            return
        with self.store.db() as db:
            connection = self.store.connection(channel)
            table = (
                "direct_messages"
                if connection["type_id"] in ("telegram", "whatsapp")
                else "wechat_messages"
            )
            row = db.execute(
                f"SELECT images FROM {table} WHERE channel=? AND event_key=?",
                (channel, "inbound:" + message["id"]),
            ).fetchone()
        if row:
            images = json.loads(row[0])
            if any(i.get("status") == "pending" for i in images):
                self.update(
                    channel,
                    message,
                    [
                        {**i, "status": "failed", "error": "图片处理已停止"}
                        if i.get("status") == "pending"
                        else i
                        for i in images
                    ],
                )

    async def load(self, channel, message, connector):
        try:
            return await self._load(channel, message, connector)
        except asyncio.CancelledError:
            self.update(
                channel,
                message,
                [
                    {"status": "failed", "error": "图片处理已停止"}
                    for _ in message.get("image_refs", [])
                ],
            )
            raise

    async def _load(self, channel, message, connector):
        refs = message.get("image_refs", [])
        if not refs:
            return ()
        try:
            row = self.store.connection(channel)
            result = await connector.call(
                "download_images",
                {"images": refs},
                timeout=300 if row and row["type_id"] == "wechat" else 120,
            )
            values = result.get("images") if isinstance(result, dict) else None
            if not isinstance(values, list) or len(values) != len(refs):
                raise ValueError("图片下载结果不完整")
        except Exception:
            self.update(
                channel,
                message,
                [{"status": "failed", "error": "图片下载失败或超时，请重发图片"} for _ in refs],
            )
            raise ValueError("Agent 图片下载失败或超时，请重发图片") from None
        images, attachments = [], []
        for value in values:
            try:
                info, attachment = await joined_work(self.adopt, channel, value)
                images.append(info)
                attachments.append(attachment)
            except Exception as error:
                identity = value.get("id") if isinstance(value, dict) else None
                if isinstance(identity, str) and ID.fullmatch(identity):
                    with self.store.db() as db:
                        saved = db.execute(
                            "SELECT 1 FROM dingtalk_assets WHERE channel=? AND id=?",
                            (channel, identity),
                        ).fetchone()
                    if not saved:
                        with contextlib.suppress(OSError, ValueError):
                            self.file(channel, identity).unlink(missing_ok=True)
                reason = (
                    str(error)
                    if isinstance(error, ValueError)
                    and str(error).startswith(("图片", "图文", "下载的图片"))
                    else "图片内容无效或尺寸过大，请转换为 PNG 或 JPEG 后重发"
                )
                images.append({"status": "failed", "error": reason})
        self.update(channel, message, images)
        failed = next((image["error"] for image in images if image["status"] == "failed"), None)
        if failed:
            raise ValueError("Agent 图片读取失败：" + failed)
        return tuple(attachments)

    def info(self, channel, identity, thumbnail=False):
        return asset_info(self.store.home, channel, identity, thumbnail)

    def save_bytes(self, channel, raw):
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
            raise ValueError("图片为空或超过 16 MB")
        # Validate before writing untrusted uploads or files produced by tools.
        with validated_image(raw):
            pass
        identity = hashlib.sha256(raw).hexdigest()
        directory = self.directory(channel)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = directory / (uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
            temporary.chmod(0o600)
            temporary.replace(self.file(channel, identity))
        finally:
            temporary.unlink(missing_ok=True)
        info, _ = self.adopt(channel, {"id": identity})
        return info

    async def upload(self, channel, encoded):
        if not isinstance(encoded, str) or not 0 < len(encoded) <= 12 * 1024 * 1024:
            raise ValueError("图片上传数据无效")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise ValueError("图片上传数据无效") from None
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("上传图片不能超过 8 MB")
        try:
            info = await joined_work(self.save_bytes, channel, raw)
        except (ValueError, OSError):
            raise ValueError("图片无法读取，请选择 PNG、JPEG、GIF 或 WebP 图片") from None
        return self.public(channel, json.dumps([info]))[0]

    def send_assets(self, channel, identities):
        if not isinstance(identities, list) or len(identities) > 8:
            raise ValueError("一次最多发送 8 张图片")
        images = []
        for identity in identities:
            info = self.info(channel, identity)
            images.append(
                {"id": identity, "mime": info["mime"], "size": info["size"], "status": "ready"}
            )
        return images

    def public(self, channel, images):
        from urllib.parse import urlencode

        output = []
        for image in json.loads(images):
            if image.get("status") == "ready":
                query = urlencode({"channel": channel, "asset": image["id"]})
                image = {
                    **image,
                    "url": "/api/channel-image?" + query,
                    "thumbnail": "/api/channel-image?" + query + "&thumbnail=1",
                }
            output.append(image)
        return output
