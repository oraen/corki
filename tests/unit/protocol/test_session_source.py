"""Native source wire shape, startup normalization and typed root authority."""

import pytest

from corki.protocol.ids import new_thread_id
from corki.protocol.session_source import (
    SessionSource,
    SessionSourceKind,
    SubAgentSource,
    ThreadSpawnSource,
)


@pytest.mark.parametrize(
    "label,stored",
    [
        (" CLI ", "cli"),
        ("\u2003VSCode\u00a0", "vscode"),
        ("app-server", "mcp"),
        ("APP_SERVER", "mcp"),
        ("AppServer", "mcp"),
        (" Atlas ", '{"custom":"atlas"}'),
        ("ÄBC", '{"custom":"Äbc"}'),
        ("\x1c", '{"custom":"\\u001c"}'),
        ("internal_guardian", '{"custom":"internal_guardian"}'),
        ("subagent_review", '{"custom":"subagent_review"}'),
    ],
)
def test_startup_labels_normalize_without_acquiring_internal_authority(label, stored):
    source = SessionSource.from_startup_arg(label)
    assert source.storage_value == stored
    assert SessionSource.from_storage(stored) == source
    assert not source.is_non_root_agent


@pytest.mark.parametrize("value", ["", " \n\t", "\u2003", None, 7])
def test_invalid_startup_labels_fail(value):
    with pytest.raises(ValueError):
        SessionSource.from_startup_arg(value)


@pytest.mark.parametrize("variant", ["review", "compact", "memory_consolidation"])
def test_subagent_unit_variants_round_trip(variant):
    source = SessionSource.subagent(SubAgentSource(variant))
    assert source.storage_value == '{"subagent":"' + variant + '"}'
    assert SessionSource.from_storage(source.storage_value) == source
    assert source.is_non_root_agent


def test_nested_spawn_and_other_round_trip_without_display_prefix_inference():
    source = SessionSource.subagent(
        SubAgentSource(
            "thread_spawn", ThreadSpawnSource(new_thread_id(), 2, "/root/worker", "name", "role")
        )
    )
    assert SessionSource.from_storage(source.storage_value) == source
    assert source.is_non_root_agent
    other = SessionSource.subagent(SubAgentSource("other", "review"))
    assert other.storage_value == '{"subagent":{"other":"review"}}'
    assert SessionSource.from_storage(other.storage_value) == other
    assert other != SessionSource.subagent(SubAgentSource("review"))


@pytest.mark.parametrize("variant", ["guardian", "memory_consolidation"])
def test_internal_sources_round_trip(variant):
    source = SessionSource.internal(variant)
    assert SessionSource.from_storage(source.storage_value) == source
    assert source.is_non_root_agent


@pytest.mark.parametrize(
    "stored",
    [
        "broken {",
        "atlas",
        "chatgpt",
        '{"custom":null}',
        '{"internal":"other"}',
        '{"subagent":{"thread_spawn":{}}}',
        "null",
        "3",
    ],
)
def test_unrecognized_stored_source_falls_back_to_unknown(stored):
    assert SessionSource.from_storage(stored) == SessionSource(SessionSourceKind.UNKNOWN)


def test_direct_custom_value_is_not_startup_normalized():
    source = SessionSource(SessionSourceKind.CUSTOM, " Atlas ")
    assert SessionSource.from_storage(source.storage_value) == source
    assert source.storage_value == '{"custom":" Atlas "}'
