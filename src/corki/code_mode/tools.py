"""Public exec/wait controls. These never bypass ordinary nested tool dispatch."""

from corki.code_mode.specs import exec_spec, parse_source
from corki.protocol.tools import ToolConcurrency, ToolResult, ToolSpec


class CodeModeExecTool:
    def __init__(self, service):
        self.service = service
        self._spec = exec_spec(service.registry)

    @property
    def spec(self):
        return self._spec

    async def execute(self, call, context):
        source, delay_ms, max_tokens = parse_source(call.raw_arguments)
        result = await self.service.execute(call.id, source, delay_ms, max_tokens)
        return ToolResult(
            call.id,
            call.name,
            result.content,
            content_items=result.content_items,
            is_error=result.is_error,
            state_update=self.service.consume_state_update(),
        )


class CodeModeWaitTool:
    spec = ToolSpec(
        "wait",
        "Observe only an exec cell that returned a running cell ID. Returns new output only. "
        "terminate stops it. A missing cell must not be restarted blindly.",
        {
            "type": "object",
            "properties": {
                "cell_id": {"type": "string"},
                "yield_time_ms": {"type": "integer", "minimum": 0},
                "max_tokens": {"type": "integer", "minimum": 0},
                "terminate": {"type": "boolean"},
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
        return ToolResult(
            call.id,
            call.name,
            result.content,
            content_items=result.content_items,
            is_error=result.is_error,
            state_update=self.service.consume_state_update(),
        )
