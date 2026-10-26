"""A cheap live opt-in check, independent of model/provider configuration reloads."""

from pathlib import Path
from threading import RLock

from corki.config.document import loads
from corki.config.skills import disabled_skill_paths, parse_skill_rules


class EvolutionSwitch:
    def __init__(self, home: Path, fallback: bool = False):
        self.path = home / "settings.json"
        self.fallback = fallback
        self._signature = object()
        self._enabled = False
        self._rules = ()
        self._lock = RLock()

    def enabled(self) -> bool:
        with self._lock:
            try:
                stat = self.path.stat()
                signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
                if signature != self._signature:
                    value = loads(self.path.read_text(encoding="utf-8"))
                    flag = value.get("features", {}).get("self_evolution", False)
                    skills = value.get("skills", {})
                    self._enabled = flag is True and skills.get("enabled", True) is not False
                    self._rules = parse_skill_rules(skills.get("config", []), self.path.parent)
                    self._signature = signature
                return self._enabled
            except FileNotFoundError:
                return self.fallback
            except (OSError, ValueError, TypeError, AttributeError):
                # A malformed/temporarily unavailable opt-in is never consent.
                self._signature = object()
                return False

    def skill_enabled(self, name, path):
        # Individual skill exclusions still apply during writes. The evolution
        # opt-in itself is captured at turn admission, not reapplied mid-review.
        self.enabled()
        with self._lock:
            return path.resolve() not in disabled_skill_paths(self._rules, ((name, path),))
