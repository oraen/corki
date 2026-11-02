"""Versioned JSON-lines transport. stdout is protocol only; stderr is drained."""

import asyncio
import contextlib
import json
import os
import signal
import sys
import uuid

from corki.platforms.processes import close_job, create_subprocess_exec

from .connections import DuplicateAccount
from .errors import delivery_error
from .plugins import assets, dependency_directory
from .programs import environment, find
from .store import BUILTINS


class Connector:
    def __init__(self, host, row):
        self.host, self.row = host, row
        self.id = row["id"]
        self.type_id = row.get("type_id", self.id)
        self.process = None
        self.pending = {}
        self.reader = self.stderr = None
        self.write_lock = asyncio.Lock()

    async def start(self):
        if self.type_id in BUILTINS:
            command = [find("node") or "node", str(assets() / "main.mjs"), self.type_id]
            cwd = assets()
        else:
            manifest = self.row["manifest"]
            cwd = self.host.store.root / "plugins" / self.type_id
            command = [
                sys.executable if manifest["runtime"] == "python" else find("node") or "node",
                str(cwd / manifest["entry"]),
            ]
        self.process = await create_subprocess_exec(
            *command,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024,
            start_new_session=os.name != "nt",
            env={**environment(), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
        )
        self.reader = asyncio.create_task(self.read())
        self.stderr = asyncio.create_task(self.drain())
        directory = self.host.store.root / "accounts" / self.row.get("state_key", self.id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        handshake = await self.call(
            "initialize",
            {
                "protocol": 1,
                "config": self.row["config"],
                "state_directory": str(directory),
                "dependencies": str(
                    dependency_directory(self.host.store, self.type_id) / "package.json"
                )
                if self.type_id in BUILTINS
                else str(cwd / "package.json"),
                "cursor": self.host.store.cursor(self.id),
            },
            timeout=20,
        )
        if not isinstance(handshake, dict) or handshake.get("protocol") != 1:
            raise ValueError("连接器协议版本不兼容")

    async def drain(self):
        # Third-party diagnostics may contain credentials. Do not persist raw stderr.
        while await self.process.stderr.read(8192):
            pass

    async def write(self, value):
        payload = (json.dumps(value, ensure_ascii=False) + "\n").encode()
        if len(payload) > 1024 * 1024:
            raise ValueError("连接器消息超过 1 MB")
        async with self.write_lock:
            self.process.stdin.write(payload)
            await self.process.stdin.drain()

    async def call(self, method, params=None, timeout=45):
        identity = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            await self.write({"id": identity, "method": method, "params": params or {}})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(identity, None)

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("无效的连接器协议")
                if "method" in value:
                    if value["method"] == "inbound":
                        try:
                            result = await self.host.inbound(self.id, value.get("params", {}))
                            await self.write({"id": value.get("id"), "result": result})
                        except ValueError as error:
                            await self.write({"id": value.get("id"), "error": str(error)})
                    elif value["method"] == "status":
                        self.host.connector_status(self.id, value.get("params", {}))
                    elif value["method"] == "account":
                        try:
                            self.host.store.account(self.id, value.get("params", {}).get("id"))
                        except DuplicateAccount:
                            self.host.store.enable(self.id, False)
                            self.host.connector_status(
                                self.id, {"state": "error", "code": "duplicate"}
                            )
                            return
                    elif value["method"] == "cursor":
                        self.host.store.cursor(self.id, value.get("params", {}).get("value"))
                    else:
                        raise ValueError("未知的连接器事件")
                elif value.get("id") in self.pending:
                    future = self.pending[value["id"]]
                    if not future.done():
                        if "error" in value:
                            # Plugin errors can contain secrets; expose a fixed diagnostic.
                            detail = (
                                delivery_error(value.get("diagnostic"))
                                if self.type_id == "dingtalk"
                                else None
                            )
                            future.set_exception(
                                detail or ValueError("渠道请求失败，请检查登录状态或网络")
                            )
                        else:
                            future.set_result(value.get("result"))
        except (ValueError, OSError, asyncio.LimitOverrunError):
            self.host.connector_status(self.id, {"state": "error", "message": "连接器通信异常"})
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ValueError("渠道连接已断开"))

    async def close(self):
        if self.process:
            if self.process.returncode is None:
                with contextlib.suppress(Exception):
                    await self.call("shutdown", timeout=3)
                if self.process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        if os.name == "nt":
                            close_job(self.process)
                        else:
                            os.killpg(self.process.pid, signal.SIGTERM)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self.process.wait(), 3)
                if self.process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        if os.name == "nt":
                            self.process.kill()
                        else:
                            os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
            close_job(self.process)
        for task in (self.reader, self.stderr):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
