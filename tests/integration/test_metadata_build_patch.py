"""Validate the exact additive native patch against the pinned reference export."""

import os
import runpy
import subprocess
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parents[2] / "native/sandbox"
BUILD = runpy.run_path(str(BRIDGE / "build.py"))
PATHS = ("codex-rs/protocol/src/permissions.rs", "codex-rs/linux-sandbox/src/bwrap.rs")


@pytest.fixture
def exported(tmp_path):
    checkout = Path(
        os.environ.get("CORKI_TEST_CODEX_CHECKOUT", "/Users/corki/IdeaProjects/ad/codex")
    )
    if not (checkout / ".git").exists():
        pytest.skip("requires the local pinned reference checkout")
    for name in PATHS:
        content = subprocess.check_output(
            ["git", "-C", str(checkout), "show", BUILD["COMMIT"] + ":" + name]
        )
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return tmp_path


def test_patch_adds_product_name_without_replacing_existing_metadata(exported):
    patch = BRIDGE / "patches/workspace-metadata.patch"
    before = {name: (exported / name).read_text() for name in PATHS}
    BUILD["apply_workspace_metadata"](exported, patch)
    for name in PATHS:
        content = (exported / name).read_text()
        assert '".corki"' in content
        for preserved in ('".codex"', '".git"', '".agents"'):
            assert content.count(preserved) == before[name].count(preserved)
    # The same patch cannot be applied twice or silently treated as already done.
    with pytest.raises(subprocess.CalledProcessError):
        BUILD["apply_workspace_metadata"](exported, patch)


@pytest.mark.parametrize("drift", ["source", "missing-patch"])
def test_missing_or_drifted_patch_stops_build_without_partial_mutation(exported, drift):
    patch = BRIDGE / "patches/workspace-metadata.patch"
    if drift == "source":
        target = exported / PATHS[1]
        target.write_text(target.read_text().replace('Path::new(".codex")', 'Path::new("changed")'))
    else:
        patch = exported / "missing.patch"
    before = {name: (exported / name).read_bytes() for name in PATHS}
    with pytest.raises(subprocess.CalledProcessError):
        BUILD["apply_workspace_metadata"](exported, patch)
    assert {name: (exported / name).read_bytes() for name in PATHS} == before
