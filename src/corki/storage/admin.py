"""Local manager row editor; identifiers come from discovered SQLite schemas."""

import base64
import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def encode(value):
    if isinstance(value, bytes):
        return {"$blob": base64.b64encode(value).decode()}
    # Preserve SQLite integers outside JavaScript's exact numeric range.
    if isinstance(value, int) and abs(value) > 9007199254740991:
        return {"$integer": str(value)}
    return value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {"$blob"}:
            return base64.b64decode(value["$blob"], validate=True)
        if set(value) == {"$integer"}:
            return decode(int(value["$integer"]))
        raise ValueError("字段值必须是文本、数字、null 或二进制标记")
    if value is not None and not isinstance(value, (str, int, float)):
        raise ValueError("字段值类型不支持；JSON 字段请使用字符串")
    if isinstance(value, int) and not -(2**63) <= value < 2**63:
        raise ValueError("整数超出 SQLite 64 位范围")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("数字必须是有限值")
    return value


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def split_statements(sql):
    statements = []
    start = 0
    for index, char in enumerate(sql):
        if char == ";" and sqlite3.complete_statement(sql[start : index + 1]):
            statements.append(sql[start : index + 1])
            start = index + 1
    tail = sql[start:]
    # Ignore trailing comments, preserving comments and semicolons inside SQL strings.
    if re.sub(r"/\*.*?\*/|--[^\n]*", "", tail, flags=re.S).strip():
        if not sqlite3.complete_statement(tail + ";"):
            raise ValueError("SQL 语句不完整")
        statements.append(tail)
    if not statements or len(statements) > 50:
        raise ValueError("每次执行需要 1–50 条 SQL 语句")
    return statements


