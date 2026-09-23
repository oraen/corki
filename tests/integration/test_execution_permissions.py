import asyncio
import json
import os
import shlex
import socket
import sys
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


def _engine():
    engine = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not engine or sys.platform != "darwin":
        pytest.skip("requires built native sandbox compiler and macOS Seatbelt")
    return Path(engine)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("tty", [False, True])
def test_runtime_permissions_do_not_follow_model_workdir(tmp_path, nested, tty):
    engine = _engine()
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    inside = root / "inside.txt"
    policy = ExecutionPermissions(
        engine,
        root,
        json.dumps(
            {"type": "workspace-write", "exclude_tmpdir_env_var": True, "exclude_slash_tmp": True}
        ),
    )
    script = f"""
from pathlib import Path
Path({str(inside)!r}).write_text('allowed')
try:
    Path({str(outside)!r}).write_text('ESCAPE')
except OSError:
    print('DENIED', flush=True)
else:
    print('ESCAPED', flush=True)
"""
    arguments = {
        "cmd": shlex.join([sys.executable, "-I", "-c", script]),
        "workdir": str(tmp_path),
        "login": False,
        "tty": tty,
        "yield_time_ms": 1000,
    }
    asyncio.run(_run(tmp_path, policy, "exec_command", arguments, nested))
    assert inside.read_text() == "allowed"
    assert not outside.exists()


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("protected", [False, True])
def test_runtime_patch_uses_same_permissions(tmp_path, nested, protected):
    engine = _engine()
    (tmp_path / ".codex").mkdir()
    name = ".codex/config.toml" if protected else "allowed.txt"
    policy = ExecutionPermissions(engine, tmp_path, json.dumps({"type": "workspace-write"}))
    args = {"patch": f"*** Begin Patch\n*** Add File: {name}\n+fixture\n*** End Patch"}
    asyncio.run(_run(tmp_path, policy, "apply_patch", args, nested))
    assert (tmp_path / name).exists() is not protected


@pytest.mark.parametrize("nested", [False, True])
def test_runtime_backend_failure_never_runs_unrestricted(tmp_path, nested):
    policy = ExecutionPermissions(tmp_path / "missing-backend", tmp_path, '{"type":"read-only"}')
    target = tmp_path / "must-not-exist"
    arguments = {"cmd": f"touch {shlex.quote(str(target))}", "login": False}
    # The compiler now loads policy at startup. A missing backend is fatal
    # before model sampling, rather than a recoverable command observation.
    with pytest.raises(ValueError, match="sandbox helper failed to start"):
        asyncio.run(_run(tmp_path, policy, "exec_command", arguments, nested))
    assert not target.exists()


@pytest.mark.parametrize("nested", [False, True])
def test_backend_failure_after_admission_is_a_tool_error(tmp_path, monkeypatch, nested):
    from corki.execution import backend

    policy = ExecutionPermissions(_engine(), tmp_path, '{"type":"read-only"}')
    run_owned = backend.run_owned

    async def command_failure(argv, payload, **kwargs):
        if "exec_policy" in json.loads(payload):
            raise ValueError("sandbox compiler became unavailable after admission")
        return await run_owned(argv, payload, **kwargs)

    monkeypatch.setattr(backend, "run_owned", command_failure)
    target = tmp_path / "must-not-exist"
    arguments = {"cmd": f"touch {shlex.quote(str(target))}", "login": False}
    results = asyncio.run(_run(tmp_path, policy, "exec_command", arguments, nested))
    assert not target.exists()
    assert any("became unavailable after admission" in result.content for result in results)


