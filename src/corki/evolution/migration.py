"""One-time relocation based on applied evolution evidence, never skill names alone."""

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
from contextlib import closing, suppress
from pathlib import Path

from corki.config import document
from corki.platforms.paths import is_link_path
from corki.skills.frontmatter import parse
from corki.skills.relocation import locations, move, recover
from corki.skills.walk import walk_skill_files
from corki.storage.file_lock import lock, open_lock, try_lock, unlock_and_close

_LOCK = threading.RLock()
_LOG = logging.getLogger(__name__)


def atomic(path, text, backup=True):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if is_link_path(path):
        raise ValueError("迁移记录不能是符号链接")
    if backup and path.exists():
        previous = path.read_bytes()
        saved = (
            path.parent
            / ".manager-backups"
            / (path.name + "." + hashlib.sha256(previous).hexdigest()[:16])
        )
        if not saved.exists():
            atomic(saved, previous.decode("utf-8"), backup=False)
    fd, temporary = tempfile.mkstemp(prefix=".skill-migrate-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class MigrationHost:
    """Only filesystem/configuration capabilities needed by the shared mover."""

    def __init__(self, home, workspace, compatibility_home):
        self.home = home.resolve()
        self.workspace = workspace.resolve()
        self.compatibility_home = (compatibility_home or Path.home()).resolve()
        self.config = self.home / "settings.json"

    def config_text(self):
        return self.config.read_text(encoding="utf-8") if self.config.exists() else "{}"

    def validate_config(self, text, **kwargs):
        document.loads(text)

    def write_config(self, text, expected, reason):
        if hashlib.sha256(self.config_text().encode()).hexdigest() != expected:
            raise ValueError("迁移期间配置发生变化，请重试")
        atomic(self.config, text)

    def files(self):
        mapping = locations(self)
        result = []
        for root, group in [
            (self.home / "skills", "corki"),
            (self.home / "skills/work", "work"),
            (self.home / "skills/self-improving", "evolution"),
            (self.home / "self-improving", "legacy-evolution"),
        ]:
            inventory = walk_skill_files(
                root, excluded_children=("work", "self-improving", ".system")
            )
            for path in inventory.files:
                if is_link_path(path):
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                try:
                    name, _, _ = parse(text, path.parent.name)
                except ValueError:
                    name = path.parent.name
                identity = mapping.get(str(path), hashlib.sha256(str(path).encode()).hexdigest())
                result.append(
                    dict(
                        id=identity,
                        kind="skills",
                        group=group,
                        name=name,
                        path=str(path),
                        revision=hashlib.sha256(text.encode()).hexdigest(),
                    )
                )
        return result


def _migrate_legacy_skills(home, workspace, compatibility_home=None):
    home = home.resolve()
    state = home / "evolution/evolution.sqlite"
    marker = home / "evolution/self-improving-v2.json"

    def saved():
        legacy = home / "evolution/self-improving-v1.json"
        current = json.loads(marker.read_text()) if marker.exists() else {}
        if current.get("complete"):
            return current
        old = json.loads(legacy.read_text()) if legacy.exists() else {}
        current["paths"] = {**old.get("paths", {}), **current.get("paths", {})}
        return current

    previous = saved()
    paths = previous.get("paths", {})
    legacy_root = home / "self-improving"
    if previous.get("complete") or (not state.is_file() and not legacy_root.is_dir()):
        return paths
    state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _LOCK:
        previous = saved()
        paths = previous.get("paths", {})
        if previous.get("complete"):
            return paths
        lock_path = state.parent / "self-improving-migration.lock"
        owner = open_lock(lock_path)
        acquired = False
        review, review_held = None, False
        try:
            lock(owner)
            acquired = True
            previous = saved()
            paths = previous.get("paths", {})
            if previous.get("complete"):
                return paths
            review = open_lock(state.parent / "review.lock")
            review_held = try_lock(review)
            if not review_held:
                return paths
            names = set()
            if state.is_file():
                with closing(sqlite3.connect(state.as_uri() + "?mode=ro", uri=True)) as db:
                    exists = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='mutations'"
                    ).fetchone()
                    if exists:
                        columns = {r[1] for r in db.execute("PRAGMA table_info(mutations)")}
                        historical = " AND policy_version<2" if "policy_version" in columns else ""
                        names = {
                            r[0]
                            for r in db.execute(
                                "SELECT DISTINCT skill FROM mutations WHERE status='applied'"
                                + historical
                            )
                        }
            host = MigrationHost(home, workspace, compatibility_home)
            recover(host, atomic)
            errors = False
            for item in host.files():
                if item["group"] != "legacy-evolution" and (
                    item["group"] not in {"corki", "work"} or item["name"] not in names
                ):
                    continue
                directory = Path(item["path"]).parent
                path = state.parent / "locks"
                path.mkdir(exist_ok=True, mode=0o700)
                filename = hashlib.sha256(str(directory).encode()).hexdigest() + ".lock"
                skill_lock = open_lock(path / filename)
                held = False
                try:
                    held = try_lock(skill_lock)
                    if not held:
                        errors = True
                        continue
                    move(
                        host,
                        dict(
                            id=item["id"],
                            group="evolution",
                            skill_revision=item["revision"],
                            revision=hashlib.sha256(host.config_text().encode()).hexdigest(),
                        ),
                        atomic,
                    )
                    destination = home / "skills/self-improving" / directory.name / "SKILL.md"
                    for source_path, previous_target in list(paths.items()):
                        if previous_target == item["path"]:
                            paths[source_path] = str(destination)
                    paths[item["path"]] = str(destination)
                except (OSError, ValueError):
                    errors = True
                    _LOG.warning("A legacy evolution skill could not be relocated; source retained")
                finally:
                    if held:
                        unlock_and_close(skill_lock)
                    else:
                        skill_lock.close()
            if not errors and legacy_root.is_dir():
                with suppress(OSError):
                    legacy_root.rmdir()  # Never remove unrelated files left in this directory.
            atomic(
                marker,
                json.dumps({"version": 2, "complete": not errors, "paths": paths}),
                backup=False,
            )
            return paths
        finally:
            if review is not None:
                if review_held:
                    unlock_and_close(review)
                else:
                    review.close()
            if acquired:
                unlock_and_close(owner)
            else:
                owner.close()


def migrate_legacy_skills(home, workspace, compatibility_home=None):
    try:
        paths = _migrate_legacy_skills(home, workspace, compatibility_home) or {}
        return {
            Path(source): Path(target)
            for source, target in paths.items()
            if (
                Path(source).is_relative_to(home.resolve() / "skills")
                or Path(source).is_relative_to(home.resolve() / "self-improving")
            )
            and Path(target).is_relative_to(home.resolve() / "skills/self-improving")
        }
    except (OSError, ValueError, TypeError, sqlite3.Error):
        _LOG.warning("Legacy evolution migration deferred; existing skills remain available")
        return {}