class DatabaseAdmin:
    def __init__(self, home):
        self.home = Path(home).resolve()

    def files(self):
        # Runtime-owned locations only; never scan workspaces, backups or imported documents.
        paths = []
        for root in (self.home, self.home / "sessions", self.home / "cron", self.home / "memory"):
            if root.is_dir():
                for p in root.iterdir():
                    if (
                        p.suffix in {".db", ".sqlite", ".sqlite3"}
                        and p.is_file()
                        and not p.is_symlink()
                        and p.resolve().is_relative_to(self.home)
                    ):
                        with p.open("rb") as f:
                            if f.read(16) == b"SQLite format 3\x00":
                                paths.append(p)
        return sorted(set(paths))

    @contextmanager
    def connect(self, identity, write=False):
        path = next(
            (p for p in self.files() if p.relative_to(self.home).as_posix() == identity), None
        )
        if path is None:
            raise ValueError("数据库不存在或不属于 Corki")
        db = sqlite3.connect(
            path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=3
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        deadline = time.monotonic() + 5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        try:
            yield db
        except sqlite3.Error as error:
            raise ValueError(f"SQLite：{error}") from error
        finally:
            db.close()

    def schema(self, db, table):
        entry = db.execute(
            "SELECT name,type,sql FROM sqlite_schema WHERE name=? AND type IN ('table','view')",
            (table,),
        ).fetchone()
        if entry is None or table.startswith("sqlite_"):
            raise ValueError("数据表不存在")
        cols = [dict(c) for c in db.execute(f"PRAGMA table_xinfo({quote(table)})")]
        keys = [c["name"] for c in sorted(cols, key=lambda c: c["pk"]) if c["pk"]]
        rowid = next(
            (n for n in ("_rowid_", "rowid", "oid") if n not in [c["name"].lower() for c in cols]),
            None,
        )
        # WITHOUT ROWID tables require their declared primary key.
        if rowid:
            try:
                db.execute(f"SELECT {rowid} FROM {quote(table)} LIMIT 0")
            except sqlite3.Error:
                rowid = None
        kind = next((r["type"] for r in db.execute("PRAGMA table_list") if r["name"] == table), "")
        readonly = (
            kind == "shadow"
            or entry["type"] != "table"
            or "VIRTUAL TABLE" in (entry["sql"] or "").upper()
            or not (rowid or keys)
        )
        return cols, rowid, keys, readonly, entry["sql"]

    def catalog(self):
        result = []
        for path in self.files():
            identity = path.relative_to(self.home).as_posix()
            with self.connect(identity) as db:
                tables = [
                    dict(r)
                    for r in db.execute(
                        "SELECT name,type FROM sqlite_schema WHERE type IN ('table','view') "
                        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    )
                ]
            result.append(
                {"id": identity, "name": path.name, "tables": tables, "size": path.stat().st_size}
            )
        return {"databases": result}

    def read(self, identity, table, offset=0, search=""):
        offset = max(0, int(offset))
        if len(search) > 500:
            raise ValueError("搜索内容过长")
        with self.connect(identity) as db:
            cols, rowid, keys, readonly, sql = self.schema(db, table)
            visible = [c for c in cols if c["hidden"] != 1]
            names = [c["name"] for c in visible]
            where = (
                " WHERE "
                + " OR ".join(f"instr(lower(CAST({quote(n)} AS TEXT)),lower(?))>0" for n in names)
                if search and names
                else ""
            )
            args = [search] * len(names) if where else []
            total = db.execute(f"SELECT count(*) FROM {quote(table)}" + where, args).fetchone()[0]
            order = [rowid] if rowid else keys
            selector = ",".join(quote(n) for n in names)
            if rowid:
                selector = rowid + "," + selector
            query = f"SELECT {selector} FROM {quote(table)}" + where
            if order:
                query += " ORDER BY " + ",".join(quote(n) for n in order)
            rows = []
            for row in db.execute(query + " LIMIT 50 OFFSET ?", args + [offset]):
                values = {n: encode(row[i + (1 if rowid else 0)]) for i, n in enumerate(names)}
                key = {rowid: encode(row[0])} if rowid else {n: values[n] for n in keys}
                rows.append({"values": values, "key": key, "revision": revision(values)})
            return dict(
                columns=cols,
                rows=rows,
                total=total,
                offset=offset,
                limit=50,
                readonly=readonly,
                sql=sql,
            )

    def mutate(self, data):
        with self.connect(data.get("database"), True) as db, db:
            table = data.get("table", "")
            cols, rowid, keys, readonly, _ = self.schema(db, table)
            if readonly:
                raise ValueError("此表只支持查看")
            action = data.get("action")
            if action not in ("insert", "update", "delete"):
                raise ValueError("不支持的操作")
            db.execute("BEGIN IMMEDIATE")
            names = [c["name"] for c in cols if c["hidden"] != 1]
            editable = {c["name"] for c in cols if c["hidden"] == 0}
            values = data.get("values", {})
            if not isinstance(values, dict) or not set(values) <= editable:
                raise ValueError("包含未知字段或生成字段")
            if action != "insert":
                key = data.get("key", {})
                if not isinstance(key, dict) or set(key) != set([rowid] if rowid else keys):
                    raise ValueError("记录标识无效")
                where = " AND ".join(f"{quote(n)} IS ?" for n in key)
                params = [decode(v) for v in key.values()]
                existing = db.execute(
                    "SELECT "
                    + ",".join(quote(n) for n in names)
                    + f" FROM {quote(table)} WHERE {where}",
                    params,
                ).fetchall()
                if len(existing) != 1 or revision(
                    {n: encode(existing[0][n]) for n in names}
                ) != data.get("revision"):
                    raise ValueError("记录已变更，请刷新后再操作")
            if action == "delete":
                db.execute(f"DELETE FROM {quote(table)} WHERE {where}", params)
            elif action == "update":
                if not values:
                    raise ValueError("请填写修改内容")
                db.execute(
                    f"UPDATE {quote(table)} SET "
                    + ",".join(f"{quote(n)}=?" for n in values)
                    + f" WHERE {where}",
                    [decode(v) for v in values.values()] + params,
                )
            elif values:
                db.execute(
                    f"INSERT INTO {quote(table)} ("
                    + ",".join(quote(n) for n in values)
                    + ") VALUES ("
                    + ",".join("?" for _ in values)
                    + ")",
                    [decode(v) for v in values.values()],
                )
            else:
                db.execute(f"INSERT INTO {quote(table)} DEFAULT VALUES")
        return {"message": "记录已删除" if action == "delete" else "记录已保存"}

    def query(self, data):
        """Execute a SQL batch atomically in a bounded transaction."""
        sql = data.get("sql")
        if not isinstance(sql, str) or not sql.strip() or len(sql.encode()) > 100_000:
            raise ValueError("请输入 SQL（最多 100 KB）")
        statements = split_statements(sql)
        confirmed = data.get("confirmed") is True
        needs_confirmation = False
        writes = False
        denied = False
        write_actions = {
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_CREATE_INDEX,
            sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_CREATE_VIEW,
            sqlite3.SQLITE_DROP_VIEW,
            sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_DROP_TRIGGER,
            sqlite3.SQLITE_ALTER_TABLE,
            sqlite3.SQLITE_REINDEX,
            sqlite3.SQLITE_ANALYZE,
        }
        read_actions = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
            sqlite3.SQLITE_RECURSIVE,
        }

        def authorize(action, arg1, arg2, database, _source):
            nonlocal writes, denied, needs_confirmation
            if action in write_actions and database in (None, "main"):
                writes = True
                if not confirmed:
                    needs_confirmation = True
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            if action in read_actions and (
                action != sqlite3.SQLITE_FUNCTION
                or (arg2 or "").lower()
                not in {
                    "load_extension",
                    "readfile",
                    "writefile",
                }
            ):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in {
                "table_info",
                "table_xinfo",
                "index_list",
                "index_info",
                "index_xinfo",
                "foreign_key_list",
                "database_list",
                "table_list",
            }:
                return sqlite3.SQLITE_OK
            denied = True
            return sqlite3.SQLITE_DENY

        started = time.monotonic()
        with self.connect(data.get("database"), True) as db:
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2 * 1024 * 1024)
            db.execute("BEGIN")
            db.set_authorizer(authorize)
            try:
                results = []
                size = 0
                for _index, statement in enumerate(statements):
                    before = db.total_changes
                    cursor = db.execute(statement)
                    columns = [c[0] for c in cursor.description or []]
                    rows = []
                    truncated = False
                    if columns:
                        for row in cursor:
                            encoded = [encode(v) for v in row]
                            encoded_size = len(json.dumps(encoded).encode())
                            if len(rows) >= 200 or size + encoded_size > 4 * 1024 * 1024:
                                truncated = True
                                break
                            size += encoded_size
                            rows.append(encoded)
                    cursor.close()
                    results.append(
                        {
                            "columns": columns,
                            "rows": rows,
                            "affected": db.total_changes - before,
                            "truncated": truncated,
                        }
                    )
                affected = db.total_changes
                db.set_authorizer(None)
                db.commit()
            except sqlite3.Error as error:
                db.set_authorizer(None)
                db.rollback()
                if needs_confirmation:
                    return {"requires_confirmation": True}
                if denied:
                    raise ValueError("不支持外部数据库、扩展加载或连接级管理命令") from error
                raise ValueError(f"第 {_index + 1} 条 SQL 失败，整批已回滚：{error}") from error
        return {
            "results": results,
            "columns": columns,
            "rows": rows,
            "affected": affected,
            "truncated": truncated,
            "writes": writes,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
