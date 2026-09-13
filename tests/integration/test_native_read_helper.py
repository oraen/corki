"""Real native startup, restricted reads, Runtime use and retained policy isolation."""

import asyncio
import base64
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image
from test_bundled_execution import compiler as compiler
from test_execution_permissions import _run
from test_filesystem_helper_runtime import workspace_policy
from test_instruction_discovery import run as instruction_runtime

from corki.config import CorkiSettings
from corki.config.instructions import ProjectInstructionsConfig
from corki.context.project_instructions import load_project_entries
from corki.execution import backend
from corki.execution.owned_process import run_owned


def image(path):
    Image.new("RGB", (2, 2), "blue").save(path)
    return path.read_bytes()


@pytest.mark.parametrize("nested", [False, True])
def test_workspace_only_runtime_reads_agents_and_image(tmp_path, compiler, nested):
    text = "Only write files in this workspace."
    (tmp_path / "AGENTS.md").write_text(text)
    image(tmp_path / "pixel.png")
    policy = workspace_policy(compiler, tmp_path)
    settings = CorkiSettings(
        tmp_path,
        skills_enabled=False,
        execution_permissions=policy,
        tool_mode="code_mode_only" if nested else "direct",
    )
    content, sources, warnings = asyncio.run(instruction_runtime(tmp_path, settings))
    assert text in content and sources == (tmp_path / "AGENTS.md",)
    assert not any("AGENTS" in warning for warning in warnings)
    results = asyncio.run(_run(tmp_path, policy, "view_image", {"path": "pixel.png"}, nested))
    assert results and not any(result.is_error for result in results), results
    if nested:
        assert "image_url" in results[-1].content
    else:
        assert results[-1].content_items


@pytest.mark.parametrize(
    "case",
    [
        "defaults",
        "override",
        "fallback",
        "no_root",
        "utf8",
        "whitespace",
        "zero",
        "untrusted",
        "symlink",
    ],
)
def test_native_discovery_matches_python_entries(tmp_path, compiler, case):
    root = tmp_path / "project"
    cwd = root / "nested"
    cwd.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "AGENTS.md").write_bytes(b"ROOT")
    (cwd / "AGENTS.md").write_bytes(b"CHILD")
    config = ProjectInstructionsConfig()
    if case == "override":
        (root / "AGENTS.override.md").write_text("")
    elif case == "fallback":
        (root / "AGENTS.md").rename(root / "RULES")
        config = replace(config, fallback_filenames=("", "RULES", "RULES"))
    elif case == "no_root":
        config = replace(config, root_markers=())
    elif case == "utf8":
        (cwd / "AGENTS.md").write_bytes("你好".encode() + b"\xff")
        config = replace(config, max_bytes=8)
    elif case == "whitespace":
        (root / "AGENTS.override.md").write_text("\x1c\x1d\x1e\x1f \t\n" * 20)
        config = replace(config, max_bytes=4)
    elif case == "zero":
        config = replace(config, max_bytes=0)
    elif case == "untrusted":
        config = replace(config, trust_level="untrusted")
    elif case == "symlink":
        (root / "AGENTS.override.md").symlink_to(cwd / "AGENTS.md")
    expected = load_project_entries(cwd, config)
    actual, warnings = asyncio.run(
        backend.read_project_instructions(workspace_policy(compiler, root), cwd, config)
    )
    assert actual == expected
    assert not warnings


def test_all_instruction_candidates_are_checked_before_read_budget(tmp_path, compiler):
    root = tmp_path / "project"
    cwd = root / "nested"
    cwd.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "AGENTS.md").write_text("fills the entire budget")
    (cwd / "not-directory").write_text("ordinary file")
    config = ProjectInstructionsConfig(max_bytes=1, fallback_filenames=("not-directory/RULES",))
    with pytest.raises(NotADirectoryError):
        load_project_entries(cwd, config)
    with pytest.raises(ValueError, match="Not a directory"):
        asyncio.run(
            backend.read_project_instructions(workspace_policy(compiler, root), cwd, config)
        )


