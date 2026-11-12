"""Model tools for progressive skill discovery and resource loading."""

from __future__ import annotations

import json

from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.skills.io import run_skill_io
from corki.skills.service import SkillService
from corki.tools.base import ToolContext


class SkillListTool:
    def __init__(self, service: SkillService) -> None:
        self._service = service

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="skill_list",
            description=(
                "List available Corki skills using bounded name, description, "
                "scope, and source metadata. "
                "Directory categories: public/shared skills are in ~/.agents/skills; "
                "Corki extensions are in $CORKI_HOME/skills (default ~/.corki/skills); "
                "work skills are in $CORKI_HOME/skills/work and may only be used when explicitly "
                "requested by the user, never automatically matched by description; "
                "built-in skills are in $CORKI_HOME/skills/.system. "
                "Both public and extension skills can have user scope; distinguish them by path. "
                "~/.codex/skills is not Corki's public skills directory. "
                "Available work skills are marked work_only, not automatic invocation candidates. "
                "Disabled or hidden skills may still exist on disk."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        return await run_skill_io(self._execute, call, context)

    def _execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        snapshot = self._service.snapshot(context.cwd)
        payload = {
            "skills": [
                {
                    "name": skill.qualified_name,
                    "description": skill.description,
                    "scope": skill.scope.value,
                    "path": str(skill.path),
                    **(
                        {"work_only": True, "allow_implicit_invocation": False}
                        if skill.work_only
                        else {}
                    ),
                    **({"plugin_id": skill.plugin_id} if skill.plugin_id is not None else {}),
                }
                for skill in snapshot.skills
                if snapshot.is_visible(skill) or (skill.work_only and snapshot.is_enabled(skill))
            ],
            "warnings": [f"{error.path}: {error.message}" for error in snapshot.errors],
        }
        return ToolResult(call.id, call.name, json.dumps(payload, ensure_ascii=False))


class SkillReadTool:
    def __init__(self, service: SkillService) -> None:
        self._service = service

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="skill_read",
            description=(
                "Read a complete SKILL.md or a referenced text resource. Read SKILL.md before "
                "acting on a matching skill; resolve referenced files relative to its directory. "
                "This reads discovered ordinary skills by name; use skill_list for discovery. "
                "Resources must be UTF-8 text no larger than 128 KiB. Model-visible output "
                "may still be truncated by the context budget. This tool has no pagination; "
                "if truncated or over the resource limit, use available ordinary read-only "
                "file tools to read the referenced file in bounded parts until EOF before acting. "
                "Memory files under memories/skills are separate: read their referenced paths "
                "using available "
                "memory tools or ordinary read-only file tools, not this name-based lookup."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The discovered skill name, including its plugin prefix "
                        "when listed. Use skill_list to resolve missing or ambiguous names.",
                    },
                    "file": {
                        "type": "string",
                        "description": "Text resource relative to the skill directory. Defaults "
                        "to SKILL.md; absolute paths, '..', and paths escaping the skill "
                        "directory are not allowed.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
            output_char_budget=160_000,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        return await run_skill_io(self._execute, call, context)

    def _execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        assert call.arguments is not None
        name = str(call.arguments["name"])
        snapshot = self._service.snapshot(context.cwd)
        skill = snapshot.resolve(name)
        if skill is None:
            return ToolResult(
                call.id,
                call.name,
                f"skill is missing or ambiguous: {name}. "
                "Use skill_list to check available names; do not infer availability from a "
                "directory name. If the reference came from memory, read the exact referenced "
                "skills/<skill-name>/SKILL.md path with memories::read when available, or "
                "ordinary read-only file tools under the memory root given in context. "
                "Do not guess a memory root or treat this lookup failure as a missing file.",
                is_error=True,
            )
        file_name = str(call.arguments.get("file") or "SKILL.md")
        try:
            contents = self._service.read(skill, file_name)
        except (OSError, UnicodeError, ValueError) as exc:
            return ToolResult(
                call.id,
                call.name,
                f"failed to read skill resource: {exc}",
                is_error=True,
            )
        return ToolResult(
            call.id,
            call.name,
            json.dumps(
                {
                    "name": skill.qualified_name,
                    "file": file_name,
                    "skill_dir": str(skill.path.parent),
                    "contents": contents,
                },
                ensure_ascii=False,
            ),
        )
