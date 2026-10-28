"""Manager side of the gateway's JSON control boundary (no runtime imports)."""

import json
import os
import subprocess
import sys
from pathlib import Path


class ChannelGateway:
    def __init__(self, home):
        self.home = home
        self.entry = Path(__file__).resolve().parent.parent / "gateway/bin/control.py"

    def request(self, data=None):
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
