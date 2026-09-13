"""Root-thread question tool, exposed as an ordinary model-only function."""

import json

from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.protocol.user_input import parse_questions
from corki.tools.base import ToolContext


class RequestUserInputTool:
    def __init__(self, *, default_mode_enabled: bool = False) -> None:
        self._default_mode_enabled = default_mode_enabled

    @property
    def spec(self) -> ToolSpec:
        def object_schema(properties):
            return {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            }

        option = object_schema(
            {
                "label": {"type": "string", "description": "User-facing label (1-5 words)."},
                "description": {
                    "type": "string",
                    "description": "One sentence explaining the tradeoff.",
                },
            }
        )
        question = object_schema(
            {
                "id": {"type": "string", "description": "Stable snake_case answer identifier."},
                "header": {
                    "type": "string",
                    "description": "Short header (12 or fewer characters).",
                },
                "question": {"type": "string", "description": "Single-sentence question."},
                "options": {
                    "type": "array",
                    "items": option,
                    "description": "Provide 2-3 mutually exclusive choices. Put the recommended "
                    'choice first and suffix its label with "(Recommended)". Do not add Other; '
                    "the client adds free-form input.",
                },
            }
        )
        modes = "Plan or Default mode" if self._default_mode_enabled else "Plan mode"
        return ToolSpec(
            "request_user_input",
            "Request user input for one to three short questions and wait for the response. "
            f"This tool is only available in {modes}.",
            object_schema(
                {
                    "questions": {
                        "type": "array",
                        "items": question,
                        "description": "Questions to show the user. Prefer 1 and do not exceed 3.",
                    }
                }
            ),
            exposure=ToolExposure.DIRECT_MODEL_ONLY,
        )

    def parse_call_arguments(self, call: ToolCall):
        if call.parse_error is not None or call.arguments is None:
            raise ValueError(f"invalid JSON arguments: {call.parse_error}")
        return call.arguments

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        if context.is_non_root_agent:
            raise ValueError("request_user_input can only be used by the root thread")
        if context.collaboration_mode != "plan" and not self._default_mode_enabled:
            raise ValueError("request_user_input is unavailable in Default mode")
        if context.request_user_input is None:
            raise ValueError("request_user_input has no active host input channel")
        response = await context.request_user_input(
            str(call.id),
            parse_questions(call.arguments),
            context.collaboration_mode == "plan",
        )
        return ToolResult(call.id, call.name, json.dumps(response, ensure_ascii=False))
