"""Lossless, content-addressed graph snapshots; canonical history stays in corki.db.

Codex appends completed rollout items and reconstructs history on resume. LangGraph
also needs scheduler versions, pending writes and exact admitted tool/settings
snapshots. Keep that contract, but reference immutable payloads instead of copying
an entire history into every node checkpoint. Legacy SQLite rows remain readable.
"""

import hashlib
import json
import zlib
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.sqlite.utils import search_where

from .checkpoint import checkpoint_serializer

_FORMAT = "corki-refs-v1:"
_SEQUENCES = frozenset(
    {
        "request_items",
        "request_tools",
        "dispatch_tools",
        "last_model_items",
        "streamed_tool_results",
        "pending_input_items",
    }
)
_SCALARS = frozenset({"context_instructions", "turn_base_instructions", "tool_inventory_json"})
_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoint_objects (
    thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL, digest TEXT NOT NULL,
    type TEXT NOT NULL, codec TEXT NOT NULL, raw_size INTEGER NOT NULL, value BLOB NOT NULL,
    PRIMARY KEY(thread_id,checkpoint_ns,digest)
);
"""


@dataclass
class _Encoded:
    envelope: dict


class _Serializer:
    def __init__(self, inner):
        self.inner = inner

    def dumps_typed(self, value):
        return self.inner.dumps_typed(value)

    def loads_typed(self, value):
        kind, payload = value
        if kind.startswith(_FORMAT):
            return _Encoded(
                self.inner.loads_typed((kind[len(_FORMAT) :], zlib.decompress(payload)))
            )
        return self.inner.loads_typed(value)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _digest(kind, payload):
    return hashlib.sha256(kind.encode() + b"\0" + payload).hexdigest()


class CompactSqliteSaver(AsyncSqliteSaver):
    """Preserve all checkpoint IDs, versions and pending writes without lossy pruning."""

    def __init__(self, conn, *, serde=None):
        super().__init__(conn, serde=_Serializer(serde or checkpoint_serializer()))
        self._objects_setup = False
        # Cache compressed bytes by their content hash, never by object identity.
        # This avoids recompressing stable history while detecting changed payloads.
        self._compressed_cache = OrderedDict()
        self._compressed_cache_bytes = 0

    async def setup(self):
        if self.is_setup and self._objects_setup:
            return
        await super().setup()
        async with self.lock:
            if not self._objects_setup:
                await self.conn.execute(_SCHEMA)
                await self.conn.commit()
                self._objects_setup = True

    def _reference(self, value, objects):
        kind, payload = self.serde.dumps_typed(value)
        identity = _digest(kind, payload)
        if identity not in objects:
            if identity in self._compressed_cache:
                row = self._compressed_cache.pop(identity)
                self._compressed_cache[identity] = row
                objects[identity] = row
                return identity
            packed = zlib.compress(payload, 1) if len(payload) >= 1024 else payload
            compressed = len(packed) < len(payload)
            objects[identity] = (
                identity,
                kind,
                "zlib" if compressed else "raw",
                len(payload),
                packed if compressed else payload,
            )
            row = objects[identity]
            if len(row[-1]) <= 4 * 1024 * 1024:
                self._compressed_cache[identity] = row
                self._compressed_cache_bytes += len(row[-1])
                while (
                    self._compressed_cache_bytes > 4 * 1024 * 1024
                    or len(self._compressed_cache) > 4096
                ):
                    _, removed = self._compressed_cache.popitem(last=False)
                    self._compressed_cache_bytes -= len(removed[-1])
        return identity

    def _field(self, channel, value, objects):
        if channel in _SEQUENCES and isinstance(value, (list, tuple)):
            return {"items": [self._reference(item, objects) for item in value]}
        if channel in _SCALARS and isinstance(value, str):
            return {"scalar": self._reference(value, objects)}
        return None

    def _envelope(self, value):
        kind, payload = self.serde.dumps_typed(value)
        return _FORMAT + kind, zlib.compress(payload, 1)

    async def _insert_objects(self, thread, namespace, objects):
        if objects:
            await self.conn.executemany(
                "INSERT OR IGNORE INTO checkpoint_objects VALUES(?,?,?,?,?,?,?)",
                [(thread, namespace, *row) for row in objects.values()],
            )

    async def aput(self, config, checkpoint, metadata, new_versions):
        await self.setup()
        thread = str(config["configurable"]["thread_id"])
        namespace = config["configurable"]["checkpoint_ns"]
        objects, references = {}, {}
        channels = dict(checkpoint.get("channel_values", {}))
        for channel, value in list(channels.items()):
            reference = self._field(channel, value, objects)
            if reference is not None:
                references[channel] = reference
                del channels[channel]
        kind, payload = self._envelope(
            {
                "checkpoint": {**checkpoint, "channel_values": channels},
                "fields": references,
            }
        )
        meta = json.dumps(get_checkpoint_metadata(config, metadata), ensure_ascii=False).encode()
        async with self.lock:
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                await self._insert_objects(thread, namespace, objects)
                await self.conn.execute(
                    "INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?,?,?)",
                    (
                        thread,
                        namespace,
                        checkpoint["id"],
                        config["configurable"].get("checkpoint_id"),
                        kind,
                        payload,
                        meta,
                    ),
                )
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise
        return {
            "configurable": {
                "thread_id": thread,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await self.setup()
        thread = str(config["configurable"]["thread_id"])
        namespace = str(config["configurable"].get("checkpoint_ns", ""))
        objects, rows = {}, []
        for index, (channel, value) in enumerate(writes):
            reference = self._field(channel, value, objects)
            kind, payload = (
                self._envelope({"value": reference})
                if reference is not None
                else self.serde.dumps_typed(value)
            )
            rows.append(
                (
                    thread,
                    namespace,
                    str(config["configurable"]["checkpoint_id"]),
                    task_id,
                    WRITES_IDX_MAP.get(channel, index),
                    channel,
                    kind,
                    payload,
                )
            )
        operation = (
            "REPLACE" if all(channel in WRITES_IDX_MAP for channel, _ in writes) else "IGNORE"
        )
        async with self.lock:
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                await self._insert_objects(thread, namespace, objects)
                await self.conn.executemany(
                    f"INSERT OR {operation} INTO writes VALUES(?,?,?,?,?,?,?,?)",
                    rows,
                )
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise

    async def _restore(self, stored):
        if stored is None:
            return None
        config = stored.config["configurable"]
        envelopes = [stored.checkpoint, *(w[2] for w in stored.pending_writes or [])]
        references = set()
        for encoded in envelopes:
            if not isinstance(encoded, _Encoded):
                continue
            fields = encoded.envelope.get("fields", {"value": encoded.envelope.get("value")})
            for reference in fields.values():
                if reference is not None:
                    references.update(reference.get("items", [reference.get("scalar")]))
        payloads = {}
        identities = sorted(references)
        # Caller retains both the local lock and a SQLite read snapshot until
        # hydration finishes, including when another process deletes the thread.
        for start in range(0, len(identities), 400):
            batch = identities[start : start + 400]
            async with self.conn.execute(
                "SELECT digest,type,codec,raw_size,value FROM checkpoint_objects "
                "WHERE thread_id=? AND checkpoint_ns=? AND digest IN ("
                + ",".join("?" for _ in batch)
                + ")",
                (str(config["thread_id"]), str(config.get("checkpoint_ns", "")), *batch),
            ) as cursor:
                for identity, kind, codec, length, payload in await cursor.fetchall():
                    if codec not in ("zlib", "raw"):
                        raise ValueError("Unknown checkpoint object codec")
                    raw = zlib.decompress(payload) if codec == "zlib" else payload
                    if len(raw) != length or _digest(kind, raw) != identity:
                        raise ValueError("Corrupt checkpoint object")
                    payloads[identity] = (kind, raw)
        if references - payloads.keys():
            raise ValueError("Missing checkpoint object; refusing incomplete recovery")

        def field(reference):
            # Deserialize each occurrence independently, matching ordinary SQLite
            # snapshots: mutable nested tool arguments must never alias across fields.
            if "items" in reference:
                return [self.serde.loads_typed(payloads[key]) for key in reference["items"]]
            return self.serde.loads_typed(payloads[reference["scalar"]])

        def restore(value):
            if not isinstance(value, _Encoded):
                return value
            envelope = value.envelope
            if "checkpoint" in envelope:
                checkpoint = envelope["checkpoint"]
                checkpoint["channel_values"].update(
                    {k: field(v) for k, v in envelope["fields"].items()}
                )
                return checkpoint
            return field(envelope["value"])

        return stored._replace(
            checkpoint=restore(stored.checkpoint),
            pending_writes=[
                (task, channel, restore(value))
                for task, channel, value in stored.pending_writes or []
            ],
        )

    @asynccontextmanager
    async def _read_snapshot(self):
        async with self.lock:
            owned = not self.conn.in_transaction
            try:
                if owned:
                    await self.conn.execute("BEGIN")
                yield
            finally:
                if owned:
                    # Also queue rollback if cancellation arrived while BEGIN's
                    # worker-thread operation was still completing.
                    await self.conn.rollback()

    async def _tuple(self, row, config=None):
        thread, namespace, identity, parent, kind, payload, metadata = row
        async with self.conn.execute(
            "SELECT task_id,channel,type,value FROM writes WHERE thread_id=? "
            "AND checkpoint_ns=? AND checkpoint_id=? ORDER BY task_id,idx",
            (thread, namespace, identity),
        ) as cursor:
            writes = [
                (task, channel, self.serde.loads_typed((typ, value)))
                for task, channel, typ, value in await cursor.fetchall()
            ]
        return await self._restore(
            CheckpointTuple(
                config
                or {
                    "configurable": {
                        "thread_id": thread,
                        "checkpoint_ns": namespace,
                        "checkpoint_id": identity,
                    }
                },
                self.serde.loads_typed((kind, payload)),
                json.loads(metadata) if metadata is not None else {},
                {
                    "configurable": {
                        "thread_id": thread,
                        "checkpoint_ns": namespace,
                        "checkpoint_id": parent,
                    }
                }
                if parent
                else None,
                writes,
            )
        )

    async def aget_tuple(self, config):
        await self.setup()
        identity = get_checkpoint_id(config)
        namespace = config["configurable"].get("checkpoint_ns", "")
        parameters = (str(config["configurable"]["thread_id"]), namespace)
        query = (
            "SELECT thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,type,"
            "checkpoint,metadata FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? "
        )
        if identity:
            query += "AND checkpoint_id=?"
            parameters += (identity,)
        else:
            query += "ORDER BY checkpoint_id DESC LIMIT 1"
        async with self._read_snapshot():
            async with self.conn.execute(query, parameters) as cursor:
                row = await cursor.fetchone()
            return await self._tuple(row, config if identity else None) if row else None

    async def alist(self, config, *, filter=None, before=None, limit=None):
        await self.setup()
        where, parameters = search_where(config, filter, before)
        query = (
            "SELECT thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,type,"
            "checkpoint,metadata FROM checkpoints " + where + " ORDER BY checkpoint_id DESC"
        )
        if limit is not None:
            query += " LIMIT ?"
            parameters = (*parameters, limit)
        async with self._read_snapshot(), self.conn.execute(query, parameters) as cursor:
            async for row in cursor:
                yield await self._tuple(row)

    async def adelete_thread(self, thread_id):
        await self.setup()
        async with self.lock:
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                for table in ("writes", "checkpoints", "checkpoint_objects"):
                    await self.conn.execute(
                        f"DELETE FROM {table} WHERE thread_id=?", (str(thread_id),)
                    )
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise
