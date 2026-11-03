"""Manager side of the gateway's JSON control boundary (no runtime imports)."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from corki.platforms.processes import background_process_options


class ChannelGateway:
    def __init__(self, home):
        self.home = home
        self.entry = Path(__file__).resolve().parent.parent / "gateway/bin/control.py"

    def request(self, data=None):
        if isinstance(data, dict) and data.get("action") == "reveal-secret":
            return self.reveal_secret(data)
        if not self.entry.is_file():
            raise ValueError("未找到转发网关，请使用包含 gateway 的完整 Corki 安装")
        try:
            result = subprocess.run(
                [sys.executable, str(self.entry), "--home", str(self.home)],
                input=json.dumps(data or {"action": "status"}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=220,
                env={**os.environ, "PYTHONUTF8": "1"},
                **background_process_options(),
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError("网关操作尚未完成，请稍后刷新状态") from error
        try:
            value = json.loads(result.stdout)
        except ValueError as error:
            raise ValueError("网关未返回有效结果，请检查运行环境") from error
        if result.returncode or "error" in value:
            raise ValueError(value.get("error", "网关操作失败"))
        return value

    def reveal_secret(self, data):
        """Read existing local credentials without starting the gateway or migrating data.

        This read-only projection uses the gateway's connections/channels tables;
        all configuration writes still go through its JSON control boundary.
        """
        identity, field = data.get("id"), data.get("field")
        if not isinstance(identity, str) or not isinstance(field, str):
            raise ValueError("密钥字段不存在")
        path = (Path(self.home) / "gateway" / "gateway.db").resolve()
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                row = db.execute(
                    "SELECT c.config,t.manifest FROM connections c "
                    "JOIN channels t ON t.id=c.type_id WHERE c.id=?",
                    (identity,),
                ).fetchone()
            if row is None:
                raise ValueError("密钥字段不存在")
            config, manifest = (json.loads(value) for value in row)
            if not any(
                item.get("key") == field and item.get("secret")
                for item in manifest.get("fields", [])
            ):
                raise ValueError("密钥字段不存在")
            return {"value": config.get(field, "")}
        except sqlite3.Error as error:
            raise ValueError("无法读取本地密钥，请刷新后重试") from error
