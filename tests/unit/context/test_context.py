import asyncio
from pathlib import Path

from corki.context import ContextBuilder, active_history, truncate_text
from corki.context.instructions import load_project_instructions
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id


def test_context_loads_hierarchical_agents_and_environment(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='sample'\n")
    (tmp_path / "AGENTS.md").write_text("root instruction")
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("nested instruction")

    snapshot = asyncio.run(ContextBuilder().build(cwd=nested, turn_id=new_turn_id()))

    combined = "\n".join(item.content for item in snapshot.items)
    assert "root instruction" in combined
    assert "nested instruction" in combined
    assert str(nested) in combined


def test_active_history_is_continuous_and_never_skips_middle_turn() -> None:
    first = new_turn_id()
    second = new_turn_id()
    third = new_turn_id()
    items = (
        UserMessageItem("old question", first),
        AssistantMessageItem("old answer", first, new_step_id()),
        UserMessageItem("middle question", second),
        AssistantMessageItem("middle answer", second, new_step_id()),
        UserMessageItem("new question", third),
        AssistantMessageItem("new answer", third, new_step_id()),
    )

    selected = active_history(items)

    assert selected == items
    assert len(truncate_text("x" * 100, 30)) <= 30


def test_project_instructions_use_root_to_cwd_total_byte_budget(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"root")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "AGENTS.md").write_bytes(b"abcdef")

    loaded = load_project_instructions(tmp_path, nested, byte_budget=7)

    assert "root" in loaded
    assert "abc" in loaded
    assert "def" not in loaded


def test_project_instructions_decode_invalid_utf8_lossily(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"project\xff doc")

    loaded = load_project_instructions(tmp_path, tmp_path)

    assert "project\ufffd doc" in loaded


def test_project_instruction_override_wins_in_the_same_directory(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("base")
    (tmp_path / "AGENTS.override.md").write_text("override")

    loaded = load_project_instructions(tmp_path, tmp_path)

    assert "override" in loaded
    assert "base" not in loaded
