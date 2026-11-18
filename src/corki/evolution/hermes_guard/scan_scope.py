# ruff: noqa
# Adapted from NousResearch/hermes-agent 67e4d22d83b2 (MIT).
from pathlib import Path
from typing import Optional
from collections import Counter
from dataclasses import replace
import hashlib
import re
from .skills_guard import _determine_verdict


def _finding_keys(skill_dir: Path, findings) -> list[tuple[str, str, str, str]]:
    """(file, pattern_id, severity, sha256 of the full source line) per finding. Not the line number
    (an edit above a finding moves it) and not ``f.match``, which the scanner truncates on long lines,
    so a kept prefix with a new tail would match. Severity is part of the key because the scanner
    derives it from context (an unchanged line under a renamed denylist owner escalates), so an
    escalated finding counts as introduced. Findings without a source line key on their match."""
    lines: dict[str, list[bytes]] = {}
    keys = []
    for f in findings:
        if f.line > 0 and f.file not in lines:
            try:
                lines[f.file] = re.split(rb"\r\n|\r|\n", (skill_dir / f.file).read_bytes())
            except OSError:
                lines[f.file] = []
        src = lines.get(f.file, [])
        text = (
            src[f.line - 1].strip()
            if 0 < f.line <= len(src)
            else f.match.encode("utf-8", "replace")
        )
        keys.append((f.file, f.pattern_id, f.severity, hashlib.sha256(text).hexdigest()))
    return keys


def _same_file_key(path: Path) -> Optional[tuple[int, int]]:
    try:
        st = path.stat()
    except OSError:  # missing, dangling or looping (ELOOP): cannot share the target's inode
        return None
    return st.st_dev, st.st_ino


def _aliases(path: Path, target: Path, real: Path, key: Optional[tuple[int, int]]) -> bool:
    if path == target:
        return False
    try:
        if path.resolve() == real:
            return True
    except (
        OSError
    ):  # a loop's RuntimeError (3.11/3.12) reaches _scan_baseline's try and fails closed
        return False
    return key is not None and _same_file_key(path) == key


def _scan_scope(skill_dir: Path, target: Path) -> list[Path]:
    """Files a background-origin write's two scans cover: *target* plus every in-skill path that is the
    same file: a symlink resolving to it (scanned under its own name's rules) or a hardlink sharing its
    inode. The normal atomic replace gives the target a new inode, so a hardlinked sibling keeps the old
    bytes and cancels against the baseline, but the EXDEV/EBUSY copy fallback and the Windows in-place
    rewrite write through the existing inode and change the sibling too. Every other file reads the same
    before and after the write, so its findings would cancel; scanning it only cost time. Structural
    checks still cover the whole dir."""
    real = target.resolve()  # the atomic write lands here when *target* is an in-skill symlink
    key = _same_file_key(target)  # None for a new target: nothing can be hardlinked to it yet
    scope = [target] + [f for f in skill_dir.rglob("*") if _aliases(f, target, real, key)]
    if (
        target.is_symlink() and not real.exists()
    ):  # dangling: the write creates *real* under its own name
        scope.append(skill_dir / real.relative_to(skill_dir.resolve()))
    return scope


def _introduced_findings(skill_dir: Path, result, baseline: Counter):
    remaining = Counter(baseline)
    new = []
    for f, key in zip(result.findings, _finding_keys(skill_dir, result.findings)):
        if remaining[key] > 0:
            remaining[key] -= 1
        else:
            new.append(f)
    return replace(result, findings=new, verdict=_determine_verdict(new))
