"""Public exec/wait controls. These never bypass ordinary nested tool dispatch."""

from corki.code_mode.specs import exec_spec, parse_source
from corki.prompting import PromptStore
from corki.protocol.tools import ToolConcurrency, ToolResult, ToolSpec


class CodeModeExecTool:
    def __init__(self, service, *, registry=None):
        self.service = service
        self._spec = exec_spec(service.registry if registry is None else registry)

    @property
    def spec(self):
        return self._spec

    async def execute(self, call, context):
        source, delay_ms, max_tokens = parse_source(call.raw_arguments)
        result = await self.service.execute(call.id, source, delay_ms, max_tokens)
        await self.service.elicitations.wait_until_clear()
        return ToolResult(
            call.id,
            call.name,
            result.content,
            content_items=result.content_items,
            is_error=result.is_error,
            state_update=self.service.consume_state_update(),
            code_mode_lifecycle_json=result.lifecycle_json,
        )


class CodeModeWaitTool:
    spec = ToolSpec(
        "wait",
        PromptStore().render("tools/code_mode_wait"),
        {
            "type": "object",
            "properties": {
                "cell_id": {
                    "type": "string",
                    "description": "Identifier of the running exec cell.",
                },
                "yield_time_ms": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Wait before yielding more output. Defaults to 10000 ms.",
                },
                "max_tokens": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Output token budget for this wait call. Defaults to 10000 tokens."
                    ),
                },
                "terminate": {
                    "type": "boolean",
                    "description": (
                        "True stops the running exec cell; false or omitted waits for output."
                    ),
                },
            },
            "required": ["cell_id"],
            "additionalProperties": False,
        },
        concurrency=ToolConcurrency.PARALLEL,
    )

    def __init__(self, service):
        self.service = service

    async def execute(self, call, context):
        args = call.arguments
        result = await self.service.wait(
            args["cell_id"],
            args.get("yield_time_ms", 10000),
            args.get("max_tokens", 10000),
            args.get("terminate", False),
        )
        await self.service.elicitations.wait_until_clear()
        return ToolResult(
            call.id,
            call.name,
            result.content,
            content_items=result.content_items,
            is_error=result.is_error,
            state_update=self.service.consume_state_update(),
            code_mode_lifecycle_json=result.lifecycle_json,
        )
