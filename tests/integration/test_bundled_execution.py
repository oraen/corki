import asyncio
import json
import os
import re
import shlex
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.execution import bundled
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.fixture
def compiler(tmp_path, monkeypatch):
    home = tmp_path / "isolated-corki-home"
    home.mkdir()
    monkeypatch.setenv("CORKI_HOME", str(home))
    if sys.platform not in {"darwin", "win32"}:
        pytest.skip("real bundled enforcement proof requires macOS or Windows")
    supplied = os.environ.get("CORKI_TEST_BUNDLED_COMPILER")
    if supplied:
        source = Path(supplied)
        root = tmp_path / "host-installation"
        root.mkdir()
        receipt = source.with_name(source.name + ".json")
        manifest = bundled.verify_compiler(source, receipt)
        name = "corki-sandbox.exe" if sys.platform == "win32" else "corki-sandbox"
        shutil.copy2(source, root / name)
        shutil.copy2(receipt, root / "manifest.json")
        for helper in manifest.get("auxiliary_sha256", {}):
            shutil.copy2(source.parent / helper, root / helper)
        monkeypatch.setattr(bundled, "_BUNDLE", root)
    binary = bundled.bundled_compiler()
    if binary is None:
        pytest.skip("requires a platform wheel or a freshly built compiler with receipt")
    return binary


@pytest.fixture
def workspace(tmp_path):
    workspace = tmp_path / "workspace"
    if sys.platform == "win32":
        from corki.platforms.windows_acl import WindowsPrivateACL
        from corki.platforms.windows_file_handles import WindowsFiles

        # Python's mode-0700 temporary directories use OWNER RIGHTS, which
        # the upstream restricted token cannot read. Use Corki's existing
        # explicit user/SYSTEM private ACL; never grant access to other users.
        acl, files = WindowsPrivateACL(), WindowsFiles()
        with acl.descriptor() as descriptor:
            handle = files.open(
                workspace,
                access=0x00120089,  # FILE_GENERIC_READ, including READ_CONTROL
                disposition=2,  # FILE_CREATE
                directory=True,
                security_descriptor=descriptor,
            )
            try:
                acl.verify(handle)
            finally:
                files.kernel.CloseHandle(handle)
    else:
        workspace.mkdir()
    return workspace


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("raw", [False, True])
def test_no_manual_compiler_config_reaches_real_runtime_enforcement(
    tmp_path, compiler, nested, raw, workspace
):
    config = workspace / "config.toml"
    config.write_text(
        '[execution]\nprofile={type="read-only"}' if raw else 'default_permissions=":read-only"'
    )
    chosen = replace(
        CorkiSettings.for_directory(workspace, config_file=config),
        skills_enabled=False,
        tool_mode="code_mode_only" if nested else "direct",
    )
    assert chosen.execution_permissions.compiler == compiler
    destination = workspace / "must-not-exist"
    requests = []
    script = (
        "from pathlib import Path\n"
        "try:\n"
        f" Path({str(destination)!r}).write_text('not allowed')\n"
        " print('unexpected write')\n"
        "except PermissionError:\n"
        " print('native write denial observed')\n"
    )
    command = shlex.join([sys.executable, "-I", "-c", script])
    if sys.platform == "win32":
        probe = workspace / "permission probe.py"
        probe.write_text(script, encoding="utf-8")
        # PowerShell literals: no POSIX shell quoting or native -c quote loss.
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"

        command = f"& {quote(sys.executable)} -I {quote(probe)}"

    class Model:
        pending_session = None

        async def stream(self, request):
            requests.append(request)
            turn, step = request.items[-1].turn_id, new_step_id()
            if len(requests) == 1:
                args = {"cmd": command, "login": False}
                if sys.platform == "win32":
                    args["shell"] = "powershell.exe"
                call = (
                    ToolCall(new_tool_call_id(), "exec_command", args)
                    if not nested
                    else ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        raw_arguments="text(await tools.exec_command(" + json.dumps(args) + "));",
                        input_kind="freeform",
                    )
                )
                item = ToolCallItem(call, turn, step)
            else:
                result = next(
                    item for item in reversed(request.items) if isinstance(item, ToolResultItem)
                )
                # A yielded command is still running. Drive both the code-mode
                # cell and terminal protocols before finishing the fake turn.
                # Otherwise runtime.aclose() cancels a slow native startup.
                running = re.search(r"Process running with session ID (\S+)", result.content)
                if running:
                    self.pending_session = running[1]
                elif "Process exited with code " in result.content:
                    self.pending_session = None
                for part in result.content_items:
                    if not hasattr(part, "text"):
                        continue
                    try:
                        value = json.loads(part.text)
                    except ValueError:
                        continue
                    if isinstance(value, dict):
                        if isinstance(value.get("session_id"), str):
                            self.pending_session = value["session_id"]
                        elif "exit_code" in value:
                            self.pending_session = None
                cell = re.search(r"Script running with cell ID (\S+)", result.content)
                if cell:
                    call = ToolCall(
                        new_tool_call_id(), "wait", {"cell_id": cell[1], "yield_time_ms": 10000}
                    )
                elif self.pending_session is not None:
                    args = {
                        "session_id": self.pending_session,
                        "chars": "",
                        "yield_time_ms": 10000,
                    }
                    call = (
                        ToolCall(new_tool_call_id(), "write_stdin", args)
                        if not nested
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments=(
                                "text(await tools.write_stdin(" + json.dumps(args) + "));"
                            ),
                            input_kind="freeform",
                        )
                    )
                else:
                    call = None
                item = (
                    ToolCallItem(call, turn, step)
                    if call is not None
                    else AssistantMessageItem("done", turn, step)
                )
            yield ModelCompleted((item,))

        async def aclose(self):
            pass

    async def scenario():
        runtime = await LangGraphRuntime.acreate(
            settings=chosen, model=Model(), database_path=tmp_path / "state.db"
        )
        try:
            async with asyncio.timeout(180):
                events = [event async for event in runtime.stream("exercise bundled permissions")]
            assert isinstance(events[-1], TurnCompleted)
            effective = runtime._graph._settings.execution_permissions
            assert effective.compiler == compiler
            assert not effective.needs_resolution
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
    results = [item for item in requests[-1].items if isinstance(item, ToolResultItem)]
    assert any("native write denial observed" in item.content for item in results), [
        item.content for item in results
    ]
    assert not destination.exists()


