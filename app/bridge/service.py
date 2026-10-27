"""Desktop host adapter for the existing Corki Runtime; no model loop here."""

import asyncio
import base64
import hashlib
import io
import json
import os
import sqlite3
import time
import uuid
from collections import deque
from contextlib import aclosing, closing, suppress
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from weakref import WeakValueDictionary

from content import (
    PREVIEW_CHARS,
    display_user_text,
    execution_timings,
    image_preview,
    message_images,
    message_text,
    prune_unused_assets,
    read_asset,
    read_message,
)
from followups import FollowUps
from live import append_event, interrupted_messages
from model_watch import ModelCallWatch
from protocol import dumps
from state import DesktopState

from corki.cli.elicitation import validate_field
from corki.cli.history_projection import HistoryProjection
from corki.config.settings import CorkiSettings
from corki.core.runtime import LangGraphRuntime
from corki.models.request_watch import request_observer
from corki.platforms.paths import directory_error_message
from corki.protocol.ids import ThreadId
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    item_from_payload,
)
from corki.protocol.session_source import SessionSource
from corki.protocol.tools import ImageAttachment
from corki.sessions.display import DisplayItemsCursor
from corki.storage.sqlite import SQLiteSessionRepository
from corki.storage.thread_archive import SQLiteThreadArchiveStore
from corki.storage.thread_delete import SQLiteThreadDeleteStore

REPLAY_BYTES = 1024 * 1024


def text(params, key, *, default=None, limit=1000):
    value = params.get(key, default)
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(f"Invalid {key}")
    return value


def integer(params, key, default, maximum=1000000000):
    value = params.get(key, default)
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"Invalid {key}")
    return value


