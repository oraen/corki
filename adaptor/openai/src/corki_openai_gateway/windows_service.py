"""Per-user Windows gateway lifecycle, independent of the Corki package.

Kernel objects scope ownership to this data directory. Stop signals the owned
worker, never kills a PID or a process name; a crash releases the singleton lock.
"""

import ctypes
import hashlib
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes as w
from pathlib import Path

from corki_openai_gateway.gateway import main as gateway_main


def state_directory() -> Path:
    home = Path(os.environ.get("CORKI_HOME", str(Path.home() / ".corki"))).expanduser()
    return home.resolve() / "gateway"


def gateway_arguments() -> list[str]:
    directory = state_directory()
    auth = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    return [
        "--port", os.environ.get("CORKI_GATEWAY_PORT", "17001"),
        "--key-file", os.environ.get("CORKI_GATEWAY_KEY_FILE", str(directory / "local.key")),
        "--auth-file", os.environ.get("CORKI_GATEWAY_AUTH_FILE", str(auth / "auth.json")),
    ]


class Handles:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "CreateMutexW": ([ctypes.c_void_p, w.BOOL, w.LPCWSTR], w.HANDLE),
            "OpenMutexW": ([w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
            "ReleaseMutex": ([w.HANDLE], w.BOOL),
            "CreateEventW": ([ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR], w.HANDLE),
            "OpenEventW": ([w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
            "SetEvent": ([w.HANDLE], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in declarations.items():
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result
        identity = os.path.normcase(str(state_directory())).encode("utf-8")
        self.prefix = "Local\\CorkiGateway-" + hashlib.sha256(identity).hexdigest()
        self.handles = []

    def own(self, handle):
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handles.append(handle)
        return handle

    def open_event(self, suffix, access):
        handle = self.api.OpenEventW(access, False, self.prefix + suffix)
        if not handle and ctypes.get_last_error() == 2:
            return None
        return self.own(handle)

    def wait(self, handle, milliseconds):
        result = self.api.WaitForSingleObject(handle, milliseconds)
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    def close(self):
        for handle in reversed(self.handles):
            self.api.CloseHandle(handle)
        self.handles.clear()


def run_worker(objects):
    mutex = objects.api.CreateMutexW(None, True, objects.prefix + "-owner")
    already_running = ctypes.get_last_error() == 183
    objects.own(mutex)
    if already_running:
        return 0
    try:
        stop = objects.own(objects.api.CreateEventW(None, True, False, objects.prefix + "-stop"))
        ready = objects.own(objects.api.CreateEventW(None, True, False, objects.prefix + "-ready"))

        def serve(server):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            objects.api.SetEvent(ready)
            try:
                while objects.wait(stop, 250) == 258:  # WAIT_TIMEOUT
                    if not thread.is_alive():
                        raise RuntimeError("Gateway serving thread exited unexpectedly")
            finally:
                if thread.is_alive():
                    server.shutdown()
                thread.join(timeout=5)

        return gateway_main(gateway_arguments(), serve=serve)
    finally:
        objects.api.ReleaseMutex(mutex)


def start(objects):
    ready = objects.open_event("-ready", 0x100000)
    if ready and objects.wait(ready, 0) == 0:
        print("Gateway is already running.")
        return 0
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "windows-service.log").open("ab") as log:
        child = subprocess.Popen(
            [sys.executable, "-m", "corki_openai_gateway.windows_service", "run"],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True,
        )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if ready is None:
            ready = objects.open_event("-ready", 0x100000)
        if ready and objects.wait(ready, 100) == 0:
            print("Gateway started.")
            return 0
        if child.poll() is not None:
            raise RuntimeError(f"Gateway startup failed; see {directory / 'windows-service.log'}")
        time.sleep(.1)
    raise RuntimeError("Gateway startup timed out; use stop before retrying and inspect the log")


def stop(objects):
    event = objects.open_event("-stop", 0x2)  # EVENT_MODIFY_STATE
    if event is None:
        print("Gateway is already stopped.")
        return 0
    # Keep the event open until the worker has released its ownership lock.
    mutex = objects.api.OpenMutexW(0x100000 | 0x1, False, objects.prefix + "-owner")
    if mutex:
        objects.own(mutex)
    if not objects.api.SetEvent(event):
        raise ctypes.WinError(ctypes.get_last_error())
    if mutex:
        result = objects.wait(mutex, 30000)
        if result not in (0, 0x80):  # Acquired or abandoned after a worker crash.
            raise RuntimeError("Gateway has not stopped; inspect its log before retrying")
        objects.api.ReleaseMutex(mutex)
    print("Gateway stopped; configuration and credentials retained.")
    return 0


def main(argv=None):
    if os.name != "nt":
        raise SystemExit("This entry point requires Windows; use the launchd scripts on macOS")
    args = sys.argv[1:] if argv is None else argv
    if args not in (["start"], ["stop"], ["run"]):
        raise SystemExit("Usage: python -m corki_openai_gateway.windows_service start|stop|run")
    objects = Handles()
    control = None
    try:
        if args[0] != "run":
            control = objects.own(
                objects.api.CreateMutexW(None, False, objects.prefix + "-control")
            )
            if objects.wait(control, 35000) not in (0, 0x80):
                control = None
                raise RuntimeError("Another gateway start/stop operation is still in progress")
        return {"start": start, "stop": stop, "run": run_worker}[args[0]](objects)
    finally:
        if control:
            objects.api.ReleaseMutex(control)
        objects.close()


if __name__ == "__main__":
    raise SystemExit(main())
