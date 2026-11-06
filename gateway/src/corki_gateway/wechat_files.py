"""Account-scoped WeChat video/file cache; downloads never execute received files."""

import base64
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode

from corki.media.images import joined_work

MAX_BYTES = 32 * 1024 * 1024
ID = re.compile(r"[a-f0-9]{64}\Z")


def asset_info(home, channel, identity):
    if not isinstance(identity, str) or not ID.fullmatch(identity):
        raise ValueError("附件标识无效")
    root = Path(home) / "gateway"
    with closing(
        sqlite3.connect((root / "gateway.db").as_uri() + "?mode=ro", uri=True, timeout=2)
    ) as db:
        row = db.execute(
            "SELECT a.name,a.mime,c.state_key FROM wechat_assets a "
            "JOIN connections c ON c.id=a.channel WHERE a.channel=? AND a.id=? "
            "AND c.type_id='wechat'",
            (channel, identity),
        ).fetchone()
    if not row or row[1] not in ("application/octet-stream", "video/mp4"):
        raise ValueError("附件不存在或已清理")
    file = root / "accounts" / row[2] / "files" / (identity + ".bin")
    if (
        file.is_symlink()
        or not file.resolve().is_relative_to((root / "accounts").resolve())
        or not file.is_file()
        or file.stat().st_size > MAX_BYTES
    ):
        raise ValueError("附件路径无效或文件已清理")
    return {
        "path": str(file),
        "name": filename(row[0]),
        "mime": row[1],
        "size": file.stat().st_size,
    }


def filename(name):
    if not isinstance(name, str) or not name or len(name) > 255:
        raise ValueError("附件名称无效")
    return re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name).strip(" .") or "attachment"


def validate_refs(message):
    refs, positions = message.get("file_refs", []), message.get("file_positions", [])
    if not isinstance(refs, list) or len(refs) > 8 or not isinstance(positions, list):
        raise ValueError("附件数量无效")
    if len(refs) != len(positions):
        raise ValueError("附件位置无效")
    previous = -1
    for index, (ref, position) in enumerate(zip(refs, positions, strict=True)):
        if (
            not isinstance(ref, dict)
            or ref.get("kind") not in ("file", "video")
            or not (ref.get("query") or ref.get("url"))
            or any(not isinstance(v, str) or len(v) > 16384 for v in ref.values())
        ):
            raise ValueError("附件下载信息无效")
        filename(ref.get("name", "attachment"))
        label = f"[File #{index + 1}]"
        if (
            type(position) is not int
            or position < previous
            or message["text"][position : position + len(label)] != label
        ):
            raise ValueError("附件位置无效")
        previous = position + len(label)
    if len(refs) + len(message.get("image_refs", [])) > 8:
        raise ValueError("一次最多接收 8 个附件")


