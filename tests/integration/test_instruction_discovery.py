"""Project discovery configuration, byte accounting and diagnostics in model requests."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.config.instructions import ProjectInstructionsConfig
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.items import AssistantMessageItem, ContextItem, new_step_id


async def run(tmp_path, settings):
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            yield ModelCompleted(
                (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
            )

        async def aclose(self):
            pass

    runtime = LangGraphRuntime.create(
        settings=settings,
        model=Model(),
        home_path=tmp_path / "home",
        database_path=tmp_path / "state.db",
    )
    try:
        events = [e async for e in runtime.stream("go")]
        assert isinstance(events[-1], TurnCompleted), events
        sources = await runtime.instruction_sources()
        agents = [
            i for i in requests[0].items if isinstance(i, ContextItem) and i.key == "project.agents"
        ]
        assert len(agents) == 1
        return (
            agents[0].content,
            sources,
            [e.message for e in events if isinstance(e, WarningEvent)],
        )
    finally:
        await runtime.aclose()


def setup(tmp_path):
    root, cwd, home = tmp_path / "project", tmp_path / "project/nested", tmp_path / "home"
    cwd.mkdir(parents=True)
    home.mkdir()
    (root / ".git").mkdir()
    (home / "AGENTS.md").write_text("GLOBAL RULE")
    return root, cwd, home


@pytest.mark.parametrize("excluded", ["zero_budget", "untrusted"])
def test_global_instructions_do_not_spend_project_budget_or_inherit_project_trust(
    tmp_path, excluded
):
    root, cwd, home = setup(tmp_path)
    (root / "AGENTS.md").write_text("PROJECT RULE")
    config = (
        ProjectInstructionsConfig(max_bytes=0)
        if excluded == "zero_budget"
        else ProjectInstructionsConfig(trust_level="untrusted")
    )
    text, sources, warnings = asyncio.run(
        run(tmp_path, CorkiSettings(cwd, skills_enabled=False, project_instructions=config))
    )
    assert "GLOBAL RULE" in text and "PROJECT RULE" not in text
    assert sources == (home / "AGENTS.md",)
    assert "instructions for " not in text
    assert not any("AGENTS.md" in w for w in warnings)


def test_whitespace_project_override_masks_default_without_spending_byte_budget(tmp_path):
    root, cwd, home = setup(tmp_path)
    (root / "AGENTS.override.md").write_text(" " * 20)
    (root / "AGENTS.md").write_text("MASKED RULE")
    (cwd / "AGENTS.md").write_text("NEXT RULE")
    config = ProjectInstructionsConfig(max_bytes=4)
    text, sources, _ = asyncio.run(
        run(tmp_path, CorkiSettings(cwd, skills_enabled=False, project_instructions=config))
    )
    assert "MASKED RULE" not in text
    assert "NEXT" in text
    assert sources == (home / "AGENTS.md", cwd / "AGENTS.md")


def test_all_candidates_are_discovered_before_budgeted_reads(tmp_path, monkeypatch):
    root, cwd, home = setup(tmp_path)
    (root / "AGENTS.md").write_text("ROOT RULE")
    original_stat = Path.stat

    def denied(path, *args, **kwargs):
        if path == cwd / "AGENTS.override.md":
            raise PermissionError("fixture candidate metadata denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    config = ProjectInstructionsConfig(max_bytes=4)
    text, sources, warnings = asyncio.run(
        run(tmp_path, CorkiSettings(cwd, skills_enabled=False, project_instructions=config))
    )
    # Unrestricted discovery failure drops project entries, but retains global.
    assert "ROOT" not in text
    assert sources == (home / "AGENTS.md",)
    assert any("fixture candidate metadata denied" in w for w in warnings)


@pytest.mark.parametrize("markers", ['[".custom"]', "[]"])
def test_toml_marker_fallback_and_raw_utf8_budget_reach_runtime(tmp_path, markers):
    root, cwd, home = setup(tmp_path)
    (root / ".custom").touch()
    (root / "RULES.md").write_text("AB")
    (cwd / "RULES.md").write_text("你好")
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f"project_root_markers={markers}\n"
        'project_doc_fallback_filenames=["", "RULES.md", "RULES.md"]\n'
        "project_doc_max_bytes=6\n"
    )
    settings = replace(
        CorkiSettings.for_directory(cwd, config_file=config_file), skills_enabled=False
    )
    text, sources, _ = asyncio.run(run(tmp_path, settings))
    if markers == "[]":
        assert "AB" not in text and "你好" in text
        assert sources == (home / "AGENTS.md", cwd / "RULES.md")
    else:
        assert "AB\n\n你�" in text and "好" not in text
        assert sources == (home / "AGENTS.md", root / "RULES.md", cwd / "RULES.md")


def test_global_read_failure_warns_and_falls_back_without_project_failure(tmp_path, monkeypatch):
    root, cwd, home = setup(tmp_path)
    (home / "AGENTS.override.md").write_text("UNREADABLE")
    (root / "AGENTS.md").write_text("PROJECT RULE")
    original_read = Path.read_bytes

    def denied(path):
        if path == home / "AGENTS.override.md":
            raise PermissionError("fixture global read denied")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", denied)
    text, sources, warnings = asyncio.run(run(tmp_path, CorkiSettings(cwd, skills_enabled=False)))
    assert "GLOBAL RULE\n\n--- project-doc ---\n\nPROJECT RULE" in text
    assert sources == (home / "AGENTS.md", root / "AGENTS.md")
    assert sum("fixture global read denied" in w for w in warnings) == 1
