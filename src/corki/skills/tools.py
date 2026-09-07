"""Model tools for progressive skill discovery and resource loading."""

from __future__ import annotations

import json

from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
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
                "scope, and source metadata."
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
        snapshot = self._service.snapshot(context.cwd)
        payload = {
            "skills": [
                {
                    "name": skill.qualified_name,
                    "description": skill.description,
                    "scope": skill.scope.value,
                    "path": str(skill.path),
                }
                for skill in snapshot.skills
                if snapshot.is_visible(skill)
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
                "acting on a matching skill; resolve referenced files relative to its directory."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "file": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
            output_char_budget=160_000,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        assert call.arguments is not None
        name = str(call.arguments["name"])
        snapshot = self._service.snapshot(context.cwd)
        skill = snapshot.resolve(name)
        if skill is None:
            return ToolResult(
                call.id,
                call.name,
                f"skill is missing or ambiguous: {name}",
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