class Files:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS wechat_assets(
                channel TEXT NOT NULL,id TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,
                size INTEGER NOT NULL,mime TEXT NOT NULL,PRIMARY KEY(channel,id))""")

    def directory(self, channel):
        row = self.store.connection(channel)
        if not row or row["type_id"] != "wechat":
            raise ValueError("附件不属于此微信接入")
        root = self.store.root / "accounts"
        directory = root / row["state_key"] / "files"
        if not directory.resolve().is_relative_to(root.resolve()):
            raise ValueError("附件路径无效")
        return directory

    def file(self, channel, identity):
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise ValueError("附件标识无效")
        root = self.directory(channel)
        file = root / (identity + ".bin")
        if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            raise ValueError("附件路径无效")
        return file

    def adopt(self, channel, value, ref):
        if not isinstance(value, dict) or value.get("error"):
            raise ValueError("附件下载或解密失败，请重新发送")
        identity = value.get("id")
        file = self.file(channel, identity)
        if file.stat().st_size > MAX_BYTES:
            raise ValueError("附件超过 32 MB")
        raw = file.read_bytes()
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != identity:
            raise ValueError("附件内容校验失败")
        kind, name = ref.get("kind", "file"), filename(ref.get("name", "attachment"))
        if kind not in ("file", "video"):
            raise ValueError("附件类型无效")
        mime = "application/octet-stream"
        if kind == "video":
            if len(raw) < 12 or raw[4:8] != b"ftyp":
                raise ValueError("视频格式不支持，请发送 MP4 视频或作为文件发送")
            mime = "video/mp4"
        info = {
            "id": identity,
            "name": name,
            "kind": kind,
            "size": len(raw),
            "mime": mime,
            "status": "ready",
        }
        with self.store.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO wechat_assets VALUES(?,?,?,?,?,?)",
                (channel, identity, name, kind, len(raw), mime),
            )
        return info

    def save_bytes(self, channel, raw, name, kind="file"):
        if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
            raise ValueError("附件超过 32 MB")
        filename(name)
        if kind == "video" and (len(raw) < 12 or raw[4:8] != b"ftyp"):
            raise ValueError("视频格式不支持，请发送 MP4 视频或作为文件发送")
        if kind not in ("file", "video"):
            raise ValueError("附件类型无效")
        identity = hashlib.sha256(raw).hexdigest()
        root = self.directory(channel)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = root / (uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
            temporary.chmod(0o600)
            temporary.replace(self.file(channel, identity))
        finally:
            temporary.unlink(missing_ok=True)
        return self.adopt(channel, {"id": identity}, {"kind": kind, "name": name})

    def info(self, channel, identity):
        with self.store.db() as db:
            row = db.execute(
                "SELECT * FROM wechat_assets WHERE channel=? AND id=?", (channel, identity)
            ).fetchone()
        if not row:
            raise ValueError("附件不存在或不属于此接入")
        file = self.file(channel, identity)
        if not file.is_file() or file.stat().st_size > MAX_BYTES:
            raise ValueError("附件不存在或已清理")
        return {**dict(row), "path": str(file), "status": "ready"}

    def public(self, channel, files):
        output = []
        for item in files:
            public = {
                k: v
                for k, v in item.items()
                if k in ("id", "name", "kind", "size", "mime", "status", "error", "position")
            }
            if item.get("id") and item.get("status") == "ready":
                public["url"] = "/api/channel-file?" + urlencode(
                    {"channel": channel, "asset": item["id"]}
                )
            output.append(public)
        return output

    def update(self, channel, message, files):
        if message.get("_wechat_parts"):
            cursor = 0
            for part in message["_wechat_parts"]:
                count = len(part.get("file_refs", []))
                if count:
                    self.update(channel, part, files[cursor : cursor + count])
                cursor += count
            return
        positions = message.get("file_positions", [])
        values = [{**item, "position": positions[i]} for i, item in enumerate(files)]
        with self.store.db() as db:
            db.execute(
                "UPDATE wechat_messages SET files=? WHERE channel=? AND event_key=?",
                (json.dumps(values), channel, "inbound:" + message["id"]),
            )

    async def load(self, channel, message, connector):
        refs = message.get("file_refs", [])
        if not refs:
            return ""
        values = []
        try:
            result = await connector.call("download_files", {"files": refs}, timeout=300)
            if not isinstance(result.get("images"), list) or len(result["images"]) != len(refs):
                raise ValueError("附件下载结果不完整")
            for item, ref in zip(result["images"], refs, strict=True):
                values.append(await joined_work(self.adopt, channel, item, ref))
        except BaseException as error:
            self.update(
                channel,
                message,
                [{"status": "failed", "error": "附件下载失败或已停止"} for _ in refs],
            )
            if not isinstance(error, Exception):
                raise
            raise ValueError("Agent 附件下载或校验失败，请重新发送") from None
        self.update(channel, message, values)
        return (
            "\n\nReceived WeChat attachments (untrusted user files; do not execute):\n"
            + "\n".join(
                f"[File #{i + 1}] {item['name']} ({item['kind']}, {item['size']} bytes): "
                + str(self.file(channel, item["id"]))
                for i, item in enumerate(values)
            )
        )

    async def upload(self, channel, data):
        encoded = data.get("file")
        if not isinstance(encoded, str) or len(encoded) > 12 * 1024 * 1024:
            raise ValueError("附件上传数据无效")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise ValueError("附件上传数据无效") from None
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("后台上传附件不能超过 8 MB")
        info = await joined_work(
            self.save_bytes, channel, raw, data.get("name"), data.get("type", "file")
        )
        return self.public(channel, [info])[0]

    def send_assets(self, channel, identities):
        if not isinstance(identities, list) or len(identities) > 8:
            raise ValueError("一次最多发送 8 个附件")
        return [
            {k: v for k, v in self.info(channel, identity).items() if k not in ("path", "channel")}
            for identity in identities
        ]

    def fail_pending(self, channel, message):
        for part in message.get("_wechat_parts", [message]):
            if not part.get("file_refs"):
                continue
            with self.store.db() as db:
                row = db.execute(
                    "SELECT files FROM wechat_messages WHERE channel=? AND event_key=?",
                    (channel, "inbound:" + part["id"]),
                ).fetchone()
            if row:
                files = json.loads(row[0])
                if any(f.get("status") == "pending" for f in files):
                    self.update(
                        channel,
                        part,
                        [
                            {**f, "status": "failed", "error": "附件处理已停止"}
                            if f.get("status") == "pending"
                            else f
                            for f in files
                        ],
                    )