@pytest.mark.parametrize("alias", [False, True])
def test_native_image_read_denies_outside_workspace(tmp_path, compiler, alias):
    root = tmp_path / "project"
    root.mkdir()
    path = tmp_path / "outside.png"
    image(path)
    if alias:
        (root / "alias.png").symlink_to(path)
        path = root / "alias.png"
    with pytest.raises(ValueError, match="sandbox filesystem operation failed"):
        asyncio.run(
            backend.file_operation(
                workspace_policy(compiler, root),
                root,
                "image",
                {"path": str(path), "max_bytes": 4096},
            )
        )


@pytest.mark.parametrize("kind", ["fifo", "directory", "too_large", "invalid"])
def test_native_image_read_is_bounded_and_regular(tmp_path, compiler, kind):
    path = tmp_path / "input"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"x" * (20 if kind == "too_large" else 2))
    pattern = {
        "fifo": "regular file",
        "directory": "regular file",
        "too_large": "byte limit",
        "invalid": "invalid or unsupported",
    }[kind]
    with pytest.raises(ValueError, match=pattern):
        asyncio.run(
            asyncio.wait_for(
                backend.file_operation(
                    workspace_policy(compiler, tmp_path),
                    tmp_path,
                    "image",
                    {"path": str(path), "max_bytes": 10},
                ),
                5,
            )
        )


def test_helper_permissions_do_not_authorize_model_command_or_change_snapshot(tmp_path, compiler):
    root = tmp_path / "project"
    root.mkdir()
    policy = workspace_policy(compiler, root)

    async def scenario():
        # Even malicious supplied argv is replaced with the fixed helper entry.
        helper = await backend._compile(
            policy,
            ["/bin/sh", "-c", "touch injected"],
            root,
            file_system_helper=True,
            native_file_system_helper=True,
        )
        normal = await backend._compile(policy, [str(compiler), "--corki-fs-helper"], root)
        assert (
            json.loads(helper.profile)
            == json.loads(normal.profile)
            == json.loads(policy.profile_json)
        )
        assert helper.terminal_snapshot == normal.terminal_snapshot
        payload = json.dumps(
            {
                "operation": "project_instructions",
                "arguments": {
                    "cwd": str(root),
                    "config": {
                        "max_bytes": 0,
                        "root_markers": [],
                        "fallback_filenames": [],
                        "trust_level": None,
                    },
                },
            }
        ).encode()
        assert json.loads(
            await run_owned(
                helper.command,
                payload,
                cwd=root,
                output_limit=4096,
                env=backend._native_helper_env(),
            )
        ) == {"ok": "[]"}
        with pytest.raises(ValueError, match="exited with status"):
            await run_owned(normal.command, payload, cwd=root, output_limit=4096)
        assert not (root / "injected").exists()

    asyncio.run(scenario())


def test_native_helper_uses_only_allowlisted_environment(tmp_path, compiler, monkeypatch):
    monkeypatch.setenv("CORKI_FS_TEST_SECRET", "must-not-reach-helper")
    data = image(tmp_path / "image.png")
    calls = []
    original = backend.run_owned

    async def capture(argv, payload, **kwargs):
        if "--corki-fs-helper" in argv:
            calls.append(kwargs["env"])
        return await original(argv, payload, **kwargs)

    monkeypatch.setattr(backend, "run_owned", capture)
    encoded = asyncio.run(
        backend.file_operation(
            workspace_policy(compiler, tmp_path),
            tmp_path,
            "image",
            {"path": str(tmp_path / "image.png"), "max_bytes": 4096},
        )
    )
    assert base64.b64decode(encoded) == data
    assert len(calls) == 1 and "CORKI_FS_TEST_SECRET" not in calls[0]
    assert set(calls[0]) <= {"PATH", "TMPDIR", "TMP", "TEMP", "__CF_USER_TEXT_ENCODING"}


def test_old_compiler_rejects_native_helper_contract(tmp_path):
    old = os.environ.get("CORKI_TEST_PRE_NATIVE_FS_COMPILER")
    if not old:
        pytest.skip("requires actual pre-native-fs compiler")
    with pytest.raises(ValueError, match="unknown field `native_file_system_helper`"):
        asyncio.run(
            backend.read_project_instructions(
                workspace_policy(Path(old), tmp_path), tmp_path, ProjectInstructionsConfig()
            )
        )
