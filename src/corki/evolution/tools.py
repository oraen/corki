"""A narrow, versioned skill writer; shared/system/project skills stay read-only."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import uuid
from contextlib import suppress

import yaml

from corki.evolution.state import EvolutionState
from corki.platforms.paths import archive_parts, is_link_path
from corki.protocol.tools import ToolResult, ToolSpec
from corki.skills.frontmatter import extract, parse
from corki.skills.io import check_skill_io, publish_skill_io, run_skill_io
from corki.storage.file_lock import open_lock, try_lock, unlock_and_close

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MAX_BYTES = 1_000_000


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
    def __init__(self, home, service, cwd, switch, *, fence=None, review=None, on_write=None):
        self.home, self.service, self.cwd = home.resolve(), service, cwd
        self.root = self.home / "skills"
        self.state = EvolutionState(self.home)
        self.fence = fence or WriteFence(switch)
        self.review = review
        self.on_write = on_write
        self.reads = {}

    @property
    def spec(self):
        return ToolSpec(
            name="skill_manage",
            description=(
                "Save verified procedures as Corki extension skills. Requires self-evolution. "
                "List/read existing skills; create a skill with complete SKILL.md "
                "frontmatter (name and description); patch or write_file only after read with its "
                "revision; history/restore provide reversible edits. Built-in, public/shared, "
                "project and plugin skills are read-only. Never alter global memory or config."
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
                            "write_file",
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
                    "version": {
                        "type": "string",
                        "description": "History edit ID whose prior content to restore",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            output_char_budget=None,
        )

    async def execute(self, call, context):
        try:
            value = await run_skill_io(self._execute, dict(call.arguments or {}))
            return ToolResult(call.id, call.name, json.dumps(value, ensure_ascii=False))
        except (OSError, ValueError, yaml.YAMLError) as error:
            return ToolResult(call.id, call.name, str(error), is_error=True)

    def _snapshot(self):
        from dataclasses import replace

        snapshot = self.service.snapshot(self.cwd, force_reload=True)
        disabled = {
            s.path
            for s in snapshot.skills
            if not self.fence.switch.skill_enabled(s.qualified_name, s.path)
        }
        return replace(snapshot, disabled_paths=snapshot.disabled_paths | disabled)

    def _editable(self, skill):
        directory = skill.path.parent
        return (
            directory.is_relative_to(self.root)
            and directory != self.root
            and directory.relative_to(self.root).parts[0] != ".system"
            and skill.plugin_id is None
        )

    def _target(self, directory, file):
        parts = archive_parts(file, windows=True)
        if len(parts) > 8 or any(part.startswith(".") for part in parts):
            raise ValueError("技能资源路径不安全")
        # Do not follow symlinks/junctions in any component, including the root.
        current = self.root if directory.is_relative_to(self.root) else directory
        if is_link_path(current):
            raise ValueError("自进化不读写链接指向的技能目录")
        for part in directory.relative_to(current).parts + parts:
            current = current / part
            if is_link_path(current):
                raise ValueError("自进化不读写链接指向的技能资源")
        if not current.resolve().is_relative_to(directory.resolve()):
            raise ValueError("技能资源越出目录")
        return current

    def _read(self, path):
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise ValueError("技能资源不是文本文件或内容过大，请使用精简的扩展技能")
        return path.read_text(encoding="utf-8")

    def _execute(self, args):
        self.fence.check()
        action = args.get("action")
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
            self.reads[str(path)] = value
            return {
                "name": name,
                "file": file,
                "exists": content is not None,
                "content": content,
                "revision": value,
                "editable": self._editable(skill),
            }
        if action not in {"create", "patch", "write_file", "history", "restore"}:
            raise ValueError("不支持的技能管理操作")
        if skill is not None and not self._editable(skill):
            raise ValueError("仅能修改 Corki 扩展 Skills，请创建扩展补充技能")
        if action == "history":
            return {"versions": self.state.history(name, file)}
        return self._write(action, name, file, path, directory, args)

    def _write(self, action, name, file, path, directory, args):
        self.state.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        locks = self.state.root / "locks"
        locks.mkdir(exist_ok=True, mode=0o700)
        owner = open_lock(locks / (hashlib.sha256(str(directory).encode()).hexdigest() + ".lock"))
        acquired = False
        created_directory = False
        try:
            acquired = try_lock(owner)
            if not acquired:
                raise ValueError("技能正在被另一个学习任务修改，请重新读取后重试")
            self.fence.check()
            before = self._read(path)
            if action == "create":
                if directory.exists():
                    raise ValueError("技能目录已经存在，不会覆盖")
            else:
                if str(path) not in self.reads or self.reads[str(path)] != revision(before):
                    raise ValueError("请先读取此文件的最新内容，再提交修改")
                if before is not None and args.get("revision") != revision(before):
                    raise ValueError("技能内容已更新，请重新读取后修改")
            after = self._new_content(action, name, file, before, args)
            if len(after.encode("utf-8")) > _MAX_BYTES:
                raise ValueError("技能资源过大，请拆分为相关参考文件")
            if file == "SKILL.md":
                old_name = (
                    parse(before, directory.name)[0] if before is not None else directory.name
                )
                parsed_name, _, _ = parse(after, directory.name)
                if not isinstance(yaml.safe_load(extract(after)).get("name"), str):
                    raise ValueError("SKILL.md 必须显式声明 name")
                if parsed_name != old_name:
                    raise ValueError("SKILL.md 的 name 必须保持与原技能一致")
            if before == after:
                return {"changed": False, "revision": revision(before)}
            identity = self.state.prepare_edit(
                name, file, before, after, "review" if self.review else "foreground", self.review
            )
            if action == "create":
                directory.mkdir(parents=True, mode=0o700)
                created_directory = True
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
                    if not self.fence.switch.skill_enabled(name, directory / "SKILL.md"):
                        raise ValueError("此技能已禁用，未提交自进化修改")
                    os.replace(temporary, path)

                publish_skill_io(publish)
            finally:
                temporary.unlink(missing_ok=True)
            self.state.applied(identity)
            self.reads.pop(str(path), None)
            if self.on_write:
                self.on_write()
            return {"changed": True, "version": identity, "revision": revision(after)}
        finally:
            if acquired:
                unlock_and_close(owner)
            else:
                owner.close()
            if created_directory and not path.exists() and directory.exists():
                with suppress(OSError):
                    directory.rmdir()  # Only our empty staging directory, never a skill tree.

    def _new_content(self, action, name, file, before, args):
        if action == "restore":
            return self.state.prior_content(args.get("version"), name, file)
        if action == "patch":
            old, new = args.get("old_text"), args.get("new_text")
            if before is None or not isinstance(old, str) or not old or before.count(old) != 1:
                raise ValueError("old_text 必须在当前文件中恰好匹配一次")
            if not isinstance(new, str):
                raise ValueError("new_text 必须是字符串")
            return before.replace(old, new, 1)
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("请提供完整的文本内容")
        return content
