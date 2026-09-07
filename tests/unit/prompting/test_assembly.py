"""Tests for feature-independent prompt contribution assembly."""

from pathlib import Path

import pytest

from corki.prompting import (
    DuplicatePromptContributionError,
    PromptAssembler,
    PromptContribution,
    PromptRole,
    PromptSlot,
    PromptStore,
)


def _write_template(root: Path, name: str, text: str) -> None:
    path = root / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_orders_future_feature_slots_without_assembler_branches(tmp_path: Path) -> None:
    _write_template(tmp_path, "base", "base")
    _write_template(tmp_path, "permissions", "permissions")
    _write_template(tmp_path, "plugin", "plugin #{plugin_name}")
    _write_template(tmp_path, "realtime", "realtime")
    assembler = PromptAssembler(PromptStore(root=tmp_path))

    assembly = assembler.assemble(
        base_template="base",
        contributions=[
            PromptContribution(
                key="extension.plugin.demo",
                template_name="plugin",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.EXTENSIONS,
                variables={"plugin_name": "demo"},
            ),
            PromptContribution(
                key="permissions.current",
                template_name="permissions",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.PERMISSIONS,
            ),
            PromptContribution(
                key="realtime.current",
                template_name="realtime",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.REALTIME,
                separate_message=True,
            ),
        ],
    )

    assert assembly.instructions == "base"
    assert [fragment.key for fragment in assembly.fragments] == [
        "realtime.current",
        "permissions.current",
        "extension.plugin.demo",
    ]
    assert assembly.fragments[0].separate_message is True
    assert assembly.fragments[-1].content == "plugin demo"


def test_rejects_duplicate_stable_contribution_keys(tmp_path: Path) -> None:
    _write_template(tmp_path, "base", "base")
    _write_template(tmp_path, "fragment", "fragment")
    contribution = PromptContribution(
        key="mode.current",
        template_name="fragment",
        role=PromptRole.DEVELOPER,
        slot=PromptSlot.COLLABORATION_MODE,
    )
    assembler = PromptAssembler(PromptStore(root=tmp_path))

    with pytest.raises(DuplicatePromptContributionError):
        assembler.assemble(
            base_template="base",
            contributions=[contribution, contribution],
        )


def test_silent_snapshot_has_no_template_or_model_text(tmp_path):
    _write_template(tmp_path, "base", "base")
    contribution = PromptContribution(
        "state", None, PromptRole.DEVELOPER, PromptSlot.EXTENSIONS, snapshot_state="hidden"
    )
    result = PromptAssembler(PromptStore(root=tmp_path)).assemble(
        base_template="base", contributions=(contribution,)
    )
    assert result.fragments[0].content == ""
    assert result.fragments[0].snapshot_state == "hidden"
    with pytest.raises(ValueError, match="silent contribution requires"):
        PromptContribution("state", None, PromptRole.DEVELOPER, PromptSlot.EXTENSIONS)
    with pytest.raises(ValueError, match="input-attached history"):
        PromptContribution(
            "state",
            None,
            PromptRole.DEVELOPER,
            PromptSlot.EXTENSIONS,
            input_scoped=True,
            snapshot_state="hidden",
        )
