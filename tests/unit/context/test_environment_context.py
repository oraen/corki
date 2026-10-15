import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from corki.config import CorkiSettings
from corki.context import ContextBuilder
from corki.context.environment import EnvironmentSnapshot, decode_snapshot
from corki.context.world_state import changed_context_items, render_context_history
from corki.prompting import PromptStore
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem, ContextRole


def clock(monkeypatch, day=8, offset=0):
    value = datetime(2026, 9, day, tzinfo=timezone(timedelta(hours=offset)))
    monkeypatch.setattr("corki.context.local_time.timezone_name", lambda: str(value.tzinfo))
    monkeypatch.setattr(
        "corki.context.builder.datetime",
        SimpleNamespace(now=lambda: SimpleNamespace(astimezone=lambda: value)),
    )


def build(path, shell="zsh"):
    snapshot = asyncio.run(ContextBuilder(shell_name=shell).build(cwd=path, turn_id=new_turn_id()))
    return next(item for item in snapshot.items if item.key == "environment.primary")


@pytest.mark.parametrize("field", ["date", "timezone", "cwd", "shell"])
def test_environment_changes_emit_typed_delta(tmp_path, monkeypatch, field):
    clock(monkeypatch)
    previous = build(tmp_path)
    path = tmp_path
    if field == "date":
        clock(monkeypatch, day=9)
    elif field == "timezone":
        clock(monkeypatch, offset=8)
    elif field == "cwd":
        path = tmp_path / "nested"
        path.mkdir()
    current = build(path, "bash" if field == "shell" else "zsh")
    updates = changed_context_items((previous,), (current,), current.turn_id)
    assert len(updates) == 1
    update = updates[0]
    assert update.content.startswith("<environment_context>\n")
    assert update.snapshot_content == current.content
    assert update.snapshot_state == current.snapshot_state is not None
    root = ElementTree.fromstring(update.content)
    assert [node.tag for node in root] == (
        ["current_date", "timezone"]
        if field in {"date", "timezone"}
        else ["cwd", "shell", "current_date", "timezone"]
    )
    assert changed_context_items((previous, update), (current,), current.turn_id) == ()
    assert render_context_history((previous, update)) == (previous, update)


def test_builder_escapes_all_xml_metacharacters(tmp_path, monkeypatch):
    clock(monkeypatch)
    path = tmp_path / "a&<b>\"'"
    path.mkdir()
    item = build(path, "a&<b>\"'")
    assert "a&amp;&lt;b&gt;&quot;&apos;" in item.content
    root = ElementTree.fromstring(item.content)
    assert root.findtext("cwd") == str(path)
    assert root.findtext("shell") == "a&<b>\"'"


@pytest.mark.parametrize("state", [None, "bad json", '{"version":99}', '{"version":true}'])
def test_legacy_or_unknown_state_gets_full_context_without_revocation(tmp_path, monkeypatch, state):
    clock(monkeypatch)
    current = build(tmp_path)
    old = replace(
        current, content="<environment_context>old</environment_context>", snapshot_state=state
    )
    (update,) = changed_context_items((old,), (current,), current.turn_id)
    assert update.content == current.content
    assert update.snapshot_content == current.content


def test_environment_config_gate_is_default_on_and_loads_from_toml(tmp_path):
    assert CorkiSettings(working_directory=tmp_path).include_environment_context is True
    config = tmp_path / "config.toml"
    config.write_text("[agent]\ninclude_environment_context=false\n")
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.include_environment_context is False


@pytest.mark.parametrize("value", [0, 1, "false", None, []])
def test_environment_gate_rejects_non_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="include_environment_context"):
        CorkiSettings(working_directory=tmp_path, include_environment_context=value)


def environment_item(shell):
    state = EnvironmentSnapshot("/repo", shell, "2026-09-08", "UTC")
    contribution = state.contribution()
    return ContextItem(
        contribution.key,
        ContextRole.USER,
        PromptStore().render(contribution.template_name, **contribution.variables),
        new_turn_id(),
        snapshot_state=contribution.snapshot_state,
    )


