"""Model-visible child lifecycle; tool calls use the ordinary execution ledger."""

import json

from corki.protocol.tools import ToolResult, ToolSpec


class AgentTool:
    def __init__(self, controller, name, description, properties, required):
        self.controller = controller
        self.spec = ToolSpec(
            name,
            description,
            {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        )

    async def execute(self, call, context):
        args = dict(call.arguments or {})
        name = call.name
        if name in {"spawn_agent", "send_agent_message"}:
            message = args.get("message")
            if not isinstance(message, str) or not message.strip() or len(message) > 100000:
                raise ValueError("message must contain 1–100000 characters")
        if name == "spawn_agent":
            result = await self.controller.spawn(message, context)
        elif name == "wait_agents":
            timeout = args.get("timeout_ms", 10000)
            if type(timeout) is not int or not 0 <= timeout <= 60000:
                raise ValueError("timeout_ms must be between 0 and 60000")
            result = await self.controller.wait(args["ids"], timeout)
        elif name == "send_agent_message":
            result = await self.controller.send(args["id"], message)
        elif name == "stop_agent":
            result = await self.controller.stop(args["id"])
        elif name == "close_agent":
            result = await self.controller.close(args["id"])
        else:
            result = {"agents": [child.snapshot() for child in self.controller.children.values()]}
        return ToolResult(call.id, name, json.dumps(result, ensure_ascii=False))


def agent_tools(controller):
    identity = {"id": {"type": "string"}}
    message = {"message": {"type": "string", "maxLength": 100000}}
    definitions = [
        (
            "spawn_agent",
            "Delegate a focused task to an independent child agent. It inherits "
            "your current model, working directory, skills and permission boundaries, but NOT "
            "your conversation history: include all necessary context and the goal in message. "
            "Use for specialist skills requiring a subagent, or independent work. Up to six "
            "children; children cannot spawn grandchildren. Shared files require coordinated "
            "ownership. Wait for actual results before reporting success. Approval or user "
            "questions must be handled by the parent; never bypass permissions.",
            message,
            ["message"],
        ),
        (
            "wait_agents",
            "Wait until any selected child finishes, or timeout. A timeout does not "
            "stop children. Inspect statuses/results; repeat if still running.",
            {
                "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "timeout_ms": {"type": "integer", "minimum": 0, "maximum": 60000},
            },
            ["ids"],
        ),
        (
            "send_agent_message",
            "Supplement a running child task, or start a follow-up on an "
            "idle child using its existing history.",
            {**identity, **message},
            ["id", "message"],
        ),
        ("list_agents", "Inspect this parent's children and their latest results.", {}, []),
        (
            "stop_agent",
            "Stop a child task and its owned work; keep its session for follow-up.",
            identity,
            ["id"],
        ),
        (
            "close_agent",
            "Stop and release a child. Its recorded history remains on disk.",
            identity,
            ["id"],
        ),
    ]
    return tuple(AgentTool(controller, *definition) for definition in definitions)
