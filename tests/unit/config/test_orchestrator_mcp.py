"""Strict host configuration; durable model selections cannot grant MCP access."""

from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core.model_settings import bind_model_settings, capture_model_settings


@pytest.mark.parametrize(
    "document,expected",
    [
        ("", True),
        ("[orchestrator]\n", True),
        ("[orchestrator.mcp]\n", True),
        ("[orchestrator.mcp]\nenabled=true\n", True),
        ("[orchestrator.mcp]\nenabled=false\n", False),
    ],
)
def test_load_model_access_gate(tmp_path, document, expected):
    config = tmp_path / "config.toml"
    config.write_text(document)
    assert (
        CorkiSettings.for_directory(tmp_path, config_file=config).orchestrator_mcp_enabled
        is expected
    )


@pytest.mark.parametrize(
    "document",
    [
        "orchestrator=false",
        'orchestrator="false"',
        "orchestrator=[]",
        "[orchestrator]\nmcp=false",
        '[orchestrator]\nmcp="false"',
        '[orchestrator.mcp]\nenabled="false"',
        "[orchestrator.mcp]\nenabled=1",
        "[orchestrator.mcp]\nenabled=0",
        "[orchestrator.mcp]\nenabled=[]",
    ],
)
def test_reject_malformed_host_gate(tmp_path, document):
    config = tmp_path / "config.toml"
    config.write_text(document)
    with pytest.raises(ValueError, match="orchestrator"):
        CorkiSettings.for_directory(tmp_path, config_file=config)


@pytest.mark.parametrize("value", [None, 0, 1, "false", [], {}])
def test_direct_host_gate_rejects_non_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="orchestrator.mcp.enabled"):
        CorkiSettings(tmp_path, orchestrator_mcp_enabled=value)


def test_saved_model_selection_cannot_restore_host_mcp_access(tmp_path):
    previous = CorkiSettings(tmp_path, orchestrator_mcp_enabled=True)
    saved = capture_model_settings(previous)
    current = replace(previous, orchestrator_mcp_enabled=False)
    assert bind_model_settings(current, saved).orchestrator_mcp_enabled is False
    assert "orchestrator_mcp_enabled" not in saved.to_payload()
