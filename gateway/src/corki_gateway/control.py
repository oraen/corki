"""Public management boundary: JSON stdin/stdout, no manager imports."""

import argparse
import asyncio
import contextlib
import errno
import hmac
import http.client
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from corki.cron.locking import FileLock

from . import package_jobs
from .store import Store, root

MAX_BODY = 24 * 1024 * 1024


def bind_server(handler, preferred_port=17003):
    try:
        return ThreadingHTTPServer(("127.0.0.1", preferred_port), handler)
    except OSError as error:
        if error.errno != errno.EADDRINUSE and getattr(error, "winerror", None) != 10048:
            raise
        return ThreadingHTTPServer(("127.0.0.1", 0), handler)


def endpoint(home):
    try:
        value = json.loads((root(home) / "endpoint.json").read_text(encoding="utf-8"))
        if (
            type(value["port"]) is not int
            or not 1 <= value["port"] <= 65535
            or not isinstance(value["token"], str)
        ):
            return None
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return None


def request(home, data, timeout=5):
    info = endpoint(home)
    if info is None:
        raise ConnectionError("网关未开启")
    connection = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=timeout)
    try:
        connection.request(
            "POST",
            "/rpc",
            json.dumps(data),
            {"Authorization": "Bearer " + info["token"], "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        value = json.loads(response.read(MAX_BODY))
        if response.status != 200:
            raise ValueError(value.get("error", "网关请求失败"))
        return value
    finally:
        connection.close()


def offline(home):
    from .plugins import installation_status

    store = Store(home)
    return {
        **store.snapshot(),
        "running": False,
        "installed": installation_status(store),
        "package_jobs": package_jobs.snapshot(store),
        "statuses": {},
        "active": 0,
        "interactions": [],
    }


def status(home):
    try:
        return request(home, {"action": "status"}, timeout=2)
    except (OSError, ConnectionError, ValueError, http.client.HTTPException):
        return offline(home)


def control(home, data):
    if not isinstance(data, dict):
        raise ValueError("请求应为 JSON 对象")
    action = data.get("action", "status")
    if action == "status":
        return status(home)
    if action == "reveal-secret":
        store = Store(home)
        row = store.connection(data.get("id"))
        key = data.get("field")
        if not row or not any(
            field.get("key") == key and field.get("secret")
            for field in row["manifest"].get("fields", [])
        ):
            raise ValueError("密钥字段不存在")
        return {"value": row["config"].get(key, "")}
    if action == "application-message":
        return Store(home).application_message(data.get("id"), data.get("sender"))
    if action == "image-info":
        from .images import asset_info

        return asset_info(home, data.get("id"), data.get("asset"), data.get("thumbnail", False))
    if action == "applications":
        try:
            return request(home, data, timeout=10)
        except (OSError, ConnectionError, http.client.HTTPException):
            result = Store(home).applications(
                data.get("id"), data.get("before"),
                name=data.get("name", ""), status=data.get("status", ""),
            )
            result["reply_activity"] = {}
            return result
    if action == "stop-reply":
        return request(home, data, timeout=10)
    if action == "recall-message":
        return request(home, data, timeout=75)
    if action == "conversation":
        # Read-only polling must not contend with account edits or gateway lifecycle.
        try:
            return request(home, data, timeout=10)
        except (OSError, ConnectionError, http.client.HTTPException):
            from .history import history

            return history(Store(home), data.get("id"), data, {})
    if action in ("dependencies", "uninstall-dependencies"):
        package_jobs.start(Store(home), data.get("id"), action)
        return status(home)
    if action in (
        "channel",
        "configure",
        "reset",
        "delete",
        "delete-connection",
        "save-connection",
        "reset-conversation",
        "reset-current-conversation",
        "prepare-uninstall",
    ):
        store = Store(home)
        connection = store.connection(data.get("id"))
        type_id = connection["type_id"] if connection else data.get("channel", data.get("id"))
        job = package_jobs.snapshot(store).get(type_id)
        if action == "prepare-uninstall":
            if (
                not job
                or job["status"] not in package_jobs.ACTIVE
                or job["token"] != data.get("package_token")
            ):
                raise ValueError("渠道任务已失效")
        elif job and job["status"] in package_jobs.ACTIVE:
            raise ValueError("此渠道正在安装或卸载，请稍后重试")
    guard = FileLock(root(home) / "control.lock")
    if not guard.acquire():
        raise ValueError("网关正在处理其他操作，请稍后重试")
    try:
        current = status(home)
        if action == "power":
            enabled = data.get("enabled")
            if type(enabled) is not bool:
                raise ValueError("开关无效")
            if enabled == current["running"]:
                return current
            if not enabled:
                request(home, {"action": "shutdown"})
                for _ in range(150):
                    if endpoint(home) is None:
                        return offline(home)
                    time.sleep(0.2)
                raise ValueError("网关仍在停止任务，请稍后刷新")
            env = {
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[1])
                + os.pathsep
                + os.environ.get("PYTHONPATH", ""),
                "PYTHONUTF8": "1",
                "CORKI_HOME": str(home),
            }
            process = subprocess.Popen(
                [sys.executable, "-m", "corki_gateway.control", "--home", str(home), "--serve"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=os.name != "nt",
                creationflags=(0x00000200 | 0x00000008) if os.name == "nt" else 0,
            )
            for _ in range(150):
                if process.poll() is not None:
                    raise ValueError("网关启动失败，请检查 Corki Python 环境与本地目录权限")
                result = status(home)
                if result["running"]:
                    return result
                time.sleep(0.2)
            # Never leave a start attempt silently owning a process after timeout.
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(5)
            raise ValueError("网关启动超时")
        if current["running"]:
            return request(home, data, timeout=210)
        if action == "channel" and data.get("enabled"):
            raise ValueError("请先开启转发网关")
        if action in ("interaction", "verification"):
            raise ValueError("网关未开启")
        from .service import Host

        async def change():
            host = Host(home, runner=lambda *args: None, online=False)
            result = await host.change(data)
            result["running"] = False
            return result

        return asyncio.run(change())
    finally:
        guard.close()


async def serve(home):
    from .service import Host

    guard = FileLock(root(home) / "service.lock")
    if not guard.acquire():
        raise ValueError("网关已经运行")
    host, server, worker = None, None, None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    token = secrets.token_urlsafe(32)
    endpoint_path = root(home) / "endpoint.json"
    try:
        host = Host(home)
        await host.start()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if (
                    self.path != "/rpc"
                    or self.headers.get("Origin")
                    or not hmac.compare_digest(
                        self.headers.get("Authorization", ""), "Bearer " + token
                    )
                ):
                    self.send_error(403)
                    return
                self.connection.settimeout(15)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_BODY:
                        raise ValueError("请求体大小无效")
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError("请求无效")

                    async def dispatch():
                        if data.get("action") == "status":
                            return {**host.snapshot(), "port": server.server_port}
                        if data.get("action") == "shutdown":
                            loop.call_later(0.1, stop.set)
                            return {"stopping": True}
                        return await host.change(data)

                    value = asyncio.run_coroutine_threadsafe(dispatch(), loop).result(timeout=205)
                    code = 200
                except Exception as error:
                    value = {
                        "error": str(error)
                        if isinstance(error, ValueError)
                        else "网关处理失败，请稍后重试"
                    }
                    code = 400
                payload = json.dumps(value, ensure_ascii=False).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass

        server = bind_server(Handler)
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        temporary = endpoint_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"port": server.server_port, "token": token}), encoding="utf-8"
        )
        temporary.chmod(0o600)
        temporary.replace(endpoint_path)
        if os.name != "nt":
            import signal

            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)
        await stop.wait()
    finally:
        if host:
            await host.close()
        if server:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
        endpoint_path.unlink(missing_ok=True)
        guard.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--home", type=Path, default=Path(os.environ.get("CORKI_HOME", str(Path.home() / ".corki")))
    )
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--package-job", nargs=2)
    args = parser.parse_args()
    try:
        if args.package_job:
            package_jobs.run(args.home, *args.package_job)
        elif args.serve:
            asyncio.run(serve(args.home))
        else:
            raw = sys.stdin.buffer.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise ValueError("请求过大")
            result = control(args.home, json.loads(raw) if raw else {"action": "status"})
            print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        print(
            json.dumps(
                {
                    "error": str(error)
                    if isinstance(error, ValueError)
                    else "网关操作失败，请检查环境与文件权限"
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
