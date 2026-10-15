"""Read-only policy-derivation experiment; all payload files are disposable."""

import asyncio
import ctypes
import json
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

from corki.config.permissions import ExecutionPermissions
from corki.execution.backend import sandbox_command
from corki.execution.bundled import bundled_compiler


async def main():
    import corki.execution.backend as backend

    with tempfile.TemporaryDirectory(prefix="corki-helper-runtime-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        roots = {
            Path(sys.executable),
            Path(sys.executable).resolve(),
            Path(sys.executable).resolve().parent,
            Path(sys.executable).parent,
            Path(sys.prefix) / "pyvenv.cfg",
            Path(sysconfig.get_config_var("LIBDIR")) / sysconfig.get_config_var("LDLIBRARY"),
            *(
                Path(sysconfig.get_path(name))
                for name in ("stdlib", "platstdlib", "purelib", "platlib")
            ),
            Path(backend.__file__).resolve().parents[1],
            Path(backend.__file__).resolve().parents[2],
        }
        if sys.platform == "darwin":
            loader = ctypes.CDLL(None)
            count = loader._dyld_image_count
            count.argtypes, count.restype = [], ctypes.c_uint32
            name = loader._dyld_get_image_name
            name.argtypes, name.restype = [ctypes.c_uint32], ctypes.c_char_p
            for index in range(count()):
                if raw := name(index):
                    roots.add(Path(raw.decode()))
        roots = sorted({path.resolve() for path in roots if path.exists()})
        base = [{"access": "write", "path": {"type": "path", "path": str(workspace)}}]
        for label, entries in (
            ("workspace-only", base),
            (
                "helper-runtime",
                [
                    *base,
                    {"access": "read", "path": {"type": "special", "value": {"kind": "minimal"}}},
                    *(
                        {"access": "read", "path": {"type": "path", "path": str(path)}}
                        for path in roots
                    ),
                ],
            ),
        ):
            profile = {
                "type": "managed",
                "network": "restricted",
                "file_system": {
                    "type": "restricted",
                    "entries": entries,
                },
            }
            permissions = ExecutionPermissions(bundled_compiler(), workspace, json.dumps(profile))
            command = await sandbox_command(
                permissions,
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(Path(backend.__file__).with_name("file_helper.py")),
                ],
                workspace,
                file_system_helper=True,
            )
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=5,
                input=json.dumps(
                    {
                        "operation": "patch",
                        "arguments": {
                            "patch": (
                                f"*** Begin Patch\n*** Add File: {label}.txt\n"
                                "+allowed\n*** End Patch"
                            )
                        },
                    }
                ),
            )
            result = {
                "exit": completed.returncode,
                "stdout": completed.stdout[:2000],
                "stderr": completed.stderr[:2000],
            }
            print(
                json.dumps(
                    {
                        "case": label,
                        "result": result,
                        "written": (workspace / (label + ".txt")).exists(),
                    }
                )
            )
            direct = await sandbox_command(
                permissions,
                [str(Path(sys.executable).resolve()), "-I", "-c", "print('bootstrap')"],
                workspace,
                file_system_helper=True,
            )
            checked = subprocess.run(
                direct, cwd=workspace, capture_output=True, text=True, timeout=5
            )
            print(
                json.dumps(
                    {
                        "case": label + "-real-executable",
                        "exit": checked.returncode,
                        "stdout": checked.stdout[:1000],
                        "stderr": checked.stderr[:1000],
                    }
                )
            )
        print(json.dumps({"candidate_helper_read_roots": [str(path) for path in roots]}))
        native = bundled_compiler().resolve()
        native_profile = {
            "type": "managed",
            "network": "restricted",
            "file_system": {
                "type": "restricted",
                "entries": [
                    *base,
                    {"access": "read", "path": {"type": "special", "value": {"kind": "minimal"}}},
                    {"access": "read", "path": {"type": "path", "path": str(native)}},
                ],
            },
        }
        command = await sandbox_command(
            ExecutionPermissions(native, workspace, json.dumps(native_profile)),
            [str(native)],
            workspace,
            file_system_helper=True,
        )
        checked = subprocess.run(
            command, cwd=workspace, capture_output=True, text=True, timeout=5, input=""
        )
        print(
            json.dumps(
                {
                    "case": "native-self-minimal",
                    "exit": checked.returncode,
                    "stdout": checked.stdout[:1000],
                    "stderr": checked.stderr[:1000],
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
