"""Explicit TokenBudget tools; window requests survive in the result ledger."""

from corki.protocol.tools import CodeModeOutput, ToolExposure, ToolResult, ToolSpec, ToolStateUpdate

_PARAMETERS = {"type": "object", "properties": {}, "additionalProperties": False}


class NewContextTool:
    spec = ToolSpec(
        "new_context",
        "Start a new context window. Does not clear, reset, or otherwise affect environment state.",
        _PARAMETERS,
        exposure=ToolExposure.DIRECT_MODEL_ONLY,
    )

    async def execute(self, call, context):
        return ToolResult(
            call.id,
            call.name,
            "A new context window will start without summarizing conversation history.",
            state_update=ToolStateUpdate(new_context_requested=True),
        )


class GetContextRemainingTool:
    spec = ToolSpec(
        "get_context_remaining",
        "Get the remaining tokens in the current context window.",
        _PARAMETERS,
    )

    def __init__(self, window, thread_id):
        self._window = window
        self._thread_id = thread_id

    async def execute(self, call, context):
        remaining = await self._window.remaining_tokens(self._thread_id)
        count = str(remaining) if remaining is not None else "unknown"
        return ToolResult(
            call.id,
            call.name,
            f"You have {count} tokens left in this context window.",
            code_mode_output=CodeModeOutput({"tokens_left": remaining}),
        )
