"""Model-visible planning tool that updates explicit graph state."""

from __future__ import annotations

from corki.planning import validate_plan
from corki.protocol.tools import CodeModeOutput, ToolCall, ToolResult, ToolSpec, ToolStateUpdate
from corki.tools.base import ToolContext


class UpdatePlanTool:
    @property
    def spec(self) -> ToolSpec:
        item_schema = {
            "type": "object",
            "properties": {
                "step": {"type": "string", "description": "Task step text."},
                "status": {
                    "type": "string",
                    "description": "Step status.",
                    "enum": ["pending", "in_progress", "completed"],
                },
            },
            "required": ["step", "status"],
            "additionalProperties": False,
        }
        return ToolSpec(
            name="update_plan",
            description=(
                "Updates the task plan.\n"
                "Provide an optional explanation and a list of plan items, each with a step "
                "and status.\n"
                "At most one step can be in_progress at a time.\n"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "array",
                        "items": item_schema,
                        "description": "The list of steps",
                    },
                    "explanation": {
                        "type": "string",
                        "description": "Optional explanation for this plan update.",
                    },
                },
                "required": ["plan"],
                "additionalProperties": False,
            },
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        if context.collaboration_mode == "plan":
            raise ValueError("update_plan is a TODO/checklist tool and is not allowed in Plan mode")
        assert call.arguments is not None
        plan = validate_plan(call.arguments["plan"])
        rendered = tuple(item.as_dict() for item in plan)
        explanation = call.arguments.get("explanation")
        return ToolResult(
            call.id,
            call.name,
            "Plan updated",
            code_mode_output=CodeModeOutput({}),
            # The dedicated plan event renders the explanation with its steps.
            display_content="",
            state_update=ToolStateUpdate(plan=rendered, plan_explanation=explanation),
        )
