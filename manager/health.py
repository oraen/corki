"""Explicit, read-only diagnostics outside the application startup path."""

import copy
import sqlite3
import threading
import time
import uuid
from contextlib import closing

from corki.config import document
from corki.config.settings import CorkiSettings


class HealthChecks:
    def __init__(self, home, workspace):
        self.home, self.workspace = home, workspace
        self.lock = threading.Lock()
        self.worker = None
        self.result = self._empty()

    def _empty(self):
        return {"id": None, "running": False, "items": [
            {"id": key, "name": name, "status": "waiting", "detail": ""}
            for key, name in [
                ("config-syntax", "配置文件格式"),
                ("config-values", "配置字段与参数"),
                ("sessions", "会话数据库"),
                ("checkpoints", "上下文数据库"),
                ("desktop", "桌面状态数据库"),
            ]
        ]}

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.result)

    def start(self):
        with self.lock:
            if not self.result["running"]:
                self.result = self._empty()
                self.result.update(id=str(uuid.uuid4()), running=True)
                self.worker = threading.Thread(target=self._run, daemon=True,
                                               name="corki-health-check")
                self.worker.start()
            return copy.deepcopy(self.result)

    def _config_path(self):
        path = document.select_file(self.home)
        if not path.is_file():
            raise ValueError("未找到配置文件，请先完成模型配置")
        return path

    def _syntax(self):
        path = self._config_path()
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("配置文件为空，请填写有效配置")
        try:
            document.loads(text, path)
        except (ValueError, TypeError):
            # Diagnostics must not echo a source line containing a credential.
            raise ValueError("配置文件格式错误，请在配置文件页面检查语法") from None
        return "配置文件格式正确"

    def _values(self):
        self._syntax()
        try:
            CorkiSettings.for_directory(self.workspace, config_file=self._config_path())
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise ValueError("配置字段或参数无效，请检查字段类型、取值范围和模型设置") from None
        return "配置字段与参数有效"

    def _database(self, relative):
        path = self.home / relative
        if not path.is_file():
            return "尚未创建，首次使用时自动建立"
        # Never initialize, migrate, repair or create a database in this checker.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=5)) as db:
            deadline = time.monotonic() + 120
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            rows = db.execute("PRAGMA quick_check").fetchall()
            if rows != [("ok",)]:
                raise ValueError("数据库完整性检查未通过，请先备份数据")
        return "数据库完整性正常"

    def _run(self):
        checks = [self._syntax, self._values,
                  lambda: self._database("sessions/corki.db"),
                  lambda: self._database("sessions/checkpoints.db"),
                  lambda: self._database("app/desktop.db")]
        try:
            for index, check in enumerate(checks):
                with self.lock:
                    self.result["items"][index]["status"] = "running"
                try:
                    detail, status = check(), "normal"
                except (OSError, UnicodeError):
                    detail, status = "无法读取文件，请检查文件权限和编码", "abnormal"
                except ValueError as error:
                    detail, status = str(error), "abnormal"
                except sqlite3.Error:
                    detail, status = "数据库检查失败或超时，请检查文件占用或完整性", "abnormal"
                except Exception:
                    detail, status = "检查未能完成，请查看本地运行环境", "abnormal"
                with self.lock:
                    self.result["items"][index].update(status=status, detail=detail)
        finally:
            with self.lock:
                self.result["running"] = False
