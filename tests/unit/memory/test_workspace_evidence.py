import base64
import json
import os

import pytest

from corki.memory import workspace
from corki.memory.artifacts import (
    ensure_memory_layout,
    write_baseline,
    write_consolidated_artifacts,
)
from corki.memory.models import ConsolidatedMemory


def entry(value, mode="100644"):
    return {"content": base64.b64encode(value).decode(), "mode": mode}


def test_sorted_added_modified_deleted_and_newline_evidence():
    before = {"delete.md": entry(b"gone\n"), "edit.md": entry(b"old"), "keep.md": entry(b"same")}
    after = {"add.md": entry(b"new\n"), "edit.md": entry(b"updated"), "keep.md": entry(b"same")}
    rendered = workspace.render_diff(before, after)
    assert "- A add.md\n- D delete.md\n- M edit.md\n" in rendered
    assert "-gone\n" in rendered and "-old\n" in rendered and "+updated\n" in rendered
    assert "\\ No newline at end of file" in rendered
    assert "keep.md" not in rendered
    assert workspace.text(before, "delete.md") == "gone\n"


@pytest.mark.skipif(os.name == "nt", reason="Unix executable mode")
def test_executable_mode_change_is_evidence_and_changes_digest(tmp_path):
    path = tmp_path / "script"
    path.write_bytes(b"same\n")
    path.chmod(0o600)
    before = workspace.capture(tmp_path)
    path.chmod(0o700)
    after = workspace.capture(tmp_path)
    assert workspace.digest(before, outputs=False) != workspace.digest(after, outputs=False)
    assert "old mode 100644\nnew mode 100755" in workspace.render_diff(before, after)


def test_diff_body_cap_preserves_status_and_unicode_boundary():
    rendered = workspace.render_diff(
        {}, {"unicode.md": entry(("界" * 100).encode())}, max_bytes=101
    )
    assert "- A unicode.md" in rendered
    assert "[workspace diff truncated at 101 bytes]" in rendered
    body = rendered.split("```diff\n", 1)[1].split("\n[workspace diff", 1)[0]
    assert len(body.encode()) <= 101 and "\ufffd" not in body


def test_lossy_non_utf8_diff_retains_change_and_byte_snapshot():
    snapshot = {"bytes": entry(b"\xff\x00")}
    assert workspace.validate(snapshot) == snapshot
    rendered = workspace.render_diff({}, snapshot)
    assert "- A bytes" in rendered and "\ufffd\x00" in rendered


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"../x": entry(b"x")},
        {"/x": entry(b"x")},
        {"a": None},
        {"a": {"mode": [], "content": ""}},
        {"a": {"mode": "100644", "content": "!!"}},
        {"a": {"mode": "100644", "content": 1}},
    ],
)
def test_invalid_snapshot_has_no_invented_previous_evidence(value):
    assert workspace.validate(value) is None


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_baseline_explicitly_lacks_prior_content(tmp_path, version):
    (tmp_path / ".consolidation-baseline.json").write_text(json.dumps({"version": version}))
    assert workspace.read_previous(tmp_path) is None
    text = workspace.render_diff(workspace.read_previous(tmp_path), {"now": entry(b"current")})
    assert "prior deletions cannot be reconstructed" in text and "- D " not in text


def test_private_and_generated_files_are_excluded_without_mutation(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("user-owned")
    (tmp_path / "phase2_workspace_diff.md").write_text("stale evidence")
    (tmp_path / "actual.md").write_text("source")
    assert list(workspace.capture(tmp_path)) == ["actual.md"]
    assert (tmp_path / ".git/HEAD").read_text() == "user-owned"
    assert (tmp_path / "phase2_workspace_diff.md").read_text() == "stale evidence"


def test_extensions_have_instructions_and_non_markdown_resources():
    sources = json.loads(
        workspace.extension_sources(
            {
                "extensions/team/instructions.md": entry(b"reference only"),
                "extensions/team/data.txt": entry(b"ordinary input"),
                "extensions/other/data.md": entry(b"no guide"),
                "extensions/ad_hoc/instructions.md": entry(b"explicit updates"),
                "extensions/ad_hoc/notes/note.md": entry(b"user note"),
            }
        )
    )
    assert sources["team"] == {
        "instructions": "reference only",
        "resources": {"data.txt": "ordinary input"},
    }
    assert sources["other"]["instructions"] == "[instructions.md missing]"
    assert sources["ad_hoc"] == {"instructions": "explicit updates", "resources": {}}


def test_reset_keeps_only_sampled_inputs_and_current_outputs(tmp_path):
    ensure_memory_layout(tmp_path)
    note = tmp_path / "extensions/ad_hoc/notes/old.md"
    note.write_text("deleted input")
    write_consolidated_artifacts(tmp_path, ConsolidatedMemory("old fact", "old index", ()))
    original = workspace.capture(tmp_path)
    write_baseline(tmp_path, workspace.digest(original, outputs=False), sampled=original)
    note.unlink()
    sampled = workspace.capture(tmp_path)
    late = note.with_name("late.md")
    late.write_text("late input")
    write_consolidated_artifacts(tmp_path, ConsolidatedMemory("new fact", "new index", ()))
    write_baseline(tmp_path, workspace.digest(sampled, outputs=False), sampled=sampled)
    baseline = workspace.read_previous(tmp_path)
    assert "extensions/ad_hoc/notes/old.md" not in baseline
    assert "extensions/ad_hoc/notes/late.md" not in baseline
    assert "old fact" not in workspace.text(baseline, "MEMORY.md")
    assert "- A extensions/ad_hoc/notes/late.md" in workspace.render_diff(
        baseline, workspace.capture(tmp_path)
    )
    assert not list(tmp_path.glob("*.tmp"))
