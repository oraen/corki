"""Codex-compatible model-facing goal tools."""

import json

from corki.prompting import PromptStore
from corki.protocol.tools import CodeModeOutput, ToolConcurrency, ToolResult, ToolSpec

_NAMES = ("get_goal", "create_goal", "update_goal")
_PROMPTS = PromptStore()


class GoalTool:
    def __init__(self, controller, name):
        self.controller = controller
        self.name = name

    @property
    def spec(self):
        properties = {}
        required = []
        if self.name == "create_goal":
            properties = {
                "objective": {"type": "string", "minLength": 1, "maxLength": 4000},
                "token_budget": {"type": "integer", "minimum": 1},
            }
            required = ["objective"]
        elif self.name == "update_goal":
            properties = {"status": {"type": "string", "enum": ["complete", "blocked", "paused"]}}
            required = ["status"]
        return ToolSpec(
            self.name,
            _PROMPTS.render(f"goals/{self.name}"),
            {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL
            if self.name == "get_goal"
            else ToolConcurrency.EXCLUSIVE,
        )

    async def execute(self, call, context):
        arguments = call.arguments or {}
        if self.name == "get_goal":
            result = await self.controller.get()
        elif self.name == "create_goal":
            result = await self.controller.create(**arguments)
        else:
            if arguments.get("status") not in {"complete", "blocked", "paused"}:
                raise ValueError("update_goal only allows complete, blocked, or paused")
            result = await self.controller.update(**arguments)
        result = codex_response(
            result, completion=self.name == "update_goal" and arguments.get("status") == "complete"
        )
        return ToolResult(
            call.id,
            call.name,
            json.dumps(result, ensure_ascii=False),
            code_mode_output=CodeModeOutput(result),
        )


def goal_tools(controller):
    return tuple(GoalTool(controller, name) for name in _NAMES)


def codex_response(result, *, completion=False):
    """Match the native tool wire shape; internal SQLite/Python names stay private."""
    goal = result["goal"]
    names = {
        "thread_id": "threadId",
        "objective": "objective",
        "status": "status",
        "token_budget": "tokenBudget",
        "tokens_used": "tokensUsed",
        "time_used_seconds": "timeUsedSeconds",
        "created_at": "createdAt",
        "updated_at": "updatedAt",
    }
    wire = {target: goal[source] for source, target in names.items()} if goal else None
    if wire:
        if wire["tokenBudget"] is None:
            del wire["tokenBudget"]
        wire["status"] = {"usage_limited": "usageLimited", "budget_limited": "budgetLimited"}.get(
            wire["status"], wire["status"]
        )
    report = result.get("completion_budget_report") if completion else None
    if report:
        report = (
            "Goal achieved. Report final usage from this result: goal.tokensUsed / "
            "goal.tokenBudget when budgeted, and goal.timeUsedSeconds "
            "as human-friendly elapsed time."
        )
    return {
        "goal": wire,
        "remainingTokens": result.get("remaining_tokens"),
        "completionBudgetReport": report,
    }
