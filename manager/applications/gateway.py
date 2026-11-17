"""aiohttp application gateway lifecycle, separate from Agent/model execution."""

import asyncio
import contextlib
import errno
import json
import os
import secrets
import socket
import tempfile
import threading

from .models import AppError

DEFAULT_PORT = 17004


class ApplicationGateway:
    def __init__(self, applications):
        self.apps = applications
        self.path = applications.store.root / "gateway.json"
        self.guard = threading.RLock()
        self.control = None
        self.thread = None
        self.loop = None
        self.stop_event = None
        self.ready = threading.Event()
        self.closed = False
        self.state = "stopped"
        self.error = ""
        self.actual_port = None
        self.mode, self.port = "auto", DEFAULT_PORT
        self.api_key = secrets.token_hex(16)
        enabled = False
        try:
            if self.path.exists():
                config = json.loads(self.path.read_text(encoding="utf-8"))
                self.validate(config.get("mode"), config.get("port"))
                if (
                    type(config.get("enabled")) is not bool
                    or not isinstance(config.get("api_key"), str)
                    or len(config["api_key"]) < 32
                ):
                    raise ValueError()
                self.mode, self.port = config["mode"], config["port"]
                self.api_key, enabled = config["api_key"], config["enabled"]
        except (ValueError, TypeError, OSError, AttributeError):
            self.state, self.error = "failed", "应用网关配置无效，请重新保存网关设置"
        if enabled:
            self.change(dict(enabled=True, mode=self.mode, port=self.port))

    @staticmethod
    def validate(mode, port):
        if mode not in ("auto", "manual"):
            raise AppError("请选择自动端口或指定端口")
        if type(port) is not int or not 1 <= port <= 65535:
            raise AppError("端口必须是 1–65535 的整数")

    def status(self, *, include_key=False):
        with self.guard:
            result = dict(
                state=self.state,
                running=self.state == "running",
                mode=self.mode,
                configured_port=self.port,
                port=self.actual_port,
                preferred_port=DEFAULT_PORT,
                error=self.error,
                host="0.0.0.0",
            )
            if include_key:
                result["api_key"] = self.api_key
            return result

    def change(self, values):
        enabled = values.get("enabled")
        if type(enabled) is not bool:
            raise AppError("网关开关参数无效")
        mode, port = values.get("mode", self.mode), values.get("port", self.port)
        self.validate(mode, port)
        with self.guard:
            if self.closed:
                raise AppError("应用服务正在关闭", 503)
            if self.control and self.control.is_alive():
                raise AppError("网关正在切换，请稍候", 409)
            if self.state == "running" and enabled and mode == self.mode and port == self.port:
                return self.status()
            self.state = "starting" if enabled else "stopping"
            self.error = ""
            self.control = threading.Thread(
                target=self._change,
                args=(enabled, mode, port),
                name="corki-app-gateway-control",
                daemon=True,
            )
            self.control.start()
            return self.status()

    def save(self, enabled):
        value = dict(enabled=enabled, mode=self.mode, port=self.port, api_key=self.api_key)
        fd, temporary = tempfile.mkstemp(prefix=".gateway-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(value, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            self.path.chmod(0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _change(self, enabled, mode, port):
        try:
            self._stop()
            with self.guard:
                self.mode, self.port = mode, port
            if enabled:
                self.ready.clear()
                self.thread = threading.Thread(
                    target=self._run, name="corki-app-gateway", daemon=True
                )
                self.thread.start()
                if not self.ready.wait(10):
                    self._stop()
                    raise AppError("网关启动超时，请重试")
                if self.error:
                    raise AppError(self.error)
            self.save(enabled)
            with self.guard:
                self.state = "running" if enabled else "stopped"
        except Exception as error:
            with contextlib.suppress(Exception):
                self._stop()
            with self.guard:
                self.state, self.error = "failed", str(error)[:500] or "应用网关启动失败"

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception as error:
            with self.guard:
                self.error = str(error)[:500] or "应用网关启动失败"
                self.state = "failed"
        finally:
            with self.guard:
                self.actual_port = None
                self.loop = self.stop_event = None
            self.ready.set()

    async def _serve(self):
        from aiohttp import web

        from corki.storage.file_lock import open_lock, try_lock, unlock_and_close

        from .gateway_protocol import ApplicationProtocol

        lock = open_lock(self.apps.store.root / "gateway.lock")
        if not try_lock(lock):
            lock.close()
            raise AppError("应用网关已由另一个管理服务运行", 409)
        runner, sock = None, None
        try:
            self.loop, self.stop_event = asyncio.get_running_loop(), asyncio.Event()
            protocol = ApplicationProtocol(self.apps, self.api_key)
            runner = web.AppRunner(protocol.web_app(), access_log=None, shutdown_timeout=2)
            await runner.setup()
            preferred = DEFAULT_PORT if self.mode == "auto" else self.port
            for port in [preferred, 0] if self.mode == "auto" else [preferred]:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # Windows exclusive bind prevents a second server silently sharing a port.
                if os.name == "nt":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("0.0.0.0", port))
                    sock.listen(256)
                    sock.setblocking(False)
                    break
                except OSError as error:
                    sock.close()
                    sock = None
                    if (
                        self.mode != "auto"
                        or port == 0
                        or error.errno not in (errno.EADDRINUSE, 10048)
                    ):
                        raise AppError(
                            f"无法监听端口 {port}：{error.strerror or '端口不可用'}"
                        ) from error
            await web.SockSite(runner, sock).start()
            with self.guard:
                self.actual_port = sock.getsockname()[1]
            self.ready.set()
            await self.stop_event.wait()
        finally:
            if runner:
                await runner.cleanup()
            if sock:
                sock.close()
            unlock_and_close(lock)

    def _stop(self):
        thread = self.thread
        if not thread:
            return
        # Startup also signals ready on failure, so cleanup never races a partially
        # initialized event loop or loses a stop request before the loop exists.
        self.ready.wait(10)
        with self.guard:
            loop, stop = self.loop, self.stop_event
        if loop and stop and loop.is_running():
            loop.call_soon_threadsafe(stop.set)
        thread.join(10)
        if thread.is_alive():
            raise AppError("网关仍在关闭，请稍后重试")
        self.thread = None

    def close(self):
        with self.guard:
            self.closed = True
            control = self.control
        if control:
            control.join(15)
        self._stop()