class Service:
    def __init__(self, home, send, factory=None):
        self.home = home.expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.database = self.home / "sessions/corki.db"
        # Bootstrap once per backend startup; every conversation borrows this
        # stateless, per-operation-connection repository instead of migrating again.
        self.repository = SQLiteSessionRepository(self.database)
        self.archive = SQLiteThreadArchiveStore(self.database)
        self.state = DesktopState(self.home)
        self.send = send
        self.factory = factory or LangGraphRuntime.acreate
        self.runtimes = {}
        self.loaded_settings = {}
        self.active_models = {}
        self.tasks = {}
        self.admissions = {}
        self.locks = WeakValueDictionary()
        self.runtime_cache_limit = 8
        self.retirement_lock = asyncio.Lock()
        self.pending = {}
        self.live = {}
        self.instance = str(uuid.uuid4())
        self.sequence = 0
        self.events = deque(maxlen=256)
        self.event_bytes = 0
        self.stopping = False
        self.secrets = set()
        self.asset_gc_at = 0.0
        self.lifecycle_revision = 0
        self.followups = FollowUps(self)
        # A previous process may have committed a Turn but lost its reply.
        for row in self.state.unresolved():
            status = None
            if row["turn_id"]:
                with closing(sqlite3.connect(self.database)) as db:
                    found = db.execute(
                        "SELECT status FROM turns WHERE thread_id=? AND id=?",
                        (row["thread_id"], row["turn_id"]),
                    ).fetchone()
                    if found and found[0] in ("completed", "failed", "cancelled"):
                        status = found[0]
            self.state.update_request(row["id"], status or "outcome-unknown")

    def safe_error(self, error):
        from corki.memory.sanitizer import redact_secrets

        value = str(error)
        for secret in sorted(self.secrets, key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[已隐藏]")
        return redact_secrets(value)[:2000]

    def directory(self, value=""):
        default = self.home / "main/default"
        directory = Path(value).expanduser().resolve() if value else default
        if directory == default:
            directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir() or not os.access(directory, os.R_OK | os.X_OK):
            raise ValueError(directory_error_message(directory, "工作目录不存在或不可访问"))
        return directory

    def settings(self, directory, *, refresh=False, **overrides):
        key = str(Path(directory).resolve())
        if refresh or key not in self.loaded_settings:
            self.loaded_settings.pop(key, None)
            self.loaded_settings[key] = CorkiSettings.for_directory(
                directory, config_file=self.home / "settings.json"
            )
        settings = self.loaded_settings[key]
        if overrides:
            settings = settings.for_model(overrides.pop("provider"), overrides.pop("model"))
            settings = replace(settings, **overrides)
        if settings.api_key:
            self.secrets.add(settings.api_key)
        return settings

    async def emit(self, kind, thread=None, data=None):
        self.sequence += 1
        event = {
            "instance": self.instance,
            "sequence": self.sequence,
            "type": kind,
            "threadId": thread,
            "data": data or {},
        }
        size = len(dumps(event).encode())
        if len(self.events) == self.events.maxlen:
            self.event_bytes -= self.events.popleft()[1]
        # Leave room for JSON-RPC/list framing. A single deliverable event may
        # exceed the replay cache; send it live but require a snapshot on gaps.
        while self.events and self.event_bytes + size > REPLAY_BYTES:
            self.event_bytes -= self.events.popleft()[1]
        if size <= REPLAY_BYTES:
            self.events.append((event, size))
            self.event_bytes += size
        await self.send({"jsonrpc": "2.0", "method": "event", "params": event})

    async def dispatch(self, method, p):
        if self.stopping:
            raise ValueError("服务正在退出")
        methods = {
            "initialize": self.initialize,
            "config.read": self.catalog,
            "threads.list": self.list_threads,
            "threads.read": self.read_thread,
            "messages.text": self.read_message_text,
            "messages.image": self.read_message_image,
            "assets.read": self.read_attachment,
            "threads.rename": self.rename,
            "threads.delete": self.delete,
            "threads.archive": self.archive_thread,
            "turn.submit": self.submit,
            "queue.action": self.followups.action,
            "turn.stop": self.stop,
            "request.read": self.request_read,
            "interaction.respond": self.respond,
            "state.read": self.read_state,
            "state.write": self.write_state,
            "events.replay": self.replay,
            "status": self.status,
        }
        handler = methods.get(method)
        if handler is None:
            raise ValueError("Unknown method")
        return await handler(p)

    async def collect_assets(self):
        now = time.monotonic()
        if now - self.asset_gc_at < 600:
            return
        self.asset_gc_at = now
        await asyncio.to_thread(
            prune_unused_assets, self.home, self.state.path, older_than=time.time() - 86400
        )

    async def initialize(self, p):
        if p.get("protocolVersion") != 1:
            raise ValueError("Unsupported protocol version")
        await self.collect_assets()
        return {
            "protocolVersion": 1,
            "threadManagementVersion": 2,
            "followUpVersion": 1,
            "instance": self.instance,
            "sequence": self.sequence,
            "defaultDirectory": str(self.home / "main/default"),
        }

    async def catalog(self, p):
        directory = self.directory(text(p, "directory", default=""))
        self.loaded_settings.pop(str(Path(directory).resolve()), None)
        if not (self.home / "settings.json").is_file():
            return {
                "valid": False,
                "error": "尚未配置模型，请先在管理页面配置供应商和模型。",
                "providers": [],
            }
        try:
            settings = self.settings(directory, refresh=True)
            # The authoritative parser validates all known fields and project layers.
            document = json.loads(settings._provider_document or "{}")
            configured = document.get("providers", {})
            if not configured:
                raise ValueError("配置中没有供应商和模型")
            providers = []
            for name, profile in configured.items():
                if not profile.get("enabled", True):
                    continue
                models = []
                for model in profile.get("models", {}):
                    selected = settings.for_model(name, model)
                    if selected.api_key:
                        self.secrets.add(selected.api_key)
                    info = selected.model_context_info(model)
                    levels = list(info.supported_reasoning_levels)
                    models.append(
                        {
                            "id": model,
                            "description": profile["models"][model].get("description", ""),
                            "reasoningLevels": levels,
                            "reasoning": selected.reasoning_effort,
                            "images": selected.supports_image_input,
                        }
                    )
                if models:
                    providers.append(
                        {"id": name, "models": sorted(models, key=lambda m: m["id"].casefold())}
                    )
            default = {
                "provider": settings.provider_id,
                "model": settings.model,
                "reasoning": settings.reasoning_effort,
            }
            valid = any(
                x["id"] == default["provider"]
                and any(m["id"] == default["model"] for m in x["models"])
                for x in providers
            )
            if not valid:
                raise ValueError("默认供应商或模型未配置")
            return {
                "valid": True,
                "providers": providers,
                "default": default,
                "permissions": "沿用 Corki 配置",
            }
        except (ValueError, TypeError, OSError) as error:
            return {"valid": False, "providers": [], "error": self.safe_error(error)}

    async def list_threads(self, p):
        offset = integer(p, "offset", 0)
        limit = integer(p, "limit", 40, 100)
        titles = self.state.titles()
        directory = text(p, "directory") if "directory" in p else None
        # URI support must be enabled on the main connection for ATTACH's
        # read-only file URI to work on Windows as well as macOS.
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=rw", uri=True)) as db:
            db.row_factory = sqlite3.Row
            # Filter before pagination and grouping, without hiding or deleting history.
            visible = "archived_at IS NULL AND source != ? "
            parameters = (SessionSource.from_startup_arg("cron").storage_value,)
            cron_database = self.home / "cron/cron.sqlite"
            if cron_database.is_file():
                # Older workers did not record provenance. Their occurrence ID is
                # also the canonical thread ID; never infer this from user text.
                db.execute("ATTACH DATABASE ? AS scheduled", (cron_database.as_uri() + "?mode=ro",))
                visible += (
                    "AND NOT EXISTS (SELECT 1 FROM scheduled.runs "
                    "WHERE runs.id=threads.id) "
                )
            rows = db.execute(
                "SELECT id,cwd,created_at,updated_at FROM threads WHERE " + visible
                + ("AND cwd=? " if directory is not None else "")
                + "ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                parameters + ((directory,) if directory is not None else ()) + (limit + 1, offset),
            ).fetchall()
            result = []
            for row in rows[:limit]:
                value = dict(row)
                first = db.execute(
                    "SELECT user_input FROM turns WHERE thread_id=? AND user_input!=? "
                    "ORDER BY rowid LIMIT 1",
                    (row["id"], ""),
                ).fetchone()
                value["title"] = titles.get(row["id"]) or (
                    display_user_text(first[0]).strip().split("\n")[0][:60] if first else "新会话"
                )
                value["running"] = row["id"] in self.tasks
                result.append(value)
            directories = (
                []
                if directory is not None
                else [
                    {"path": row[0], "name": Path(row[0]).name or row[0]}
                    for row in db.execute(
                        "SELECT cwd FROM threads WHERE " + visible
                        + "GROUP BY cwd ORDER BY MAX(updated_at) DESC,cwd",
                        parameters,
                    )
                ]
            )
        return {
            "threads": result,
            "next": offset + limit if len(rows) > limit else None,
            "directories": directories,
        }

    async def read_thread(self, p):
        # A settled run clears the transient projection. If that happens while
        # SQLite reads are in flight, reread canonical history instead of joining
        # an old page with the new empty cache and claiming the final sequence.
        while True:
            revision = self.lifecycle_revision
            result = await self._read_thread(p)
            if revision == self.lifecycle_revision:
                return result

    async def _read_thread(self, p):
        thread = text(p, "threadId")
        record = await self.archive.read(ThreadId(thread))
        before = p.get("before")
        cursor = (
            None
            if before is None
            else DisplayItemsCursor(ThreadId(thread), integer(p, "before", 0, 2**63 - 1))
        )
        limit = 60
        while True:
            page = await self.repository.load_display_items_page(
                ThreadId(thread), cursor=cursor, limit=limit
            )
            rows = []
            for item in HistoryProjection(page.leading_replacements).feed(page.items):
                if not isinstance(
                    item, (UserMessageItem, AssistantMessageItem, ToolCallItem, ToolResultItem)
                ):
                    continue
                content = message_text(item)
                base = {
                    "id": str(item.id),
                    "sourceId": str(item.id),
                    "createdAt": item.created_at,
                    "threadId": thread,
                    "turnId": str(item.turn_id),
                    "content": content[:PREVIEW_CHARS],
                    "truncated": len(content) > PREVIEW_CHARS,
                    "nextText": PREVIEW_CHARS if len(content) > PREVIEW_CHARS else None,
                }
                if isinstance(item, (UserMessageItem, AssistantMessageItem)):
                    rows.append(
                        {
                            **base,
                            "role": "user" if isinstance(item, UserMessageItem) else "assistant",
                            "imageCount": len(message_images(item)),
                            "phase": getattr(item, "phase", None),
                        }
                    )
                elif isinstance(item, ToolCallItem):
                    rows.append(
                        {
                            **base,
                            "id": f"tool:{item.call.id}",
                            "role": "tool",
                            "name": item.call.name,
                            "callId": str(item.call.id),
                        }
                    )
                else:
                    rows.append(
                        {
                            **base,
                            "id": f"output:{item.call_id}",
                            "role": "tool-result",
                            "error": item.is_error,
                            "callId": str(item.call_id),
                        }
                    )
            if len(dumps(rows).encode()) < 500000 or limit == 1:
                break
            limit = max(1, limit // 2)
        timings = await asyncio.to_thread(
            execution_timings,
            self.database,
            thread,
            {row["callId"] for row in rows if row.get("callId")},
        )
        for row in rows:
            row.update(timings.get((row.get("turnId"), row.get("callId")), {}))
            if row["role"] == "tool" and row.get("startedAt") is not None:
                row["completed"] = row.get("elapsedSeconds") is not None
                row["streaming"] = not row["completed"] and thread in self.tasks
        # A long tool-heavy turn can push its question outside the bounded page.
        # Include only that turn's canonical question as a display anchor; the
        # pagination cursor still covers the intervening history without gaps.
        if rows and rows[0]["role"] != "user" and page.next_cursor is not None:
            with closing(sqlite3.connect(self.database)) as db:
                question = db.execute(
                    "SELECT payload_json FROM conversation_items WHERE thread_id=? "
                    "AND turn_id=? AND kind='user_message' "
                    "AND json_extract(payload_json,'$.retained_from_id') IS NULL AND sequence < "
                    "(SELECT sequence FROM conversation_items WHERE thread_id=? AND id=?) "
                    "ORDER BY sequence DESC LIMIT 1",
                    (thread, rows[0]["turnId"], thread, rows[0]["sourceId"]),
                ).fetchone()
            if question:
                item = item_from_payload("user_message", json.loads(question[0]))
                content = message_text(item)
                rows.insert(0, {
                    "id": str(item.id), "sourceId": str(item.id), "threadId": thread,
                    "turnId": str(item.turn_id), "role": "user", "contextOnly": True,
                    "createdAt": item.created_at, "content": content[:PREVIEW_CHARS],
                    "truncated": len(content) > PREVIEW_CHARS,
                    "nextText": PREVIEW_CHARS if len(content) > PREVIEW_CHARS else None,
                    "imageCount": len(message_images(item)),
                })
        turns = await self.repository.load_display_turns_page(ThreadId(thread), limit=60)
        # Insert cancellation presentation at its own turn boundary, never after
        # a later question. The core model context does not consume these rows.
        visible_turns = {row["turnId"] for row in rows}
        terminal_turns = []
        if visible_turns:
            placeholders = ",".join("?" for _ in visible_turns)
            with closing(sqlite3.connect(self.database)) as db:
                terminal_turns = db.execute(
                    "SELECT id,status,error,updated_at FROM turns WHERE thread_id=? "
                    f"AND id IN ({placeholders}) AND status IN ('cancelled','failed')",
                    (thread, *visible_turns),
                ).fetchall()
        interrupted = {row[0] for row in terminal_turns} & visible_turns
        partials = self.state.interrupted(thread, interrupted, PREVIEW_CHARS)
        for turn, status, error, finished_at in terminal_turns:
            if turn not in visible_turns:
                continue
            index = max(i for i, row in enumerate(rows) if row["turnId"] == turn) + 1
            existing = {row["id"] for row in rows}
            additions = [
                {**row, "createdAt": finished_at}
                for row in partials if row["turnId"] == turn and row["id"] not in existing
            ]
            if status == "failed" and error:
                model_failure = error == "模型调用失败"
                additions.append({
                    "id": f"error:{turn}", "turnId": turn,
                    "role": "assistant" if model_failure else "error",
                    "content": error[:2000], "error": not model_failure,
                    "phase": "final_answer" if model_failure else None,
                    "createdAt": finished_at,
                })
            elif status == "cancelled":
                additions.append({
                    "id": f"stopped:{turn}", "turnId": turn,
                    "role": "stopped", "content": "回答已停止",
                })
            rows[index:index] = additions
        model = await self.repository.load_thread_model_settings(ThreadId(thread))
        return {
            "threadId": thread,
            "directory": str(record.cwd),
            "messages": rows,
            "next": page.next_cursor.before_sequence if page.next_cursor else None,
            "turns": [
                {"id": str(t.id), "status": str(t.status), "error": (t.error or "")[:2000]}
                for t in turns.turns
            ],
            "model": {
                "provider": model.provider,
                "model": model.model,
                "reasoning_effort": model.reasoning_effort,
            }
            if model
            else None,
            "running": thread in self.tasks,
            "receipt": self.state.latest_request(thread),
            "live": list(self.live.get(thread, [])),
            "followUps": self.followups.snapshot(thread),
            "pending": [v for v in self.pending.values() if v["threadId"] == thread],
            "instance": self.instance,
            "sequence": self.sequence,
        }

    async def read_attachment(self, p):
        raw, mime = await asyncio.to_thread(read_asset, self.home, text(p, "assetId"))
        if mime != "text/plain":
            raise ValueError("该附件不是文本")
        return {"text": raw.decode("utf-8")}

    async def read_message_text(self, p):
        thread, message = text(p, "threadId"), text(p, "messageId")
        try:
            item = await asyncio.to_thread(read_message, self.database, thread, message)
        except ValueError as error:
            try:
                content = self.state.interrupted_text(thread, message)
            except LookupError:
                raise error from None
        else:
            content = message_text(item)
        offset = integer(p, "offset", 0, len(content))
        end = min(len(content), offset + PREVIEW_CHARS)
        return {"content": content[offset:end], "next": end if end < len(content) else None}

    async def read_message_image(self, p):
        item = await asyncio.to_thread(
            read_message, self.database, text(p, "threadId"), text(p, "messageId")
        )
        data = await asyncio.to_thread(image_preview, item, integer(p, "index", 0, 100))
        return {"data": data}

    async def rename(self, p):
        thread = text(p, "threadId")
        await self.archive.read(ThreadId(thread))
        title = text(p, "title", limit=120).strip()
        if not title:
            raise ValueError("名称不能为空")
        self.state.rename(thread, title)
        await self.emit("threads.changed", thread)
        return {}

    async def archive_thread(self, p):
        return await self.remove_thread(p, archive=True)

    async def delete(self, p):
        return await self.remove_thread(p, archive=False)

    async def remove_thread(self, p, *, archive):
        thread = text(p, "threadId")
        lock = self.locks.setdefault(thread, asyncio.Lock())
        async with lock:
            if thread in self.tasks:
                raise ValueError("请先停止任务再归档或删除会话")
            runtime = self.runtimes.get(thread)
            if runtime:
                await runtime.aclose()
                self.runtimes.pop(thread, None)
            with closing(self.state.connect()) as db, db:
                db.execute("DELETE FROM followups WHERE thread=?", (thread,))
                db.execute("DELETE FROM followup_settings WHERE thread=?", (thread,))
            if archive:
                await self.archive.archive(ThreadId(thread))
                self.state.put("draft:" + thread, None)
            else:
                await SQLiteThreadDeleteStore(self.database).delete(ThreadId(thread))
                self.state.delete_thread(thread)
                # Invalidate replay rather than leaving deleted message bodies in it.
                self.events.clear()
                self.event_bytes = 0
            self.live.pop(thread, None)
            self.lifecycle_revision += 1
            await self.emit("threads.changed", thread)
            return {"archived" if archive else "deleted": True}

    async def request_read(self, p):
        identity = text(p, "requestId")
        receipt = self.state.request(identity) or self.followups.receipt(identity)
        if receipt is None and identity in self.admissions:
            return {"id": identity, "state": "preparing"}
        return receipt

    async def submit(self, p):
        identity = text(p, "requestId", limit=100)
        digest = hashlib.sha256(dumps(p).encode()).hexdigest()
        pending = self.admissions.get(identity)
        if pending:
            if pending[0] != digest:
                raise ValueError("请求 ID 已用于其他内容")
            return await asyncio.shield(pending[1])
        task = asyncio.create_task(self._submit(p))
        self.admissions[identity] = (digest, task)

        def settled(completed):
            # IPC callers may time out or disappear while admission continues.
            # Cleanup belongs to the admission itself, not to a waiting caller.
            current = self.admissions.get(identity)
            if current is not None and current[1] is completed:
                self.admissions.pop(identity, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(settled)
        return await asyncio.shield(task)

    async def _submit(self, p, *, queued_id=None, force_steer=False):
        identity = text(p, "requestId", limit=100)
        uuid.UUID(identity)
        content = text(p, "content", limit=100000).strip()
        attachments = p.get("attachments", [])
        if not isinstance(attachments, list) or len(attachments) > 8:
            raise ValueError("附件数量无效")
        if not content and not attachments:
            raise ValueError("请输入消息")
        thread = text(p, "threadId", default="") or str(uuid.uuid4())
        digest = hashlib.sha256(dumps(p).encode()).hexdigest()
        if queued_id is not None:
            row = next((r for r in self.followups.rows(thread) if r["id"] == queued_id), None)
            if row is None:
                return self.state.request(identity)
            digest = row["digest"]
        prior = self.state.request(identity)
        if queued_id is None:
            queued = self.followups.receipt(identity, digest)
            if queued:
                return queued
        if prior:
            if prior["digest"] != digest:
                raise ValueError("请求 ID 已用于其他内容")
            return prior
        lock = self.locks.setdefault(thread, asyncio.Lock())
        async with lock:
            if self.state.is_deleted(thread):
                raise ValueError("此会话已删除，请新建会话")
            prior = self.state.request(identity)
            if prior:
                return prior
            mode = p.get("followUpMode", "steer")
            if mode not in ("steer", "queue"):
                raise ValueError("无效的追加方式")
            exists = await self.repository.thread_exists(ThreadId(thread))
            directory = (
                (await self.archive.read(ThreadId(thread))).cwd
                if exists
                else self.directory(text(p, "directory", default=""))
            )
            provider = text(p, "provider")
            model = text(p, "model")
            effort = p.get("reasoning")
            # Use the admitted snapshot; config.read is the explicit reload path.
            settings = self.settings(
                directory, provider=provider, model=model, reasoning_effort=effort
            )
            images = []
            parts = []
            for entry in attachments:
                if not isinstance(entry, dict):
                    raise ValueError("附件格式错误")
                name = text(entry, "name", limit=200)
                if entry.get("assetId"):
                    raw, mime = await asyncio.to_thread(read_asset, self.home, entry["assetId"])
                    if (entry.get("type") == "text") != (mime == "text/plain"):
                        raise ValueError("附件类型不匹配")
                    entry = {
                        **entry,
                        "data": raw.decode("utf-8")
                        if mime == "text/plain"
                        else f"data:{mime};base64," + base64.b64encode(raw).decode(),
                    }
                if entry.get("type") == "image":
                    data = text(entry, "data", limit=1500000)
                    if not settings.supports_image_input:
                        raise ValueError("所选模型未声明支持图片输入")
                    if not data.startswith(
                        (
                            "data:image/png;base64,",
                            "data:image/jpeg;base64,",
                            "data:image/webp;base64,",
                            "data:image/gif;base64,",
                        )
                    ):
                        raise ValueError("不支持的图片类型")
                    from PIL import Image

                    try:
                        raw = base64.b64decode(data.split(",", 1)[1], validate=True)
                        with Image.open(io.BytesIO(raw)) as image:
                            if image.width * image.height > 32_000_000:
                                raise ValueError("图片尺寸过大")
                            image.verify()
                    except Exception as error:
                        raise ValueError("图片内容无效或尺寸过大") from error
                    images.append(ImageAttachment(data))
                elif entry.get("type") == "text":
                    parts.append(f"\n\n附件 {name}:\n" + text(entry, "data", limit=200000))
                else:
                    raise ValueError("不支持的附件类型")
            if queued_id is None:
                self.followups.resume_for_input(thread)
            waiting = any(r["state"] in ("queued", "dispatching", "delivered")
                          for r in self.followups.rows(thread))
            if thread in self.tasks or (queued_id is None and waiting):
                if queued_id is not None and not force_steer:
                    self.followups.mark(queued_id, "queued")
                    return self.followups.receipt(queued_id)
                return await self.followups.accept(
                    p, digest, content + "".join(parts), tuple(images), force=force_steer
                )
            runtime = self.runtimes.get(thread)
            if runtime is None:
                runtime = await self.factory(
                    settings=settings,
                    database_path=self.database,
                    repository=self.repository,
                    home_path=self.home,
                    compatibility_home=Path.home(),
                    thread_id=ThreadId(thread),
                )
                self.runtimes[thread] = runtime

                async def approval(request):
                    value = {"threadId": thread, **asdict(request)}
                    self.pending[request.request_id] = value
                    try:
                        await self.emit("approval", thread, value)
                        # The core router owns this callback task. Keep it alive
                        # until a response or server cancellation closes the request.
                        await asyncio.Future()
                    finally:
                        if self.pending.pop(request.request_id, None) is not None:
                            await self.emit(
                                "interaction.resolved",
                                thread,
                                {"requestId": request.request_id},
                            )

                runtime.set_execution_approval_handler(approval)
                runtime.set_mcp_elicitation_handler(approval)
            else:
                # Refresh insertion order for idle-host retirement.
                self.runtimes.pop(thread)
                self.runtimes[thread] = runtime
                await runtime.update_thread_settings(
                    provider=provider, model=model, reasoning_effort=effort
                )
            # Prepare a bounded display page through the public runtime lifecycle.
            # This acquires the canonical writer lease and materializes thread metadata
            # before the UI can open the newly accepted conversation.
            await runtime.load_display_items_page(limit=1)
            if not exists:
                self.state.rename(thread, content.split("\n")[0][:60] or "附件会话")
            recent = self.state.get("recentDirectories", [])
            self.state.put(
                "recentDirectories",
                [str(directory), *[x for x in recent if x != str(directory)]][:12],
            )
            self.state.admit(identity, thread, digest)
            if queued_id is not None:
                self.followups.remove(queued_id)
            self.live[thread] = [
                {
                    "type": "UserMessageSubmitted",
                    "data": {
                        "request_id": identity,
                        "createdAt": datetime.now(UTC).isoformat(),
                        "text": (content + "".join(parts))[:PREVIEW_CHARS],
                        "turn_id": None,
                    },
                }
            ]
            if queued_id is None:
                self.followups.setting(thread, paused=False)
            self.active_models[thread] = (provider, model, effort)
            self.tasks[thread] = asyncio.create_task(
                self.run(thread, identity, runtime, content + "".join(parts), tuple(images))
            )
            if queued_id is not None:
                # Automatic handoff has no new composer admission/snapshot read.
                # Promote its displayed input before removing the deferred copy.
                await self.emit("UserMessageSubmitted", thread, self.live[thread][0]["data"])
            self.lifecycle_revision += 1
            return self.state.request(identity)

    async def retire_idle_runtimes(self):
        """Bound idle hosts without terminating a turn, approval or background terminal."""
        async with self.retirement_lock:
            for thread, runtime in list(self.runtimes.items()):
                if self.stopping or len(self.runtimes) <= self.runtime_cache_limit:
                    break
                if thread in self.tasks:
                    continue
                lock = self.locks.setdefault(thread, asyncio.Lock())
                if lock.locked():
                    continue
                async with lock:
                    if self.runtimes.get(thread) is not runtime or thread in self.tasks:
                        continue
                    # Only the public Runtime process inventory decides whether
                    # this host still owns interactive/background execution.
                    if await runtime.list_background_terminals():
                        continue
                    if self.stopping or any(p["threadId"] == thread for p in self.pending.values()):
                        continue
                    await runtime.aclose()
                    self.runtimes.pop(thread, None)
                    self.live.pop(thread, None)

    async def run(self, thread, identity, runtime, content, images):
        terminal = False

        async def health(warning):
            data = {"warning": warning, "reason": watch.warning_reason}
            if thread in self.live:
                append_event(self.live[thread], "model.health", data)
            await self.emit("model.health", thread, data)

        watch = ModelCallWatch(health)
        observer_token = request_observer.set(watch)
        try:
            async with aclosing(
                runtime.stream(content, attachments=images, realtime=True)
            ) as events:
                async for event in events:
                    kind = type(event).__name__
                    if kind == "AssistantReasoningDelta" and event.channel != "summary":
                        continue
                    data = asdict(event)
                    if kind in ("AssistantMessageCompleted", "TurnCancelled", "TurnFailed"):
                        data["createdAt"] = datetime.now(UTC).isoformat()
                    if kind == "ToolCallStarted":
                        data["startedAt"] = int(time.time() * 1000)
                    if kind == "ToolCallCompleted":
                        timing = await asyncio.to_thread(
                            execution_timings, self.database, thread, {str(event.tool_call_id)}
                        )
                        data.update(timing.get((str(event.turn_id), str(event.tool_call_id)), {}))
                    if kind == "TurnStarted":
                        terminal = False
                        self.state.update_request(identity, "running", str(event.turn_id))
                        for entry in self.live[thread]:
                            if (
                                entry["type"] == "UserMessageSubmitted"
                                and not entry["data"]["turn_id"]
                            ):
                                entry["data"]["turn_id"] = str(event.turn_id)
                    if kind == "UserInputRequested":
                        value = {
                            "threadId": thread,
                            **data,
                            "kind": "question",
                            "request_id": event.call_id,
                        }
                        self.pending[event.call_id] = value
                    if kind in ("TurnCancelled", "TurnFailed"):
                        self.state.save_interrupted(
                            thread,
                            str(event.turn_id),
                            interrupted_messages(self.live[thread], str(event.turn_id)),
                        )
                    if kind in ("TurnCompleted", "TurnCancelled", "TurnFailed"):
                        terminal = True
                        self.followups.settle(thread, getattr(event, "unsubmitted_inputs", ()))
                        data.pop("unsubmitted_inputs", None)
                        if kind != "TurnCompleted":
                            self.followups.setting(thread, paused=True)
                        # Steers share the canonical turn, not another model loop.
                        for receipt in self.state.unresolved():
                            if receipt["thread_id"] == thread and receipt["turn_id"] == str(
                                event.turn_id
                            ):
                                self.state.update_request(
                                    receipt["id"],
                                    {
                                        "TurnCompleted": "completed",
                                        "TurnCancelled": "cancelled",
                                        "TurnFailed": "failed",
                                    }[kind],
                                )
                        self.state.update_request(
                            identity,
                            {
                                "TurnCompleted": "completed",
                                "TurnCancelled": "cancelled",
                                "TurnFailed": "failed",
                            }[kind],
                            error=data.get("error"),
                        )
                    # Live projection is bounded; canonical history remains authoritative.
                    live = self.live[thread]
                    append_event(live, kind, data)
                    await self.emit(kind, thread, data)
            if not terminal:
                raise RuntimeError("执行结束但未收到完成状态，请重新打开会话核对结果")
        except asyncio.CancelledError:
            self.followups.setting(thread, paused=True)
            # Runtime emits TurnCancelled and then cancels its iterator by design.
            # Without a terminal event, cancellation is not proof of durable completion.
            if not terminal:
                self.followups.unknown(thread)
                self.state.update_request(identity, "outcome-unknown")
                await self.emit(
                    "host.error",
                    thread,
                    {"message": "停止结果尚未确认，请重新打开会话核对状态。"},
                )
        except Exception as error:
            self.followups.unknown(thread)
            self.followups.setting(thread, paused=True)
            message = self.safe_error(error)
            self.state.update_request(identity, "failed", error=message)
            await self.emit("host.error", thread, {"message": message})
        finally:
            await watch.close()
            request_observer.reset(observer_token)
            self.pending = {k: v for k, v in self.pending.items() if v["threadId"] != thread}
            try:
                await self.retire_idle_runtimes()
            finally:
                take_inputs = getattr(runtime, "take_unsubmitted_inputs", None)
                if take_inputs is not None:
                    take_inputs()
                self.tasks.pop(thread, None)
                self.active_models.pop(thread, None)
                self.live.pop(thread, None)
                self.lifecycle_revision += 1
                await self.emit("run.settled", thread, {"requestId": identity})
                await self.followups.changed(thread)
                self.followups.schedule(thread)

    async def stop(self, p):
        thread = text(p, "threadId")
        self.followups.setting(thread, paused=True)
        await self.followups.changed(thread)
        runtime = self.runtimes.get(thread)
        if runtime and thread in self.tasks:
            runtime.defer_goal_continuation()
            await runtime.cancel_active()
        return {"stopping": thread in self.tasks}

    async def respond(self, p):
        identity = text(p, "requestId")
        value = self.pending.get(identity)
        if not value or value["threadId"] != text(p, "threadId"):
            raise ValueError("该请求已结束")
        runtime = self.runtimes[value["threadId"]]
        if value["kind"] == "question":
            ok = runtime.respond_user_input(value["turn_id"], value["call_id"], p.get("response"))
            if not ok:
                raise ValueError("该问题已失效")
        else:
            action = text(p, "action")
            if action not in ("accept", "decline", "cancel"):
                raise ValueError("无效的确认操作")
            content = {}
            if action == "accept" and value["params"].get("mode") != "url":
                content = p.get("content", {})
                schema = value["params"].get("requestedSchema", {})
                fields = schema.get("properties", {})
                if not isinstance(content, dict) or content.keys() - fields.keys():
                    raise ValueError("表单包含无效字段")
                content = dict(content)
                for key, field in fields.items():
                    if key not in content and "default" in field:
                        content[key] = field["default"]
                    if key not in content:
                        if key in schema.get("required", []):
                            raise ValueError(f"请填写 {field.get('title', key)}")
                        continue
                    try:
                        validate_field(content[key], field)
                    except (ValueError, TypeError, KeyError, OverflowError) as error:
                        raise ValueError(f"{field.get('title', key)} 的值不符合要求") from error
            # Route by the core-owned request kind, never an MCP server's name.
            if value["kind"] in ("shell_approval", "patch_approval"):
                scope = content.get("scope", "once")
                if scope not in ("once", "session", "rule"):
                    raise ValueError("无效的批准范围")
                amendment = None
                if action == "accept" and scope == "rule":
                    amendment = value["params"].get("_meta", {}).get("execpolicy_amendment")
                    if value["kind"] != "shell_approval" or not amendment:
                        raise ValueError("当前请求没有可保存的执行规则")
                runtime.respond_execution_approval(
                    identity,
                    action,
                    remember=action == "accept" and scope == "session",
                    execpolicy_amendment=amendment,
                )
            else:
                runtime.respond_mcp_elicitation(
                    value["server_name"],
                    identity,
                    action,
                    content=content,
                )
        self.pending.pop(identity, None)
        await self.emit("interaction.resolved", value["threadId"], {"requestId": identity})
        return {}

    async def read_state(self, p):
        key = text(p, "key")
        if key not in ("ui", "recentDirectories", "deleteConfirmationUntil") and not key.startswith(
            "draft:"
        ):
            raise ValueError("Invalid state key")
        return self.state.get(key)

    async def write_state(self, p):
        key = text(p, "key")
        if key not in ("ui", "recentDirectories", "deleteConfirmationUntil") and not key.startswith(
            "draft:"
        ):
            raise ValueError("Invalid state key")
        value = p.get("value")
        if key.startswith("draft:") and self.state.is_deleted(key[6:]):
            raise ValueError("此会话已删除")
        if key == "deleteConfirmationUntil" and (
            type(value) not in (int, float) or not 0 <= value <= (time.time() + 86401) * 1000
        ):
            raise ValueError("Invalid reminder expiry")
        if len(dumps(value).encode()) > 1500000:
            raise ValueError("草稿过大")
        self.state.put(key, value)
        await self.collect_assets()
        return {}

    async def replay(self, p):
        since = integer(p, "sequence", 0, 2**53 - 1)
        if (
            p.get("instance") != self.instance
            or since > self.sequence
            or (not self.events and since < self.sequence)
            or (self.events and since < self.events[0][0]["sequence"] - 1)
        ):
            return {"reset": True, "instance": self.instance, "sequence": self.sequence}
        return {
            "reset": False,
            "events": [event for event, _ in self.events if event["sequence"] > since],
        }

    async def status(self, p):
        return {"running": list(self.tasks), "instance": self.instance, "sequence": self.sequence}

    async def close(self):
        self.stopping = True
        await asyncio.gather(*self.followups.pumps, return_exceptions=True)
        admissions = [task for _, task in self.admissions.values()]
        for task in admissions:
            task.cancel()
        await asyncio.gather(*admissions, return_exceptions=True)
        self.admissions.clear()
        for runtime in list(self.runtimes.values()):
            runtime.defer_goal_continuation()
            await runtime.cancel_active()
        if self.tasks:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*self.tasks.values(), return_exceptions=True), 15
                )
        await asyncio.gather(*(r.aclose() for r in self.runtimes.values()), return_exceptions=True)
        await self.repository.close()
