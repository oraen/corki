import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _run_demo(name: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "examples" / name)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_core_loop_demo() -> None:
    result = _run_demo("core_loop_demo.py")

    assert result == {
        "cross_turn_memory": True,
        "model_steps": 5,
        "plan_recalled": True,
        "project_context_before_user": True,
        "status": "ok",
        "tool_calls": 4,
    }


def test_builtin_tools_demo() -> None:
    result = _run_demo("builtin_tools_demo.py")

    assert result == {
        "image_attachment_recalled": True,
        "long_process_resumed": True,
        "status": "ok",
        "tools": [
            "apply_patch",
            "exec_command",
            "update_plan",
            "view_image",
            "write_stdin",
        ],
    }


def test_extensions_demo() -> None:
    result = _run_demo("extensions_demo.py")
    assert result["model_continuation"] is True
    assert result["mcp_discovered"] is True
    assert "tool_search" in result["initial_tools"]
    assert "mcp__fixture::echo" not in result["initial_tools"]

    assert result["status"] == "ok"
    assert result["skill_recalled"] is True
    assert result["plugin_called"] is True
    assert result["mcp_called"] is True
    assert {"skill_read", "plugin__demo__echo", "mcp__fixture::echo"} <= set(
        result["advertised_tools"]
    )
