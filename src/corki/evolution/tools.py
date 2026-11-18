"""Skill maintenance, following Hermes skill_manager_tool/skill_manager_guards."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import stat
import threading
import uuid
from contextlib import suppress
from contextvars import ContextVar
from pathlib import Path, PureWindowsPath

import yaml

from corki.evolution.state import EvolutionState
from corki.protocol.tools import ToolResult, ToolSpec
from corki.skills.frontmatter import extract, parse
from corki.skills.io import check_skill_io, publish_skill_io, run_skill_io
from corki.storage.file_lock import lock, open_lock, unlock_and_close

_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")
_MAX_BYTES = 1_048_576
_LOG = logging.getLogger(__name__)
_BATCH_LOCKS = ContextVar("skill_batch_locks", default=frozenset())
_BATCH_APPROVED = ContextVar("skill_batch_approved", default=False)


def _audit(function, *args, **kwargs):
    # Hermes skill_ledger/_record_success: telemetry must not veto a foreground edit.
    try:
        return function(*args, **kwargs)
    except (OSError, sqlite3.Error):
        _LOG.warning("Skill mutation telemetry unavailable", exc_info=True)
        return None


def revision(value: str | None):
    return hashlib.sha256(value.encode()).hexdigest() if value is not None else None


class WriteFence:
    def __init__(self, switch, *, enabled=None):
        self.switch = switch
        self.enabled = switch.enabled() if enabled is None else enabled
        self.revoked = threading.Event()

    def check(self):
        check_skill_io()
        if self.revoked.is_set() or not self.enabled:
            raise ValueError("自进化已关闭或本次学习已取消，未提交修改")

    def revoke(self):
        self.revoked.set()


class SkillManageTool:
    def __init__(
        self,
        home,
        service,
        cwd,
        switch,
        *,
        fence=None,
        review=None,
        on_write=None,
        approved=False,
        background_origin=False,
    ):
        self.home, self.service, self.cwd = home.resolve(), service, cwd
        self.root = self.home / "skills/self-improving"
        self.state = EvolutionState(self.home)
        self.fence = fence or WriteFence(switch)
        self.review = review
        self.on_write = on_write
        self.reads = {}
        self.approved = approved
        self.background_origin = background_origin

    @property
    def spec(self):
        from .batch import operation_schema

        return ToolSpec(
            name="skill_manage",
            description=(
                "Maintain existing skills in place, regardless of their origin. Create new "
                "skills in $CORKI_HOME/skills/self-improving. Requires self-evolution. "
                "List/read existing skills; create a skill with complete SKILL.md "
                "frontmatter (name and description); edit replaces SKILL.md, patch changes text, "
                "write_file/remove_file manage supporting files. Background reviews must freshly "
                "read existing files before changing them. New files need no prior read. "
                "An optional revision detects stale edits; history/restore support rollback. "
                "Prefer an operations array for atomic create/patch/supporting-file changes. "
                "Delete must be the sole operation. Never delete a skill during a turn review. "
                "User-locked skills cannot be changed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list",
                            "status",
                            "read",
                            "create",
                            "patch",
                            "edit",
                            "write_file",
                            "remove_file",
                            "delete",
                            "history",
                            "restore",
                        ],
                    },
                    "name": {
                        "type": "string",
                        "description": "Existing skill name or new lowercase-hyphen name",
                    },
                    "file": {
                        "type": "string",
                        "description": "Relative skill resource; default SKILL.md",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full UTF-8 content for create/write_file",
                    },
                    "revision": {
                        "type": "string",
                        "description": "Exact revision returned by read",
                    },
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                    "absorbed_into": {
                        "type": "string",
                        "description": "Existing umbrella skill receiving this skill's knowledge",
                    },
                    "version": {
                        "type": "string",
                        "description": "History edit ID whose prior content to restore",
                    },
                    "operations": {
                        "type": "array",
                        "description": "Atomic operations; all changes roll back on failure.",
                        "items": operation_schema(),
                        "minItems": 1,
                        "maxItems": 20,
                    },
                },
                "additionalProperties": False,
            },
            output_char_budget=None,
        )

    async def execute(self, call, context):
        try:
            value = await run_skill_io(self._execute, dict(call.arguments or {}))
            return ToolResult(call.id, call.name, json.dumps(value, ensure_ascii=False))
        except (OSError, ValueError, sqlite3.Error, yaml.YAMLError) as error:
            return ToolResult(call.id, call.name, str(error), is_error=True)

    def _snapshot(self):
        from dataclasses import replace

        snapshot = self.service.snapshot(self.cwd, force_reload=True)
        disabled = self.fence.switch.disabled_skills(
            tuple((s.qualified_name, s.path) for s in snapshot.skills)
        )
        return replace(snapshot, disabled_paths=snapshot.disabled_paths | disabled)

    def _editable(self, skill):
        # Hermes _locate_for_write has no ownership/source gate for content writes.
        return not self._locked(skill.path.parent)

    def _locked(self, directory, *, writing=False):
        try:
            return self.state.skill_locked(str(directory.resolve()))
        except (OSError, sqlite3.Error) as error:
            if writing and (self.review or self.background_origin):
                raise ValueError(
                    "无法确认用户锁定状态，后台暂不修改此技能；请检查维护记录"
                ) from error
            _LOG.warning("Skill lock metadata unavailable; foreground editing remains available")
            return False

    def _target(self, directory, file):
        relative = Path(file)
        if relative.is_absolute() or PureWindowsPath(file).is_absolute() or ".." in relative.parts:
            raise ValueError("技能资源路径不能越出技能目录")
        current = directory / relative
        if not current.resolve().is_relative_to(directory.resolve()):
            raise ValueError("技能资源越出目录")
        return current

    def _read(self, path):
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise ValueError("技能资源不是文本文件或内容过大，请使用精简的自进化技能")
        return path.read_text(encoding="utf-8-sig")

    def _execute(self, args):
        self.fence.check()
        if "operations" in args:
            from .batch import execute_batch

            return execute_batch(self, args)
        args = dict(args)
        for legacy, current in (
            ("old_string", "old_text"),
            ("new_string", "new_text"),
            ("file_path", "file"),
            ("file_content", "content"),
        ):
            if legacy in args and current not in args:
                args[current] = args[legacy]
        action = args.get("action")
        if action == "patch" and "content" in args:
            if "old_text" in args or "new_text" in args:
                raise ValueError("patch 只能选择全文替换或局部替换，不能同时使用")
            action = args["action"] = "edit"
        if action == "status":
            return self.state.status()
        snapshot = self._snapshot()
        if action == "list":
            return {
                "skills": [
                    {
                        "name": skill.qualified_name,
                        "description": skill.description,
                        "editable": self._editable(skill),
                    }
                    for skill in snapshot.skills
                    if snapshot.is_enabled(skill)
                ]
            }
        name, file = args.get("name"), args.get("file", "SKILL.md")
        if not isinstance(name, str) or not isinstance(file, str):
            raise ValueError("请指定技能名称和资源文件")
        skill = snapshot.resolve(name)
        if skill is None and action not in {"read", "create"}:
            # Disabling invocation is not a content-write lock (Hermes _find_skill).
            from dataclasses import replace

            skill = replace(snapshot, disabled_paths=frozenset()).resolve(name)
        if action == "create":
            if not _NAME.fullmatch(name) or len(name) > 64:
                raise ValueError("新技能名称须为 1–64 个小写字母、数字或横线")
            # Include disabled skills: learning never bypasses an existing disabled copy.
            if any(s.name.casefold() == name.casefold() for s in snapshot.skills):
                raise ValueError("同名技能已经存在，请读取后修改或选择不同名称")
            if self.root.exists() and any(
                p.name.casefold() == name.casefold() for p in self.root.iterdir()
            ):
                raise ValueError("同名技能目录已经存在")
            directory, file = self.root / name, "SKILL.md"
        else:
            if skill is None:
                raise ValueError("技能不存在、已禁用或名称不唯一")
            name = skill.qualified_name
            directory = skill.path.parent
        path = self._target(directory, file)
        if action == "read":
            content = self._read(path)
            value = revision(content)
            self.reads[str(path.resolve())] = value
            _audit(self.state.record_skill_activity, str(directory.resolve()), name)
            return {
                "name": name,
                "file": file,
                "exists": content is not None,
                "content": content,
                "revision": value,
                "editable": self._editable(skill),
            }
        if action not in {
            "create",
            "patch",
            "edit",
            "write_file",
            "remove_file",
            "delete",
            "history",
            "restore",
        }:
            raise ValueError("不支持的技能管理操作")
        if skill is not None and self._locked(directory, writing=action != "history"):
            raise ValueError("此技能已由用户锁定，不能修改，也不能创建同名替代技能")
        if action == "history":
            return {"versions": self.state.history(name, file)}
        if (
            not self.approved
            and not _BATCH_APPROVED.get()
            and self.fence.switch.option("skills", "write_approval", False) is True
        ):
            identity = self.state.stage(args, background=bool(self.review))
            return {
                "staged": True,
                "pending_id": identity,
                "message": "已暂存，等待用户审批；尚未写入技能",
            }
        if action == "delete":
            from .maintenance import Curator

            curator = Curator(self.home, self.service, self.cwd)
            metadata = next(
                (r for r in self.state.usage() if r["path"] == str(directory.resolve())), {}
            )
            if metadata.get("pinned"):
                raise ValueError("此技能已固定，不能删除；仍可修改内容")
            if self.review or self.background_origin:
                if str(directory.resolve()) not in {r["path"] for r in curator.candidates()}:
                    raise ValueError("后台只能归档由它管理的本地技能，不能删除其他来源的技能")
                target = args.get("absorbed_into")
                if not target or target == name or snapshot.resolve(target) is None:
                    raise ValueError("后台归档必须指定已经吸收其内容的现有技能 absorbed_into")
                archive_id = publish_skill_io(
                    lambda: curator.archive(directory, name, absorbed_into=target)
                )
                return {"changed": True, "archived": archive_id}
            from corki.platforms.paths import is_link_path

            if is_link_path(directory) or directory.resolve() == skill.root.resolve():
                raise ValueError("不能删除技能根目录或链接目录")
            # Source disappears; the before-package remains recoverable like Hermes' ledger blobs.
            archive_id = publish_skill_io(lambda: curator.archive(directory, name, external=True))
            return {"changed": True, "deleted": name, "recovery_archive": archive_id}
        parts = path.relative_to(directory).parts
        if (
            action in {"write_file", "remove_file"}
            and file != "SKILL.md"
            and (len(parts) < 2 or parts[0] not in {"references", "templates", "scripts", "assets"})
        ):
            raise ValueError("资源文件须放在 references、templates、scripts 或 assets 中")
        if action == "remove_file" and file == "SKILL.md":
            raise ValueError("不能删除 SKILL.md；本轮复盘只能改进技能，不能删除技能")
        return self._write(action, name, file, path, directory, args)

    def _write(self, action, name, file, path, directory, args):
        self.state.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        locks = self.state.root / "locks"
        locks.mkdir(exist_ok=True, mode=0o700)
        owner = (
            None
            if str(directory.resolve()) in _BATCH_LOCKS.get()
            else open_lock(locks / (hashlib.sha256(str(directory).encode()).hexdigest() + ".lock"))
        )
        acquired = False
        created_directory = False
        try:
            if owner is not None:
                lock(owner)
                acquired = True
            self.fence.check()
            before = self._read(path)
            if action == "create":
                if directory.exists():
                    raise ValueError("技能目录已经存在，不会覆盖")
            else:
                if (
                    self.review
                    and before is not None
                    and (
                        str(path.resolve()) not in self.reads
                        or self.reads[str(path.resolve())] is None
                    )
                ):
                    raise ValueError("请先读取此文件的最新内容，再提交修改")
                if "revision" in args and args["revision"] != revision(before):
                    raise ValueError("技能内容已更新，请重新读取后修改")
            if self._locked(directory, writing=True):
                raise ValueError("此技能已由用户锁定，不能修改")
            after = self._new_content(action, name, file, before, args)
            if after is not None and len(after) > 100_000:
                raise ValueError("技能资源过大，请拆分为相关参考文件")
            if after is not None and len(after.encode("utf-8")) > 1_048_576:
                raise ValueError("技能资源超过 1 MiB，请拆分为相关参考文件")
            if file == "SKILL.md":
                parsed_name, description, _ = parse(after, directory.name)
                if not isinstance(yaml.safe_load(extract(after)).get("name"), str):
                    raise ValueError("SKILL.md 必须显式声明 name")
                if action == "create" and parsed_name != directory.name:
                    raise ValueError("新技能的 name 必须与目录名一致")
                old_description = parse(before, directory.name)[1] if before is not None else None
                if len(description) > 60 and description != old_description:
                    raise ValueError("技能描述请控制在 60 个字符内；详细信息放到正文中")
                if not re.split(r"\n---\s*\n", after, maxsplit=1)[-1].strip():
                    raise ValueError("SKILL.md 需要在 frontmatter 后提供正文")
            if before == after:
                return {"changed": False, "revision": revision(before)}
            from .scanning import SkillScan

            scan = SkillScan(
                directory,
                path,
                background=bool(self.review) or self.background_origin,
                enabled=self.fence.switch.option("skills", "guard_agent_created", False) is True,
            )
            identity = _audit(
                self.state.prepare_edit,
                name,
                file,
                before,
                after,
                "review" if self.review or self.background_origin else "foreground",
                self.review,
            )
            if action == "create":
                directory.mkdir(parents=True, mode=0o700)
                created_directory = True
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if after is None:
                publish_skill_io(lambda: (self.fence.check(), path.unlink()))
                if identity is not None:
                    _audit(self.state.applied, identity)
                _audit(
                    self.state.record_skill_activity, str(directory.resolve()), name, action=action
                )
                if self.on_write:
                    self.on_write()
                return {"changed": True, "version": identity}
            temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("x", encoding="utf-8", newline="") as stream:
                    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
                    os.chmod(temporary, mode)
                    stream.write(after)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Recheck directory boundaries, revision and live consent at publication.
                self._target(directory, file)
                if self._read(path) != before:
                    raise ValueError("技能内容已更新，未覆盖其他修改")

                def publish():
                    self.fence.check()
                    os.replace(temporary, path.resolve())

                publish_skill_io(publish)
                try:
                    scan.check()
                except Exception:
                    if before is None:
                        path.resolve().unlink(missing_ok=True)
                    else:
                        temporary.write_text(before, encoding="utf-8", newline="")
                        os.chmod(temporary, mode)
                        os.replace(temporary, path.resolve())
                    raise
            finally:
                temporary.unlink(missing_ok=True)
            if identity is not None:
                _audit(self.state.applied, identity)
            _audit(
                self.state.record_skill_activity,
                str(directory.resolve()),
                name,
                action=action,
                agent_created=action == "create" and bool(self.review or self.background_origin),
            )
            if str(path.resolve()) in self.reads:
                self.reads[str(path.resolve())] = revision(after)
            if self.on_write:
                self.on_write()
            return {"changed": True, "version": identity, "revision": revision(after)}
        finally:
            if acquired:
                unlock_and_close(owner)
            elif owner is not None:
                owner.close()
            if created_directory and not path.exists() and directory.exists():
                with suppress(OSError):
                    directory.rmdir()  # Only our empty staging directory, never a skill tree.

    def _new_content(self, action, name, file, before, args):
        if action == "remove_file":
            if before is None:
                raise ValueError("资源文件不存在")
            return None
        if action == "restore":
            return self.state.prior_content(args.get("version"), name, file)
        if action == "patch":
            from .hermes_guard.fuzzy_match import fuzzy_find_and_replace

            old, new = args.get("old_text"), args.get("new_text")
            if before is None or not isinstance(old, str) or not isinstance(new, str):
                raise ValueError("请提供待替换的文本及新文本；先读取目标文件确认内容")
            content, _, _, error = fuzzy_find_and_replace(
                before, old, new, bool(args.get("replace_all", False))
            )
            if error:
                raise ValueError(error)
            return content
        content = args.get("content")
        if not isinstance(content, str) or (action != "write_file" and not content.strip()):
            raise ValueError("请提供完整的文本内容")
        return content
