import asyncio
import io
from functools import partial
from pathlib import Path

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from corki.cli import terminal
from corki.cli.backtrack import prompt_input
from corki.cli.command_completion import CommandCompleter
from corki.cli.draft_history import DraftEntry
from corki.cli.inline_images import ImageDraft
from corki.cli.input_owner import DraftText, ImageInput, QueuedInput, input_image_kwargs
from corki.cli.reference_completion import FileSearchUnavailable, catalog, files
from corki.config import CorkiSettings
from corki.plugins.models import PluginManifest
from corki.protocol.input_mentions import InputMention
from corki.protocol.items import UserMessageItem
from corki.protocol.tools import ImageAttachment
from corki.skills.mentions import select
from corki.skills.models import SkillMetadata, SkillScope, SkillSnapshot


@pytest.fixture(autouse=True)
def legacy_search_backend(monkeypatch):
    # These cases cover the rg fallback; native behavior has dedicated integration/PTY cases.
    from corki.cli import native_file_search

    monkeypatch.setattr(native_file_search, "executable", lambda: None)


def skill(path, **kwargs):
    return SkillMetadata("same", "Local skill", path, path.parent, SkillScope.USER, **kwargs)


def test_bound_reference_survives_paste_image_queue_and_undo():
    selector = InputMention("same", "/b/SKILL.md", "skill")
    draft = ImageDraft()
    draft.clear("$same")
    draft.bind(0, "$same", selector)
    draft.paste("x" * 1001, 0, 0)
    draft.attach(ImageAttachment("data:image/png;base64,a"), 0, 0)
    saved = DraftEntry.capture(draft)
    assert saved.bindings[0][0] == 10 + len(saved.pastes[0][1])
    text, images, positions = draft.expanded()
    message = ImageInput(text, images, positions, saved, draft.mentions)
    kwargs = input_image_kwargs(QueuedInput(message))
    assert kwargs["mentions"] == (selector,)
    snapshot = SkillSnapshot((skill(Path("/a/SKILL.md")), skill(Path("/b/SKILL.md"))))
    assert select(text, snapshot, mentions=kwargs["mentions"]) == (snapshot.skills[1],)
    restored = ImageDraft()
    restored.append(saved.text, saved.images, saved.positions, saved.pastes, saved.bindings)
    start, label, _ = restored.bindings[0]
    restored.sync(restored.text[:start] + restored.text[start + 1 :], start)
    assert restored.mentions == () and "$same" not in restored.text
    restored.undo()
    assert restored.mentions == (selector,)
    assert restored.bindings == saved.bindings


def test_typed_mention_is_not_a_binding_and_overlaps_rejected():
    draft = ImageDraft()
    draft.clear("$same")
    assert input_image_kwargs(DraftText(draft.text, DraftEntry.capture(draft))) == {}
    draft.bind(0, "$same", InputMention("same", "/b/SKILL.md", "skill"))
    with pytest.raises(ValueError, match="overlap"):
        draft.bind(0, "$same", InputMention("same", "/a/SKILL.md", "skill"))


def test_durable_prompt_reference_restores_bound_editable_draft():
    selector = InputMention("same", "/b/SKILL.md", "skill")
    value = prompt_input(UserMessageItem("use $same please", "turn", mentions=(selector,)))
    assert input_image_kwargs(value) == {"mentions": (selector,)}
    ui = terminal.TerminalUI.__new__(terminal.TerminalUI)
    ui._inline_images = ImageDraft()
    ui._draft = ""
    ui.restore_queued_inputs((value,))
    assert ui._inline_images.bindings == ((4, "$same", selector),)
    assert ui._inline_images.text == "use $same please"


def test_catalog_hides_plugin_owned_skills_only_in_unified_mode():
    plugin = PluginManifest("local", None, None, Path("/plugins/local"), plugin_id="local@user")
    owned = skill(Path("/p/SKILL.md"), plugin_id=plugin.identity)
    assert [row.label for row in catalog((owned,), (plugin,), "@")] == ["@local"]
    assert [row.label for row in catalog((owned,), (plugin,), "$")] == ["$same"]
    assert catalog((), (plugin,), "@")[0].selector.path == "plugin://local@user"