@pytest.mark.parametrize("trust", [None, "trusted", "untrusted"])
def test_native_default_profile_uses_available_backend(tmp_path, compiler, trust):
    import subprocess

    request = {
        "selection": {"type": "selection", "project_trust": trust},
        "cwd": str(tmp_path),
        "policy_cwd": str(tmp_path),
        "command": [sys.executable],
    }
    if sys.platform == "win32":
        request["runtime_home"] = str(tmp_path / "home")
    result = subprocess.run(
        [str(compiler)], input=json.dumps(request) + "\n",
        text=True, encoding="utf-8", capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert "error" not in response, response
    response = response["ok"]
    assert response["active_permission_profile"]["id"] == (
        ":read-only" if trust is None else ":workspace"
    )


@pytest.mark.parametrize("approval", ["never", "on-request"])
def test_native_command_admission_recognizes_enabled_sandbox(tmp_path, compiler, approval):
    import subprocess

    request = {
        "legacy": {"type": "read-only"},
        "cwd": str(tmp_path),
        "policy_cwd": str(tmp_path),
        "command": ["corki-nonexecuted-admission-fixture"],
        "exec_policy": [],
        "approval_policy": approval,
    }
    if sys.platform == "win32":
        request["runtime_home"] = str(tmp_path / "home")
    result = subprocess.run(
        [str(compiler)], input=json.dumps(request) + "\n",
        text=True, encoding="utf-8", capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert "error" not in response, response
    response = response["ok"]
    assert response["exec_policy_checked"] is True
    assert response["exec_approval"] is None
    assert response["sandbox"] == ("windows_sandbox" if sys.platform == "win32" else "seatbelt")


@pytest.mark.parametrize("approval", ["never", "on-request"])
def test_native_workspace_patch_uses_available_sandbox(workspace, compiler, approval):
    """Compile and execute the real helper; no fixture can fake sandbox admission."""
    import subprocess

    root = workspace.resolve()
    request = {
        "legacy": {"type": "workspace-write"},
        "cwd": str(root),
        "policy_cwd": str(root),
        "command": [str(compiler), "--corki-fs-helper"],
        "approval_policy": approval,
        "file_system_helper": True,
        "native_file_system_helper": True,
        "native_patch": True,
    }
    if sys.platform == "win32":
        request["runtime_home"] = str(root / "home")
    compiled = subprocess.run(
        [str(compiler)], input=json.dumps(request) + "\n", cwd=root,
        text=True, encoding="utf-8", capture_output=True, timeout=30,
    )
    assert compiled.returncode == 0, compiled.stderr
    response = json.loads(compiled.stdout)
    assert "error" not in response, response
    response = response["ok"]
    patch = "*** Begin Patch\n*** Add File: allowed.txt\n+allowed\n*** End Patch"
    arguments = {"patch": patch, "authority": response["patch_authority"], "prepare": True}

    def invoke():
        result = subprocess.run(
            response["command"],
            input=json.dumps({"operation": "patch", "arguments": arguments}),
            cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        envelope = json.loads(result.stdout)
        assert "error" not in envelope, envelope
        return json.loads(envelope["ok"])

    review = invoke()
    assert review["requires_approval"] is False
    assert not (root / "allowed.txt").exists()
    arguments.update(prepare=False, patch=review["patch"])
    outcome = invoke()
    assert outcome["success"] is True, outcome
    assert (root / "allowed.txt").read_text() == "allowed\n"


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows backend selection")
@pytest.mark.parametrize("narrow_reads", [False, True])
def test_windows_backend_selected_before_launch(tmp_path, compiler, narrow_reads):
    import base64
    import subprocess

    request = {
        "cwd": str(tmp_path), "policy_cwd": str(tmp_path),
        "runtime_home": str(tmp_path / "host"),
        "command": [sys.executable],
    }
    if narrow_reads:
        request["profile"] = {
            "type": "managed", "network": "restricted",
            "file_system": {"type": "restricted", "entries": [
                {"access": "write", "path": {"type": "path", "path": str(tmp_path)}}
            ]},
        }
    else:
        request["legacy"] = {"type": "read-only"}
    result = subprocess.run(
        [str(compiler)], input=json.dumps(request) + "\n", cwd=tmp_path,
        text=True, encoding="utf-8", capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    assert "error" not in envelope, envelope
    response = envelope["ok"]
    assert response["sandbox"] == "windows_sandbox"
    command = response["command"]
    assert command[1] == "--corki-windows-sandbox"
    authority = json.loads(base64.urlsafe_b64decode(command[2] + "=" * (-len(command[2]) % 4)))
    assert authority["level"] == ("elevated" if narrow_reads else "restricted-token")
    assert authority["profile"] == response["profile"]
