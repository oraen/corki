"""Fixture inspection of staged files, not evidence preloaded for a model.

Legacy retention/ownership fixtures use this to assert the sampled source set.
Actual on-demand model tool reads are tested separately in the child Runtime suites.
"""

import json
import re
from pathlib import Path

from corki.memory import workspace


def inspect_worker_evidence(request):
    """Read the live test worker's sampled files without fabricating a request item."""
    assert not any(getattr(i, "key", None) == "memory.consolidation.inputs" for i in request.items)
    environment = next(i for i in request.items if getattr(i, "key", None) == "environment.primary")
    root = Path(re.search(r"<cwd>(.*?)</cwd>", environment.content, re.S)[1])
    sampled = workspace.capture(root)
    return {
        "workspace_diff": (root / "phase2_workspace_diff.md").read_text(),
        "extension_sources": workspace.extension_sources(sampled),
        "previous_memory": workspace.text(sampled, "MEMORY.md"),
        "previous_summary": workspace.text(sampled, "memory_summary.md"),
        "previous_skills": json.dumps(
            {n: workspace.text(sampled, n) for n in sampled if n.startswith("skills/")}
        ),
        "raw_memories": workspace.text(sampled, "raw_memories.md"),
        "ad_hoc_notes": "\n\n".join(
            f"## {n}\n{workspace.text(sampled, n)}"
            for n in sorted(sampled)
            if n.startswith("extensions/ad_hoc/notes/") and n.endswith(".md")
        ),
    }
