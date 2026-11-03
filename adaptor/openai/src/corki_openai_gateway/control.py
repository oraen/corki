"""Local gateway lifecycle. JSON-only control output, no upstream credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from corki_openai_gateway.gateway import CredentialsUnavailable, read_credentials

# Public local protocol key, never an upstream API credential or login token.
LOCAL_KEY = "sk-f127ac39ckkb278c8abddb4lbcc592zb"


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with temporary.open(
            "xb" if isinstance(value, bytes) else "x",
            encoding=None if isinstance(value, bytes) else "utf-8",
        ) as stream:
            os.chmod(temporary, 0o600)
            stream.write(value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Controller:
    def __init__(self, home: Path, *, provider="openai"):
        self.home = home.expanduser().resolve()
        self.root = self.home / "gateway"
        self.owner = hashlib.sha256(str(self.home).encode()).hexdigest()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.provider = provider
        self.startup_provider = "openai"

    def metadata(self):
        try:
            value = json.loads((self.root / "service.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def status(self, port=None):
        port = port or self.metadata().get("port", 17001)
        result = {"running": False, "occupied": False, "port": port}
        try:
            with self.opener.open(
                f"http://127.0.0.1:{port}/__corki_gateway/health", timeout=1
            ) as response:
                value = json.loads(response.read(8192))
            if value.get("service") == "corki-adaptor" and value.get("version") == 2:
                result.update(
                    running=True,
                    managed=value.get("managed") is True and value.get("owner") == self.owner,
                    instance=value.get("instance"),
                )
                return result
        except (OSError, ValueError, AttributeError):
            pass
        with socket.socket() as probe:
            probe.settimeout(0.3)
            result["occupied"] = probe.connect_ex(("127.0.0.1", port)) == 0
        legacy = self.legacy()
        if (
            result["occupied"]
            and legacy
            and "--port" in legacy[2]
            and int(legacy[2][legacy[2].index("--port") + 1]) == port
        ):
            result.update(running=True, legacy=True, managed=False)
        return result

    def legacy(self):
        if sys.platform != "darwin":
            return None
        import plistlib

        path = Path.home() / "Library/LaunchAgents/local.corki.codex-gateway.plist"
        try:
            value = plistlib.loads(path.read_bytes())
            args = value["ProgramArguments"]
            module = args[args.index("-m") + 1]
            key = Path(args[args.index("--key-file") + 1]).resolve()
            if module not in (
                "corki_openai_gateway",
                "corki.codex_gateway",
                "adaptor.openai",
            ) or not key.is_relative_to(self.home):
                return None
            return path, value, args
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return None

    def migrate_legacy(self):
        if os.name == "nt":
            subprocess.run(
                [sys.executable, "-m", "corki_openai_gateway.windows_service", "stop"],
                env={**os.environ, "CORKI_HOME": str(self.home)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=40,
                check=True,
            )
            return
        legacy = self.legacy()
        if not legacy:
            return
        path, value, args = legacy
        service = f"gui/{os.getuid()}/local.corki.codex-gateway"
        loaded = (
            subprocess.run(
                ["launchctl", "print", service], capture_output=True, timeout=5
            ).returncode
            == 0
        )
        if loaded:
            subprocess.run(
                ["launchctl", "bootout", service], check=True, capture_output=True, timeout=10
            )
        # Retain the private deployment for recovery, but prevent launchd from
        # resurrecting an obsolete listener at the next login.
        write_private(self.root / "legacy-service.plist", path.read_bytes())
        path.unlink()

    def login_job(self, port, auth, *, enabled):
        """Preserve login startup only for deployments that already had it.

        Do not bootstrap a second listener during this login. The owned job is
        loaded at the next login and is explicitly unloaded before stop/rebind.
        """
        if sys.platform != "darwin":
            return
        import plistlib

        path = Path.home() / "Library/LaunchAgents" / f"local.corki.adaptor-{self.owner[:12]}.plist"
        archive = self.root / "legacy-service.plist"
        if not archive.exists() and not path.exists():
            return
        args = [
            sys.executable,
            "-m",
            "corki_openai_gateway",
            "--port",
            str(port),
            "--key-file",
            str(self.root / "local.key"),
            "--auth-file",
            str(auth),
            "--control-file",
            str(self.root / "service.control"),
            "--owner",
            self.owner,
        ]
        args += ["--startup-provider", self.startup_provider]
        environment = {}
        if archive.exists():
            previous = plistlib.loads(archive.read_bytes())
            allowed = {
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
            }
            environment = {
                key: value
                for key, value in previous.get("EnvironmentVariables", {}).items()
                if key.upper() in allowed and isinstance(value, str)
            }
        deployment = {
            "Label": f"local.corki.adaptor-{self.owner[:12]}",
            "ProgramArguments": args,
            "WorkingDirectory": str(self.home),
            "RunAtLoad": enabled,
            "KeepAlive": enabled,
            "EnvironmentVariables": environment,
            "StandardOutPath": str(self.root / "service.log"),
            "StandardErrorPath": str(self.root / "service.log"),
        }
        write_private(path, plistlib.dumps(deployment))

    def stop_login_job(self):
        if sys.platform != "darwin":
            return
        import plistlib

        label = f"local.corki.adaptor-{self.owner[:12]}"
        path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
        if not path.exists():
            return
        deployment = plistlib.loads(path.read_bytes())
        args = deployment["ProgramArguments"]
        if "--owner" not in args or args[args.index("--owner") + 1] != self.owner:
            raise ValueError("网关启动项归属不匹配，未停止该服务")
        service = f"gui/{os.getuid()}/{label}"
        if (
            subprocess.run(
                ["launchctl", "print", service], capture_output=True, timeout=5
            ).returncode
            == 0
        ):
            subprocess.run(
                ["launchctl", "bootout", service], capture_output=True, timeout=10, check=True
            )
        deployment.update(RunAtLoad=False, KeepAlive=False)
        write_private(path, plistlib.dumps(deployment))

    def auth_file(self):
        explicit = os.environ.get("CORKI_GATEWAY_AUTH_FILE")
        if explicit:
            return Path(explicit).expanduser()
        saved = self.metadata().get("auth_file")
        if saved:
            return Path(saved)
        legacy = self.legacy()
        if legacy:
            args = legacy[2]
            if "--auth-file" in args:
                return Path(args[args.index("--auth-file") + 1]).expanduser()
        return (
            Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
            / "auth.json"
        )

    def check(self):
        auth = self.auth_file()
        if self.provider in ("openai", "auto"):
            try:
                read_credentials(auth)
            except CredentialsUnavailable:
                if self.provider == "openai":
                    raise
            else:
                self.startup_provider = "openai"
                return auth
        from corki_openai_gateway.qoder_credentials import QoderCredentialReader

        QoderCredentialReader()()
        self.startup_provider = "qoder"
        return auth

    def stop(self):
        self.stop_login_job()
        metadata = self.metadata()
        legacy = self.legacy()
        default_port = (
            int(legacy[2][legacy[2].index("--port") + 1])
            if legacy and "--port" in legacy[2]
            else 17001
        )
        port = metadata.get("port", default_port)
        status = self.status(port)
        if not status["running"]:
            if status["occupied"]:
                self.migrate_legacy()
                if self.status(port)["occupied"]:
                    raise ValueError("网关端口被其他服务占用，未停止该服务")
            return
        if status.get("legacy"):
            self.migrate_legacy()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                current = self.status(port)
                if not current["running"] and not current["occupied"]:
                    return
                time.sleep(0.1)
            raise ValueError("旧网关尚未停止")
        if not status.get("managed"):
            raise ValueError("该网关不是管理页面启动的，请先关闭原网关")
        token = (self.root / "service.control").read_text().strip()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/__corki_gateway/stop",
            data=b"",
            headers={"X-Corki-Control": token},
            method="POST",
        )
        with self.opener.open(request, timeout=3) as response:
            response.read(8192)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not self.status(port)["occupied"] and not self.status(port)["running"]:
                return
            time.sleep(0.1)
        raise ValueError("旧网关尚未停止，未启动新网关")

    def start_auto(self):
        self.check()
        current = self.status()
        if current.get("running") and current.get("managed"):
            return current
        preferred = int(os.environ.get("CORKI_GATEWAY_PORT", "17001"))
        if not 1 <= preferred <= 65535 or preferred == 17002:
            raise ValueError("网关首选端口配置无效")
        return self.start(preferred, auto_port=True)

    def start(self, port, *, auto_port=False):
        auth = self.check()  # Fail without changing an existing deployment.
        previous = self.metadata().get("port")
        if previous and previous != port:
            owned = self.status(previous)
            if owned.get("managed") or owned.get("legacy"):
                self.stop()
        current = self.status(port)
        if current["running"] and current.get("managed"):
            self.login_job(port, auth, enabled=True)
            return current
        self.stop_login_job()
        self.migrate_legacy()
        if not auto_port:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = self.status(port)
                if not state["occupied"] and not state["running"]:
                    break
                time.sleep(0.1)
            if self.status(port)["occupied"] or self.status(port)["running"]:
                raise ValueError("端口已被其他服务占用，请更换端口")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_private(self.root / "local.key", LOCAL_KEY)
        write_private(self.root / "service.control", secrets.token_urlsafe(32))
        # Retain only transport settings from a migrated deployment, never its
        # login/token environment. Private archived plist stays outside the repo.
        legacy = self.legacy()
        transport = {}
        if sys.platform == "darwin":
            import plistlib

            try:
                deployment = (
                    legacy[1]
                    if legacy
                    else plistlib.loads((self.root / "legacy-service.plist").read_bytes())
                )
                allowed = {
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "NO_PROXY",
                    "SSL_CERT_FILE",
                    "SSL_CERT_DIR",
                }
                transport = {
                    key: value
                    for key, value in deployment.get("EnvironmentVariables", {}).items()
                    if key.upper() in allowed and isinstance(value, str)
                }
            except (OSError, ValueError, TypeError):
                pass
        args = [
            sys.executable,
            "-m",
            "corki_openai_gateway",
            "--port",
            str(port),
            "--key-file",
            str(self.root / "local.key"),
            "--auth-file",
            str(auth),
            "--control-file",
            str(self.root / "service.control"),
            "--owner",
            self.owner,
        ]
        ready_file = self.root / ("service-ready-" + secrets.token_hex(12) + ".json")
        args += ["--startup-provider", self.startup_provider]
        args += ["--ready-file", str(ready_file)]
        if auto_port:
            args += ["--auto-port"]
        options = {
            "cwd": str(self.home),
            "stdin": subprocess.DEVNULL,
            "env": {**transport, **os.environ, "CORKI_HOME": str(self.home)},
        }
        if os.name == "nt":
            # Windows ignores CREATE_NO_WINDOW when DETACHED_PROCESS is also set.
            # In particular, the venv launcher can then give Python a visible console.
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        with (self.root / "service.log").open("ab") as log:
            child = subprocess.Popen(args, stdout=log, stderr=log, **options)
        try:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    ready = json.loads(ready_file.read_text())
                    actual_port = ready["port"]
                    if ready.get("owner") != self.owner or type(actual_port) is not int:
                        raise ValueError("无效的网关启动响应")
                    current = self.status(actual_port)
                    if current.get("managed") and current.get("instance") == ready.get("instance"):
                        write_private(
                            self.root / "service.json",
                            json.dumps(
                                {
                                    "port": actual_port,
                                    "pid": child.pid,
                                    "instance": current["instance"],
                                    "auth_file": str(auth),
                                }
                            ),
                        )
                        self.login_job(actual_port, auth, enabled=True)
                        return current
                except FileNotFoundError:
                    pass
                if child.poll() is not None:
                    raise ValueError("网关启动失败，请检查登录状态或本地网关日志")
                time.sleep(0.15)
            raise ValueError("网关启动超时")
        except BaseException:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            raise
        finally:
            ready_file.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("status", "check", "start", "start-auto", "stop", "register")
    )
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--provider", choices=("auto", "openai", "qoder"))
    args = parser.parse_args(argv)
    try:
        if args.port is not None and (not 1 <= args.port <= 65535 or args.port == 17002):
            raise ValueError("请选择 1–65535 的端口，不能使用管理服务端口")
        controller = Controller(
            args.home,
            provider=args.provider
            or ("auto" if args.action in ("start", "start-auto") else "openai"),
        )
        with exclusive(controller.root / "control.lock"):
            if args.action == "register":
                write_private(
                    controller.root / "installation.json", json.dumps({"python": sys.executable})
                )
                result = {"installed": True}
            elif args.action == "check":
                controller.check()
                result = {"ready": True}
                if controller.startup_provider == "qoder":
                    from corki_openai_gateway.qoder_models import model_catalog

                    result["models"] = model_catalog()
            elif args.action == "start-auto":
                result = controller.start_auto()
            elif args.action == "start":
                port = args.port
                if port is None:
                    try:
                        port = int(
                            json.loads((controller.root / "manager.json").read_text())["port"]
                        )
                    except (OSError, ValueError, KeyError, TypeError):
                        port = controller.metadata().get("port", 17001)
                if type(port) is not int or not 1 <= port <= 65535 or port == 17002:
                    raise ValueError("网关端口配置无效")
                result = controller.start(port)
            elif args.action == "stop":
                controller.stop()
                result = {"running": False}
            else:
                result = controller.status(args.port)
        print(json.dumps({"protocol_version": 1, **result}, ensure_ascii=False))
        return 0
    except CredentialsUnavailable:
        print(
            json.dumps(
                {
                    "protocol_version": 1,
                    "error": "开启失败，没有检测到对应登录信息",
                    "code": "credentials_unavailable",
                },
                ensure_ascii=False,
            )
        )
        return 1
    except ValueError as error:
        print(json.dumps({"protocol_version": 1, "error": str(error)}, ensure_ascii=False))
        return 1
    except Exception:
        # Do not leak environment variables, tokens, paths from upstream errors.
        message = (
            "未找到可用的 Codex 登录，请先登录 Codex 后重试"
            if args.action == "check"
            else "网关操作失败，请检查端口、Codex 登录状态和本地网关日志"
        )
        print(json.dumps({"protocol_version": 1, "error": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
