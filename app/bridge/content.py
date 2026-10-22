"""Bounded desktop content views; original messages remain in the core database."""

import base64
import hashlib
import io
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    item_from_payload,
)
from corki.protocol.tools import ImageAttachment

MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif", "text/plain"}
PREVIEW_CHARS = 32000

# Display-only projection of the scheduler's fixed preamble. Canonical input
# remains untouched, and partial matches or quoted occurrences are preserved.
SCHEDULED_PREAMBLE = (
    "This is an unattended scheduled task in a new session. Execute the task below "
    "using the configured permissions. Do not wait for user input or request permission "
    "escalation; report any blocker. Do not create additional schedules unless the task "
    "explicitly requests them. Return a self-contained result.\n\n"
)


def display_user_text(content):
    return content.removeprefix(SCHEDULED_PREAMBLE)


def read_asset(home, identity):
    if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{64}", identity):
        raise ValueError("附件标识无效")
    root = Path(home) / "app/attachments"
    meta = json.loads((root / (identity + ".json")).read_text())
    if meta.get("version") != 1 or meta.get("mime") not in MIMES:
        raise ValueError("附件记录无效")
    file = root / (identity + ".bin")
    if file.stat().st_size > 1_000_000:
        raise ValueError("附件过大")
    with file.open("rb") as source:
        raw = source.read(1_000_001)
    digest = hashlib.sha256(meta["mime"].encode() + b"\0" + raw).hexdigest()
    if len(raw) != meta.get("size") or digest != identity:
        raise ValueError("附件内容已改变，请重新添加")
    return raw, meta["mime"]


def message_text(item):
    if isinstance(item, ToolCallItem):
        return json.dumps(item.call.arguments, ensure_ascii=False)
    if isinstance(item, ToolResultItem):
        return item.display_content or item.content
    if isinstance(item, UserMessageItem):
        return display_user_text(item.content)
    if isinstance(item, AssistantMessageItem):
        return item.content
    raise ValueError("该消息没有可显示的内容")


def read_message(database, thread, identity):
    with closing(sqlite3.connect(database)) as db:
        row = db.execute(
            "SELECT kind,payload_json FROM conversation_items WHERE thread_id=? AND id=?",
            (thread, identity),
        ).fetchone()
    if not row:
        raise ValueError("消息不存在或不属于当前会话")
    return item_from_payload(row[0], json.loads(row[1]))


def message_images(item):
    if not isinstance(item, UserMessageItem):
        return ()
    # Core normalizes current messages into content_items; legacy histories
    # retain the separate attachments field. Never count both representations.
    if item.content_items:
        return tuple(part for part in item.content_items if isinstance(part, ImageAttachment))
    return item.attachments


def image_preview(item, index):
    from PIL import Image

    images = message_images(item)
    if not 0 <= index < len(images):
        raise ValueError("图片不存在")
    data = images[index].data_url
    header, _, encoded = data.partition(",")
    if header not in {f"data:{mime};base64" for mime in MIMES if mime.startswith("image/")}:
        raise ValueError("仅预览保存在本机的图片")
    if len(encoded) > 32_000_000:
        raise ValueError("原图过大，无法生成预览")
    raw = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 32_000_000:
            raise ValueError("图片尺寸超过预览范围")
        # Even incompressible RGBA pixels must fit the 2 MB JSON-RPC frame
        # after base64 encoding; previews never replace the canonical original.
        image.thumbnail((480, 480))
        output = io.BytesIO()
        image.convert("RGBA").save(output, format="PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def prune_unused_assets(home, state_path, *, older_than):
    """Remove only aged, unreferenced desktop upload blobs; never core history."""
    root = Path(home) / "app/attachments"
    if not root.is_dir():
        return 0
    candidates = [
        p
        for p in root.glob("*.json")
        if re.fullmatch("[a-f0-9]{64}", p.stem) and p.stat().st_mtime < older_than
    ]
    if not candidates:
        return 0
    references = set()
    with closing(sqlite3.connect(state_path)) as db:
        rows = list(db.execute("SELECT value FROM values_store WHERE key LIKE 'draft:%'"))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='followups'").fetchone():
            rows.extend(db.execute("SELECT payload FROM followups"))
        for row in rows:
            stack = [json.loads(row[0])]
            while stack:
                value = stack.pop()
                if isinstance(value, dict):
                    if isinstance(value.get("assetId"), str):
                        references.add(value["assetId"])
                    stack.extend(value.values())
                elif isinstance(value, list):
                    stack.extend(value)
    removed = 0
    for meta in candidates:
        if meta.stem not in references:
            (root / (meta.stem + ".bin")).unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            removed += 1
    return removed


def execution_timings(database, thread, calls):
    """Read the core execution ledger, never infer duration from model text."""
    if not calls:
        return {}
    placeholders = ",".join("?" for _ in calls)
    with closing(sqlite3.connect(database)) as db:
        rows = db.execute(
            "SELECT call_id,turn_id,unixepoch(started_at),unixepoch(completed_at) "
            f"FROM tool_executions WHERE thread_id=? AND call_id IN ({placeholders})",
            (thread, *calls),
        ).fetchall()
    return {
        (turn, call): {
            "startedAt": start * 1000,
            **({"elapsedSeconds": max(0, end - start)} if end is not None else {}),
        }
        for call, turn, start, end in rows
        if start is not None
    }
