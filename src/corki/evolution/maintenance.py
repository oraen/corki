"""Hermes curator cadence, provenance-based inactivity transitions and recoverable archives."""

import json
import logging
import os
import re
import shutil
import sqlite3
import tarfile
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from corki.platforms.paths import is_link_path
from corki.skills.io import check_skill_io, publish_skill_io
from corki.storage.file_lock import open_lock, try_lock, unlock_and_close

from .state import EvolutionState, now

_LOG = logging.getLogger(__name__)

TRANSIENT = {
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


class Curator:
    def __init__(self, home, service, cwd, document=None):
        self.home, self.service, self.cwd = Path(home).resolve(), service, cwd
        self.state = EvolutionState(self.home)
        self.options = (document or {}).get("curator", {})
        if not isinstance(self.options, dict):
            self.options = {}

    def number(self, key, default):
        try:
            cast = float if key == "min_idle_hours" else int
            value = cast(self.options.get(key, default))
            return value if value >= (0 if key == "min_idle_hours" else 1) else default
        except (TypeError, ValueError):
            return default

    def due(self, timestamp=None):
        timestamp = timestamp or datetime.now(UTC)
        if self.options.get("enabled", True) is False or self.state.maintenance_value(
            "paused", False
        ):
            return False
        last = self.state.maintenance_value("last_run_at")
        if last is None:
            # Hermes idle_schedule.interval_due: seed first sight, never prune at first launch.
            self.state.save_maintenance("last_run_at", timestamp.isoformat())
            return False
        return timestamp - datetime.fromisoformat(last) >= timedelta(
            hours=self.number("interval_hours", 168)
        )

    def next_delay(self):
        if self.options.get("enabled", True) is False or self.state.maintenance_value(
            "paused", False
        ):
            return None
        last = self.state.maintenance_value("last_run_at")
        until_due = (
            0
            if last is None
            else (
                datetime.fromisoformat(last)
                + timedelta(hours=self.number("interval_hours", 168))
                - datetime.now(UTC)
            ).total_seconds()
        )
        return max(self.number("min_idle_hours", 2) * 3600, until_due)

    def candidates(self, *, include_bundled=False):
        snapshot = self.service.snapshot(self.cwd, force_reload=True)
        by_path = {str(s.path.parent.resolve()): s for s in snapshot.skills}
        if not self.state.maintenance_value("legacy_usage_seeded", False):
            # Migrate actual recorded background creations, never infer provenance from a name.
            with self.state.database() as db:
                legacy = list(
                    db.execute(
                        "SELECT skill,min(created_at) AS created FROM mutations "
                        "WHERE policy_version<2 AND status='applied' AND origin='review' "
                        "AND file='SKILL.md' AND before_text IS NULL GROUP BY skill"
                    )
                )
                for row in legacy:
                    skill = snapshot.resolve(row["skill"])
                    if skill is not None:
                        db.execute(
                            "INSERT OR IGNORE INTO skill_usage(path,name,created_by,created_at) "
                            "VALUES (?,?,'agent',?)",
                            (
                                str(skill.path.parent.resolve()),
                                skill.qualified_name,
                                row["created"],
                            ),
                        )
            self.state.save_maintenance("legacy_usage_seeded", True)
        prune_bundled = include_bundled and self.options.get("prune_builtins", False) is True
        if prune_bundled:
            with self.state.database() as db:
                for path, skill in by_path.items():
                    if skill.scope.value == "system":
                        db.execute(
                            "INSERT OR IGNORE INTO skill_usage(path,name,created_by,created_at) "
                            "VALUES (?,?,'bundled',?)",
                            (path, skill.qualified_name, now()),
                        )
        return [
            {**row, "skill": by_path[row["path"]]}
            for row in self.state.usage()
            if row["path"] in by_path
            and (
                row["created_by"] == "agent"
                or (prune_bundled and by_path[row["path"]].scope.value == "system")
            )
            and not row["pinned"]
            and not row["locked"]
            and by_path[row["path"]].scope.value in {"user", "system"}
            and by_path[row["path"]].plugin_id is None
            and Path(row["path"]).is_relative_to(self.home / "skills")
            and snapshot.is_enabled(by_path[row["path"]])
        ]

    @staticmethod
    def reference_pattern(skill):
        # Corki's scheduler uses explicit prompt mentions, not Hermes' skills[] job field.
        names = {skill.name, skill.qualified_name}
        paths = {str(skill.path), str(skill.discovery_path or skill.path)}
        return re.compile(
            r"\[\$(?:"
            + "|".join(re.escape(n) for n in names)
            + r")\]\s*\((?:skill://)?(?:"
            + "|".join(re.escape(p) for p in paths)
            + r")\)|\$(?:"
            + "|".join(re.escape(n) for n in names)
            + r")(?![A-Za-z0-9_.:&-])"
        )

    def cron_references(self):
        path = self.home / "cron/cron.sqlite"
        if not path.exists():
            return set()
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            references = set()
            for row in db.execute("SELECT definition FROM jobs"):
                prompt = json.loads(row[0]).get("prompt", "")
                references.update(
                    s.qualified_name
                    for s in self.service.snapshot(self.cwd).skills
                    if self.reference_pattern(s).search(prompt)
                )
            return references

    def rewrite_cron_references(self, source, target):
        """Hermes curator._rewrite_cron_refs: migrate explicit bindings, not arbitrary prose."""
        path = self.home / "cron/cron.sqlite"
        if not path.exists():
            return 0
        pattern = self.reference_pattern(source)
        replacement = f"[${target.qualified_name}]({target.path})"
        count = 0
        with closing(sqlite3.connect(path, timeout=15)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for identity, raw in db.execute("SELECT id,definition FROM jobs").fetchall():
                definition = json.loads(raw)
                prompt = definition.get("prompt", "")
                updated = pattern.sub(lambda _: replacement, prompt)
                if updated != prompt:
                    definition["prompt"] = updated
                    db.execute(
                        "UPDATE jobs SET definition=?,version=version+1,updated=? WHERE id=?",
                        (json.dumps(definition, ensure_ascii=False), time.time(), identity),
                    )
                    count += 1
        return count

    @staticmethod
    def move(source, target):
        # Hermes curator._archive_skill_directory also handles separate mounted volumes.
        import errno

        try:
            os.rename(source, target)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            shutil.move(str(source), str(target))

    def archive(self, path, name, *, external=False, absorbed_into=None):
        check_skill_io()
        path = Path(path)
        if (
            is_link_path(path)
            or (not external and not path.is_relative_to(self.home / "skills"))
            or path
            in {
                self.home / "skills",
                self.home / "skills/work",
                self.home / "skills/self-improving",
            }
        ):
            raise ValueError("不能归档技能根目录之外或链接指向的目录")
        if not (path / "SKILL.md").is_file():
            raise ValueError("归档目标不是技能目录")
        snapshot = self.service.snapshot(self.cwd)
        source = snapshot.resolve(name)
        target = snapshot.resolve(absorbed_into) if absorbed_into else None
        identity = str(uuid.uuid4())
        destination = self.home / "skills/.archive" / identity / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.move(path, destination)
        try:
            with self.state.database() as db:
                db.execute(
                    "INSERT INTO skill_archives VALUES (?,?,?,?,?)",
                    (identity, str(path), str(destination), name, now()),
                )
                db.execute("UPDATE skill_usage SET state='archived' WHERE path=?", (str(path),))
        except Exception:
            self.move(destination, path)
            raise
        if source is not None and target is not None:
            try:
                updated = self.rewrite_cron_references(source, target)
                self.state.save_maintenance(
                    "cron_rewrites",
                    {"from": name, "to": target.qualified_name, "jobs_updated": updated},
                )
            except (OSError, sqlite3.Error, ValueError):
                # As in Hermes, a cron-store failure is visible but doesn't undo the merge.
                _LOG.warning("Curator cron reference migration failed", exc_info=True)
        return identity

    def restore(self, identity):
        with self.state.database() as db:
            row = db.execute("SELECT * FROM skill_archives WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise ValueError("归档不存在")
            target, archive = Path(row["path"]), Path(row["archive"])
            if target.exists():
                raise ValueError("原位置已有技能，不会覆盖")
            target.parent.mkdir(parents=True, exist_ok=True)
            self.move(archive, target)
            try:
                db.execute("DELETE FROM skill_archives WHERE id=?", (identity,))
                db.execute(
                    "UPDATE skill_usage SET state='active',last_activity_at=? WHERE path=?",
                    (now(), str(target)),
                )
            except Exception:
                self.move(target, archive)
                raise
        return str(target)

    def snapshot(self):
        options = self.options.get("backup", {})
        options = options if isinstance(options, dict) else {}
        if options.get("enabled", True) is False:
            return None
        root = self.home / "evolution/backups"
        root.mkdir(parents=True, exist_ok=True)
        target = root / (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".tar.gz"
        )
        temporary = target.with_suffix(".tar.gz.tmp")
        try:
            self._snapshot_into(temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        self.prune_backups()
        return str(target)

    def _snapshot_into(self, target):
        with tarfile.open(target, "w:gz") as archive:
            source = self.home / "skills"
            if source.exists():
                for directory, children, files in os.walk(source, followlinks=False):
                    check_skill_io()
                    children[:] = [
                        name
                        for name in children
                        if name not in TRANSIENT | {".archive", ".hub", ".curator_backups"}
                        and not is_link_path(Path(directory) / name)
                    ]
                    for name in files:
                        file = Path(directory) / name
                        if not file.is_file() or is_link_path(file):
                            continue
                        archive.add(
                            file,
                            arcname=str(file.relative_to(self.home / "skills")),
                            recursive=False,
                        )
            cron = self.home / "cron/cron.sqlite"
            if cron.exists():
                try:
                    with closing(sqlite3.connect(cron.as_uri() + "?mode=ro", uri=True)) as db:
                        definitions = list(db.execute("SELECT id,definition,version FROM jobs"))
                    import io

                    data = json.dumps(definitions, ensure_ascii=False).encode()
                    info = tarfile.TarInfo("cron-jobs.json")
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
                except (OSError, sqlite3.Error):
                    _LOG.warning("Curator cron configuration backup unavailable", exc_info=True)
        with target.open("rb") as saved:
            os.fsync(saved.fileno())

    def prune_backups(self):
        options = self.options.get("backup", {})
        options = options if isinstance(options, dict) else {}
        try:
            keep = max(1, int(options.get("keep", 2)))
        except (ValueError, TypeError):
            keep = 2
        for old in sorted((self.home / "evolution/backups").glob("*.tar.gz"), reverse=True)[keep:]:
            old.unlink()

    def run(self, *, dry_run=False, force=False, timestamp=None):
        timestamp = timestamp or datetime.now(UTC)
        if not force and not self.due(timestamp):
            return None
        self.state.root.mkdir(parents=True, exist_ok=True)
        owner = open_lock(self.state.root / "curator.lock")
        if not try_lock(owner):
            owner.close()
            return None
        try:
            if not force and not self.due(timestamp):
                return None
            candidates = self.candidates(include_bundled=True)
            protected = self.cron_references()
            counts = dict(checked=len(candidates), marked_stale=0, archived=0, reactivated=0)
            if not dry_run:
                try:
                    if self.options.get("consolidate", False):
                        self.snapshot()
                    else:
                        self.prune_backups()
                except OSError:
                    _LOG.warning(
                        "Curator backup unavailable; continuing maintenance", exc_info=True
                    )
                self.state.save_maintenance("last_run_at", timestamp.isoformat())
            for row in candidates:
                check_skill_io()
                if row["name"] in protected or Path(row["path"]).name in protected:
                    continue
                anchor = datetime.fromisoformat(row["last_activity_at"] or row["created_at"])
                age = timestamp - anchor
                desired = (
                    "archived"
                    if age >= timedelta(days=self.number("archive_after_days", 30))
                    else "stale"
                    if age >= timedelta(days=self.number("stale_after_days", 14))
                    else "active"
                )
                if desired == row["state"]:
                    continue
                if desired == "archived":
                    if not dry_run:
                        try:
                            publish_skill_io(lambda row=row: self.archive(row["path"], row["name"]))
                        except (OSError, ValueError):
                            _LOG.warning("Skill archive failed; source retained", exc_info=True)
                            continue
                    counts["archived"] += 1
                else:
                    counts["marked_stale" if desired == "stale" else "reactivated"] += 1
                    if not dry_run:
                        with self.state.database() as db:
                            db.execute(
                                "UPDATE skill_usage SET state=? WHERE path=?",
                                (desired, row["path"]),
                            )
            if not dry_run:
                self.state.save_maintenance("last_report", {"at": timestamp.isoformat(), **counts})
            return counts
        finally:
            unlock_and_close(owner)


async def consolidate(curator, settings, switch, *, model=None, requirements=None):
    """Hermes' opt-in LLM consolidation: a separate Skills-only agent, not the live task."""
    from dataclasses import replace
    from types import SimpleNamespace

    from corki.models.factory import create_model
    from corki.prompting import PromptStore
    from corki.tools.registry import ToolRegistry

    from .policy import ReviewPolicy
    from .tools import WriteFence
    from .worker import review

    candidates = curator.candidates()
    if not candidates:
        return None
    auxiliary = switch.document.get("auxiliary", {})
    task = auxiliary.get("curator", {}) if isinstance(auxiliary, dict) else {}
    if isinstance(task, dict) and task.get("provider") and task.get("model"):
        settings = settings.for_model(task["provider"], task["model"])
    owned = model is None
    model = model or create_model(settings)
    identity = curator.state.begin_review("curator", str(uuid.uuid4()), settings)
    owner = SimpleNamespace(
        home=curator.home,
        cwd=curator.cwd,
        service=curator.service,
        switch=switch,
        requirements=requirements,
        registry=ToolRegistry(),
    )
    prompt = PromptStore().render("extensions/evolution/curator") + "\n\nCandidates:\n"
    prompt += "\n".join(
        json.dumps({k: v for k, v in row.items() if k != "skill"}, ensure_ascii=False)
        for row in candidates
    )
    job = SimpleNamespace(
        prefix=None,
        snapshot=None,
        policy=ReviewPolicy(),
        curator=True,
        candidates={row["path"] for row in candidates},
        prompt=prompt,
        usage=None,
    )
    status = "failed"
    try:
        result = await review(
            owner,
            identity=identity,
            settings=replace(settings, collaboration_mode="default"),
            model=model,
            repository=None,
            thread="curator",
            fence=WriteFence(switch, enabled=True),
            job=job,
        )
        status = "completed"
        return result
    finally:
        usage = job.usage
        curator.state.finish_review(
            identity,
            status,
            usage.input_tokens if usage else 0,
            usage.output_tokens if usage else 0,
        )
        if owned:
            await model.aclose()
