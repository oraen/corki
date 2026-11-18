"""Hermes background skill scans: block newly introduced findings, not legacy examples."""

from collections import Counter

from .hermes_guard.scan_scope import _finding_keys, _introduced_findings, _scan_scope
from .hermes_guard.skills_guard import format_scan_report, scan_skill, should_allow_install


class SkillScan:
    def __init__(self, directory, target, *, background, enabled):
        self.directory = directory
        self.scope = _scan_scope(directory, target) if background else None
        self.enabled = background or enabled
        self.baseline = None
        if background:
            result = scan_skill(directory, source="agent-created", files=self.scope)
            self.baseline = Counter(_finding_keys(directory, result.findings))

    def check(self):
        if not self.enabled:
            return
        result = scan_skill(self.directory, source="agent-created", files=self.scope)
        if self.baseline is not None:
            result = _introduced_findings(self.directory, result, self.baseline)
        allowed, _ = should_allow_install(result)
        if not allowed:
            raise ValueError("技能检查未通过：" + format_scan_report(result))