def test_file_search_is_local_and_skips_ignored_and_control_names(tmp_path):
    (tmp_path / "中文 name.txt").touch()
    (tmp_path / "bad\nfile").touch()
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").touch()
    assert [r.label for r in asyncio.run(files(tmp_path))] == ["中文 name.txt"]


def test_directory_candidates_include_nested_parents_once(tmp_path):
    directory = tmp_path / "文档 guides" / "nested"
    directory.mkdir(parents=True)
    (directory / "one.md").touch()
    (directory / "two.md").touch()
    rows = asyncio.run(files(tmp_path))
    assert [(row.label, row.description) for row in rows if row.description == "Directory"] == [
        ("文档 guides", "Directory"),
        ("文档 guides/nested", "Directory"),
    ]
    assert all(row.selector is None for row in rows)

    async def complete():
        completer = CommandCompleter(tmp_path)
        return [
            item
            async for item in completer.get_completions_async(Document("@文档", 3), CompleteEvent())
        ]

    candidates = asyncio.run(complete())
    assert any(item.text == '"文档 guides"' for item in candidates)


def test_followed_directory_link_preserves_reference_spelling_and_ignores(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    (project / ".gitignore").write_text("ignored-link/\n", encoding="utf-8")
    external = tmp_path / "外部资料"
    external.mkdir()
    (external / "说明.md").write_text("reference", encoding="utf-8")
    (project / "文档 link").symlink_to(external, target_is_directory=True)
    (project / "ignored-link").symlink_to(external, target_is_directory=True)
    rows = asyncio.run(files(project))
    assert ("文档 link/说明.md", "File") in [(r.label, r.description) for r in rows]
    assert ("文档 link", "Directory") in [(r.label, r.description) for r in rows]
    assert not any("ignored-link" in row.label for row in rows)
    assert not any(str(external) in row.label for row in rows)


def test_cyclic_directory_link_fails_without_hanging_or_partial_candidates(tmp_path):
    (tmp_path / "keep.md").touch()
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(FileSearchUnavailable, match="failed or exceeded"):
        asyncio.run(files(tmp_path))


def test_real_large_project_search_returns_late_match_not_index_overflow(tmp_path):
    for index in range(10001):
        (tmp_path / f"ordinary-{index:05}.txt").touch()
    (tmp_path / "zz-中文-target.md").touch()
    rows = asyncio.run(files(tmp_path, "中文-target"))
    assert [(r.label, r.description) for r in rows] == [("zz-中文-target.md", "File")]


def test_candidate_search_keeps_same_named_skills_bound_by_path():
    async def run():
        completer = CommandCompleter()

        async def load():
            return (skill(Path("/a/SKILL.md")), skill(Path("/b/SKILL.md"))), ()

        completer.references = load
        rows = [c async for c in completer.get_completions_async(Document("$sam"), CompleteEvent())]
        assert len(rows) == 2
        assert rows[1].reference.selector.path == "/b/SKILL.md"

    asyncio.run(run())


def test_real_composer_select_does_not_submit_and_history_restores_binding(tmp_path, monkeypatch):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )

        async def load():
            return (skill(tmp_path / "SKILL.md"),), ()

        ui.set_reference_loader(load)

        async def wait(predicate):
            while not predicate():
                await asyncio.sleep(0.002)

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(6):
                    await wait(lambda: ui._session.app.is_running)
                    pipe.send_text("$sa")
                    await wait(lambda: ui._session.default_buffer.complete_state is not None)
                    await asyncio.sleep(0.15)  # explicit selection, not pasted newline
                    pipe.send_text("\r")
                    await wait(lambda: bool(ui._inline_images.bindings))
                    assert not task.done()
                    assert ui._session.default_buffer.text == "$same "
                    pipe.send_text("\r")
                    value = await task
                    assert input_image_kwargs(value)["mentions"] == (
                        InputMention("same", str(tmp_path / "SKILL.md"), "skill"),
                    )
                    task = asyncio.create_task(ui.read_message())
                    await wait(lambda: ui._session.app.is_running)
                    pipe.send_text("\x1b[A")
                    await wait(lambda: bool(ui._inline_images.bindings))
                    pipe.send_text("\r")
                    assert input_image_kwargs(await task) == input_image_kwargs(value)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