@pytest.mark.parametrize("before,after", [(None, "zsh"), ("zsh", None)])
def test_unknown_shell_transition_advances_silent_snapshot(before, after):
    old, current = environment_item(before), environment_item(after)
    (update,) = changed_context_items((old,), (current,), current.turn_id)
    assert update.is_snapshot_only
    assert update.snapshot_content == current.content
    assert decode_snapshot(update.snapshot_state).shell == after
    assert render_context_history((old, update)) == (old,)
    assert changed_context_items((old, update), (current,), current.turn_id) == ()
    changed = environment_item("bash")
    (next_update,) = changed_context_items((old, update), (changed,), changed.turn_id)
    assert next_update.is_snapshot_only == (after is None)


@pytest.mark.parametrize(
    "state",
    [
        "null",
        "[]",
        "1",
        '{"version":1}',
        '{"version":1,"environment":[]}',
        '{"version":1,"environment":{"cwd":null}}',
        '{"version":1,"environment":{"cwd":1}}',
        '{"version":1,"environment":{"cwd":"/repo","shell":false}}',
        '{"version":1,"environment":{"cwd":"/repo","current_date":[]}}',
        '{"version":1,"environment":{"cwd":"/repo","timezone":{}}}',
    ],
)
def test_invalid_snapshot_is_unknown_not_prompt_authority(state):
    assert decode_snapshot(state) is None
    current = environment_item("zsh")
    previous = replace(current, snapshot_state=state)
    (update,) = changed_context_items((previous,), (current,), current.turn_id)
    assert update.content == current.content


def test_optional_fields_absent_and_snapshot_round_trip():
    state = EnvironmentSnapshot("/a&b", None, None, None)
    assert decode_snapshot(state.encode()) == state
    assert decode_snapshot(json.dumps({"version": 1, "environment": {"cwd": "/a&b"}})) == state
    assert state.body(include_environment=True) == "  <cwd>/a&amp;b</cwd>\n"


def test_section_disable_is_silent_and_reenable_is_full():
    original = environment_item("zsh")
    (removed,) = changed_context_items((original,), (), original.turn_id)
    assert removed.is_snapshot_only
    assert removed.snapshot_content == ""
    assert render_context_history((original, removed)) == (original,)
    assert changed_context_items((original, removed), (), original.turn_id) == ()
    (returned,) = changed_context_items((original, removed), (original,), original.turn_id)
    assert returned.content == original.content
    assert changed_context_items((original, removed, returned), (original,), original.turn_id) == ()


def test_disabled_builder_does_not_read_clock(tmp_path, monkeypatch):
    def fail():
        raise AssertionError("disabled environment must not read time")

    monkeypatch.setattr("corki.context.builder.datetime", SimpleNamespace(now=fail))
    result = asyncio.run(
        ContextBuilder(include_environment_context=False).build(cwd=tmp_path, turn_id=new_turn_id())
    )
    assert "environment.primary" not in {item.key for item in result.items}


@pytest.mark.parametrize("field", ["filesystem", "network"])
def test_effective_authority_change_at_same_cwd_emits_delta(field):
    old = environment_item("zsh")
    state = decode_snapshot(old.snapshot_state)
    fragment = f"<{field} />"
    current_state = replace(state, **{field: fragment})
    current = replace(
        old,
        content=PromptStore().render(
            "context/environment_fragment", body=current_state.body(include_environment=True)
        ),
        snapshot_state=current_state.encode(),
    )
    (update,) = changed_context_items((old,), (current,), current.turn_id)
    root = ElementTree.fromstring(update.content)
    assert root.find("cwd") is None
    assert root.find(field) is not None
    assert update.snapshot_content == current.content
    assert decode_snapshot(update.snapshot_state) == current_state
    assert changed_context_items((old, update), (current,), current.turn_id) == ()
    (removed,) = changed_context_items((old, update), (old,), old.turn_id)
    assert ElementTree.fromstring(removed.content).find(field) is None
    assert decode_snapshot(removed.snapshot_state) == state


def test_old_four_field_snapshot_refreshes_once_without_rewriting_history():
    current = environment_item("zsh")
    value = json.loads(current.snapshot_state)
    value["version"] = 1
    value["environment"].pop("filesystem")
    value["environment"].pop("network")
    old = replace(current, snapshot_state=json.dumps(value))
    (update,) = changed_context_items((old,), (current,), current.turn_id)
    assert update.content == current.content
    assert render_context_history((old, update)) == (old, update)
    assert changed_context_items((old, update), (current,), current.turn_id) == ()
