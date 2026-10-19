"""Persistent SQL documents and bounded result tabs for the local manager."""

import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from corki.storage.admin import DatabaseAdmin

LOCK = threading.RLock()


class QueryWorkspace:
    def __init__(self, home):
        self.home = Path(home)
        self.root = self.home / "manager/sql-workspace"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
            raise ValueError("查询标识无效")
        path = self.root / identity
        if path.is_symlink():
            raise ValueError("查询路径无效")
        return path

    def read(self, path):
        if not path.is_file():
            raise ValueError("查询或结果已删除，请刷新")
        return json.loads(path.read_text())

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = json.dumps(data, ensure_ascii=True)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def document(self, identity):
        return self.read(self.path(identity) / "query.json")

    def listing(self):
        with LOCK:
            docs = []
            for p in sorted(self.root.glob("*/query.json")):
                if p.parent.is_symlink():
                    continue
                doc = self.read(p)
                doc["results"] = [self.read(f) for f in sorted(p.parent.glob("*/meta.json"))]
                doc["results"].sort(key=lambda r: r["created"])
                docs.append(doc)
            return {"queries": sorted(docs, key=lambda d: d["created"])}

    def result(self, query, result):
        with LOCK:
            self.document(query)
            return self.read(self.path(query) / self.path(result).name / "data.json")

    def mutate(self, data):
        with LOCK:
            action = data.get("action")
            if action == "create":
                if len(list(self.root.glob("*/query.json"))) >= 100:
                    raise ValueError("最多保留 100 个查询，请先关闭不需要的标签")
                database = data.get("database")
                with DatabaseAdmin(self.home).connect(database):
                    pass
                identity = uuid.uuid4().hex
                doc = {
                    "id": identity,
                    "name": "新查询",
                    "database": database,
                    "sql": "",
                    "revision": 1,
                    "created": time.time(),
                }
                self.write(self.path(identity) / "query.json", doc)
                return doc
            identity = data.get("id")
            doc = self.document(identity)
            path = self.path(identity)
            if action == "save":
                if data.get("revision") != doc["revision"]:
                    raise ValueError("查询已在其他页面修改，请刷新；本地草稿已保留")
                sql, name = data.get("sql"), data.get("name")
                if not isinstance(sql, str) or len(sql.encode()) > 100_000:
                    raise ValueError("SQL 最多 100 KB")
                if not isinstance(name, str) or not name.strip() or len(name) > 80:
                    raise ValueError("查询名称应为 1–80 个字符")
                doc.update(sql=sql, name=name.strip(), revision=doc["revision"] + 1)
                self.write(path / "query.json", doc)
                return doc
            if action == "close":
                if data.get("revision") != doc["revision"]:
                    raise ValueError("查询已更新，请刷新后再关闭")
                shutil.rmtree(path)
                return {"message": "查询及其结果已删除"}
            result_id = data.get("result")
            if action == "close-result":
                result_path = path / self.path(result_id).name
                if result_path.is_symlink():
                    raise ValueError("结果路径无效")
                if result_path.exists():
                    shutil.rmtree(result_path)
                return {"message": "结果已删除"}
            if action != "run":
                raise ValueError("不支持的操作")
            # Reserve an ID before executing; a lost response must not cause a repeat write.
            result_path = path / self.path(result_id).name
            if (result_path / "meta.json").exists():
                return {"result": self.read(result_path / "meta.json")}
            if len(list(path.glob("*/meta.json"))) >= 100:
                raise ValueError("此查询已有 100 个结果，请关闭不需要的结果标签")
            used = sum(p.stat().st_size for p in self.root.glob("*/*/data.json"))
            if used > 60 * 1024 * 1024:
                raise ValueError("结果存储接近 64 MB，请先关闭不需要的结果标签")
            sql = data.get("sql")
            if not isinstance(sql, str) or not sql.strip() or len(sql.encode()) > 100_000:
                raise ValueError("请输入 SQL（最多 100 KB）")
            meta = {"id": result_id, "created": time.time(), "status": "unknown"}
            self.write(result_path / "meta.json", meta)
            self.write(
                result_path / "data.json",
                {
                    "sql": sql,
                    "status": "unknown",
                    "error": "执行状态未确认，可能因服务中断；请核查数据后再决定是否执行。",
                },
            )
            try:
                outcome = DatabaseAdmin(self.home).query(
                    {
                        "database": doc["database"],
                        "sql": sql,
                        "confirmed": data.get("confirmed") is True,
                    }
                )
                if outcome.get("requires_confirmation"):
                    shutil.rmtree(result_path)
                    return outcome
                # Batch payload only; avoid storing the final result twice.
                for key in ("columns", "rows", "truncated"):
                    outcome.pop(key, None)
                outcome.update(sql=sql, status="completed")
            except ValueError as error:
                outcome = {"sql": sql, "status": "failed", "error": str(error)}
            self.write(result_path / "data.json", outcome)
            meta["status"] = outcome["status"]
            self.write(result_path / "meta.json", meta)
            return {"result": meta}
