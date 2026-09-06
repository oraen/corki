"""Model-visible planning tool that updates explicit graph state."""

from __future__ import annotations

from corki.planning import validate_plan
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec, ToolStateUpdate
from corki.tools.base import ToolContext


class UpdatePlanTool:
    @property
    def spec(self) -> ToolSpec:
        item_schema = {
            "type": "object",
            "properties": {
                "step": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed"],
                },
            },
            "required": ["step", "status"],
            "additionalProperties": False,
        }
        return ToolSpec(
            name="update_plan",
            description=("Update the user-visible task plan. At most one step may be in_progress."),
            parameters={
                "type": "object",
                "properties": {
                    "plan": {"type": "array", "items": item_schema, "minItems": 1},
                    "explanation": {"type": "string"},
                },
                "required": ["plan"],
                "additionalProperties": False,
            },
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        assert call.arguments is not None
        plan = validate_plan(call.arguments["plan"])
        rendered = tuple(item.as_dict() for item in plan)
        explanation = str(call.arguments.get("explanation", "")).strip()
        content = explanation or "Plan updated."
        return ToolResult(
            call.id,
            call.name,
            content,
            display_content=content,
            state_update=ToolStateUpdate(plan=rendered),
        )
