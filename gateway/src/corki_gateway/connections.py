"""Account instances and explicit shared conversation bindings."""

import json
import uuid
from pathlib import Path


class DuplicateAccount(ValueError):
    pass


class Connections:
    def migrate_connections(self):
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS gateway_meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS connections(
                    id TEXT PRIMARY KEY, type_id TEXT NOT NULL, name TEXT NOT NULL,
                    config TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 0,
                    account TEXT NOT NULL DEFAULT '', state_key TEXT NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS connection_type ON connections(type_id);
                CREATE TABLE IF NOT EXISTS conversations(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, workdir TEXT NOT NULL,
                    thread TEXT NOT NULL UNIQUE);
            """)
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM gateway_meta WHERE key='connections-v1'").fetchone():
                return
            # Keep legacy account IDs, state paths and route hashes exactly as before.
            # Empty channel definitions are components, not phantom account instances.
            for row in db.execute("SELECT * FROM channels").fetchall():
                state = self.root / "accounts" / row["id"]
                used = bool(json.loads(row["config"]) or row["enabled"] or row["account"])
                used = used or (state.is_dir() and any(state.iterdir()))
                used = (
                    used
                    or db.execute(
                        "SELECT 1 FROM routes WHERE channel=? LIMIT 1", (row["id"],)
                    ).fetchone()
                    is not None
                )
                if used:
                    manifest = json.loads(row["manifest"])
                    db.execute(
                        "INSERT OR IGNORE INTO connections VALUES(?,?,?,?,?,?,?,?)",
                        (
                            row["id"],
                            row["id"],
                            manifest["name"],
                            row["config"],
                            row["enabled"],
                            row["account"],
                            row["id"],
                            "",
                        ),
                    )
            db.execute("UPDATE channels SET config='{}', enabled=0, account=''")
            db.execute("INSERT INTO gateway_meta VALUES('connections-v1','1')")

    def connections(self, type_id=None):
        with self.db() as db:
            rows = db.execute(
                "SELECT c.*,t.manifest FROM connections c JOIN channels t "
                "ON t.id=c.type_id "
                + ("WHERE c.type_id=? " if type_id else "")
                + "ORDER BY c.rowid",
                (type_id,) if type_id else (),
            ).fetchall()
        return [
            {**dict(r), "config": json.loads(r["config"]), "manifest": json.loads(r["manifest"])}
            for r in rows
        ]

    def connection(self, identity):
        return next((r for r in self.connections() if r["id"] == identity), None)

    def ensure_connection(self, identity):
        """Compatibility for the original local control protocol's default account IDs."""
        row = self.connection(identity)
        if row:
            return row
        channel = next((r for r in self.channels() if r["id"] == identity), None)
        if not channel:
            raise ValueError("接入不存在")
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO connections(id,type_id,name,state_key) VALUES(?,?,?,?)",
                (identity, identity, channel["manifest"]["name"], identity),
            )
        self.ensure_conversations()
        return self.connection(identity)

    def conversation_list(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM conversations ORDER BY rowid")]

    def save_connection(self, data, *, validate_only=False):
        identity = data.get("id")
        old = self.connection(identity) if identity else None
        if identity and not old:
            raise ValueError("接入已不存在，请刷新")
        type_id = old["type_id"] if old else data.get("channel")
        channel = next((r for r in self.channels() if r["id"] == type_id), None)
        if not channel:
            raise ValueError("渠道不存在")
        if old and data.get("channel", type_id) != type_id:
            raise ValueError("已有接入不能更换渠道，请新建接入")
        name = data.get("name", "")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise ValueError("请填写 1–60 字的接入名称")
        name = name.strip()
        row = old or {**channel, "config": {}}
        config = self.validated_config(row, data.get("values", {}))
        workdir = str(Path(config.get("workdir") or self.home / "main/default").resolve())
        old_workdir = str(
            Path(
                (old or {}).get("config", {}).get("workdir") or self.home / "main/default"
            ).resolve()
        )
        if old and old_workdir != workdir and data.get("confirm_reset") is not True:
            raise ValueError("修改工作目录会重置当前会话，请确认后保存")
        changed = old and any(
            config.get(k, "false" if k == "http_disable_parameters" else None)
            != old["config"].get(k, "false" if k == "http_disable_parameters" else None)
            for k in config.keys() | old["config"].keys()
            if k not in ("workdir", "http_retry_count")
        )
        binding = old["conversation_id"] if old else ""
        new_group = not binding or old_workdir != workdir or changed
        if new_group:
            binding = uuid.uuid4().hex
        identity = identity or "account-" + uuid.uuid4().hex
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM connections WHERE type_id=? AND name=? COLLATE NOCASE AND id<>?",
                (type_id, name, identity),
            ).fetchone():
                raise ValueError("此渠道已有同名接入")
            if validate_only:
                return identity
            if new_group:
                db.execute(
                    "INSERT INTO conversations VALUES(?,?,?,?)",
                    (binding, name, workdir, str(uuid.uuid4())),
                )
            if changed:
                for table in ("peers", "routes", "receipts", "cursors"):
                    db.execute(f"DELETE FROM {table} WHERE channel=?", (identity,))
            db.execute(
                "INSERT INTO connections(id,type_id,name,config,state_key,conversation_id) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,config=excluded.config,conversation_id=excluded.conversation_id",
                (identity, type_id, name, json.dumps(config), identity, binding),
            )
            if changed:
                db.execute("UPDATE connections SET account='' WHERE id=?", (identity,))
        if old and (new_group or changed) and hasattr(self, "http_queue"):
            self.http_queue.skip_pending(identity)
        return identity

    def remove_connection(self, identity):
        with self.db() as db:
            for table in ("peers", "routes", "receipts", "cursors"):
                db.execute(f"DELETE FROM {table} WHERE channel=?", (identity,))
            db.execute("DELETE FROM connections WHERE id=?", (identity,))

    def ensure_conversations(self):
        """Upgrade bindings once per account; never merge separate accounts' histories."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            used = set()
            for row in db.execute("SELECT * FROM connections ORDER BY rowid").fetchall():
                binding = row["conversation_id"]
                group = db.execute("SELECT * FROM conversations WHERE id=?", (binding,)).fetchone()
                if group and binding not in used:
                    used.add(binding)
                    continue
                cwd = str(
                    Path(
                        json.loads(row["config"]).get("workdir") or self.home / "main/default"
                    ).resolve()
                )
                # Preserve the latest legacy independent history.
                # Other old histories remain in core storage.
                previous = (
                    db.execute(
                        "SELECT thread FROM receipts WHERE channel=? ORDER BY updated DESC LIMIT 1",
                        (row["id"],),
                    ).fetchone()
                    if not binding
                    else None
                )
                thread = previous[0] if previous else str(uuid.uuid4())
                if db.execute("SELECT 1 FROM conversations WHERE thread=?", (thread,)).fetchone():
                    thread = str(uuid.uuid4())
                binding = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO conversations VALUES(?,?,?,?)", (binding, row["name"], cwd, thread)
                )
                db.execute(
                    "UPDATE connections SET conversation_id=? WHERE id=?", (binding, row["id"])
                )
                used.add(binding)

    def reset_conversation(self, identity):
        row = self.connection(identity)
        if not row:
            raise ValueError("接入不存在")
        binding = uuid.uuid4().hex
        cwd = str(Path(row["config"].get("workdir") or self.home / "main/default").resolve())
        with self.db() as db:
            db.execute(
                "INSERT INTO conversations VALUES(?,?,?,?)",
                (binding, row["name"], cwd, str(uuid.uuid4())),
            )
            db.execute("UPDATE connections SET conversation_id=? WHERE id=?", (binding, identity))