@pytest.mark.parametrize(
    "profile",
    [
        '{"type":"unrecognized"}',
        '{"type":"workspace-write","network_access":false,"network_access":true}',
        '{"type":"read-only","network_access":"false"}',
        '{"type":"read-only","type":"read-only"}',
    ],
)
def test_native_rejects_invalid_policy_before_any_side_effect(tmp_path, profile):
    policy = ExecutionPermissions(_engine(), tmp_path, profile)
    target = tmp_path / "must-not-exist"
    requests = []

    async def scenario():
        class Model:
            async def stream(self, request):
                requests.append(request)
                call = ToolCall(
                    new_tool_call_id(),
                    "exec_command",
                    {
                        "cmd": f"touch {shlex.quote(str(target))}",
                        "login": False,
                    },
                )
                yield ModelCompleted(
                    (ToolCallItem(call, request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, execution_permissions=policy
            ),
            database_path=tmp_path / "sessions.db",
            home_path=tmp_path / "home",
            model=Model(),
        )
        try:
            # AGENTS discovery now validates the policy during startup, before
            # the model can select a tool, rather than producing a tool error.
            with pytest.raises(ValueError, match="sandbox policy rejected"):
                _ = [event async for event in runtime.stream("test permissions")]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
    assert not target.exists()
    assert not requests


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("network", [False, True])
def test_runtime_network_policy_is_os_enforced(tmp_path, nested, network):
    policy = ExecutionPermissions(
        _engine(),
        tmp_path,
        json.dumps(
            {
                "type": "read-only",
                "network_access": network,
            }
        ),
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        port = listener.getsockname()[1]
        script = f"""
import socket
try:
    socket.create_connection(('127.0.0.1', {port}), timeout=1).close()
except OSError:
    print('NETWORK_DENIED')
else:
    print('NETWORK_CONNECTED')
"""
        args = {
            "cmd": shlex.join([sys.executable, "-I", "-c", script]),
            "login": False,
            "yield_time_ms": 1000,
        }
        results = asyncio.run(_run(tmp_path, policy, "exec_command", args, nested))
    expected = "NETWORK_CONNECTED" if network else "NETWORK_DENIED"
    assert any(expected in result.content for result in results), results


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("denied", [False, True])
def test_runtime_image_read_respects_deny_and_symlink_alias(tmp_path, nested, denied):
    from PIL import Image

    target = tmp_path / "private.png"
    Image.new("RGB", (2, 2)).save(target)
    alias = tmp_path / "alias.png"
    alias.symlink_to(target)
    entries = [{"access": "read", "path": {"type": "special", "value": {"kind": "root"}}}]
    if denied:
        entries.append({"access": "deny", "path": {"type": "path", "path": str(target)}})
    policy = ExecutionPermissions(
        _engine(),
        tmp_path,
        json.dumps(
            {
                "type": "managed",
                "file_system": {"type": "restricted", "entries": entries},
                "network": "restricted",
            }
        ),
    )
    results = asyncio.run(_run(tmp_path, policy, "view_image", {"path": str(alias)}, nested))
    assert results
    if not nested:
        assert results[-1].is_error is denied, results
    if denied:
        assert any("sandbox" in result.content.lower() for result in results), results


async def _run(tmp_path, policy, name, arguments, nested):
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            turn, step = request.items[-1].turn_id, new_step_id()
            if len(requests) == 1:
                call = (
                    ToolCall(new_tool_call_id(), name, arguments)
                    if not nested
                    else ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        raw_arguments="try { text(await tools."
                        + name
                        + "("
                        + json.dumps(arguments)
                        + ")); } catch(e) { text(String(e)); }",
                        input_kind="freeform",
                    )
                )
                item = ToolCallItem(call, turn, step)
            else:
                item = AssistantMessageItem("handled", turn, step)
            yield ModelCompleted((item,))

        async def aclose(self):
            pass

    runtime = await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode="code_mode_only" if nested else "direct",
            execution_permissions=policy,
        ),
        database_path=tmp_path / "sessions.db",
        model=Model(),
    )
    try:
        events = [event async for event in runtime.stream("test permissions")]
        assert isinstance(events[-1], TurnCompleted), events[-1]
        assert len(requests) == 2
        return [item for item in requests[-1].items if isinstance(item, ToolResultItem)]
    finally:
        await runtime.aclose()
