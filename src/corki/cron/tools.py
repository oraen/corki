"""One Hermes-style management tool, backed by the same store as the UI."""

import asyncio
import json

from corki.protocol.tools import ToolResult, ToolSpec

from .store import CronStore


class CronTool:
    def __init__(self, home):
        self.store = CronStore(home)

    @property
    def spec(self):
        return ToolSpec(
            "cron_manage",
            "Manage persistent scheduled tasks only when the user explicitly requests scheduling. "
            "Actions: list, create, update, pause, resume, delete, history. Schedules: 'every 2h' "
            "or '30m' recur; 'in 30m' or a future ISO timestamp run once; five-field cron uses "
            "the supplied IANA timezone. Each occurrence runs a fresh Corki Agent session using "
            "the saved provider/model (default model for legacy tasks) and configured permissions. "
            "The Manager or cron service must remain "
            "running to trigger tasks; list reports online. Missed fires collapse to one. "
            "Use list to obtain id/version before edits. Pausing does not cancel a running task. "
            "Never schedule a task merely to wait for a tool or to continue ordinary work.",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list",
                            "create",
                            "update",
                            "pause",
                            "resume",
                            "delete",
                            "history",
                        ],
                    },
                    "id": {"type": "string"},
                    "version": {"type": "integer"},
                    "values": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string", "maxLength": 500},
                            "prompt": {"type": "string"},
                            "provider": {"type": "string"},
                            "model": {"type": "string"},
                            "schedule": {"type": "string"},
                            "timezone": {"type": "string"},
                            "workdir": {"type": "string"},
                            "enabled": {"type": "boolean"},
                        },
                        "required": ["name", "prompt", "schedule", "timezone"],
                        "additionalProperties": False,
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, cwd):
        action = arguments["action"]
        identity = arguments.get("id")
        version = arguments.get("version")
        if action == "list":
            return self.store.list()
        if action == "history":
            return {"runs": self.store.history(identity)}
        if action in ("create", "update"):
            values = dict(arguments.get("values") or {})
            if action == "create" and "provider" not in values and "model" not in values:
                from corki.config.document import loads

                try:
                    config = loads((self.store.home / "settings.json").read_text())
                    defaults = config.get("default", {})
                    values.update(provider=defaults.get("provider"), model=defaults.get("model"))
                except (OSError, ValueError, AttributeError) as error:
                    raise ValueError("请先配置默认供应商和模型") from error
            values.setdefault("workdir", str(cwd))
            if action == "update" and not identity:
                raise ValueError("update requires id and version")
            identity = self.store.save(values, identity if action == "update" else None, version)
        else:
            self.store.change(identity, action, version)
        return {"id": identity, "message": "Saved", "online": self.store.list()["online"]}

    async def execute(self, call, context):
        try:
            result = await asyncio.to_thread(self.invoke, call.arguments, context.cwd)
            return ToolResult(call.id, call.name, json.dumps(result, ensure_ascii=False))
        except ValueError as error:
            return ToolResult(call.id, call.name, str(error), is_error=True)


def register_cron_tools(registry, home):
    if CronStore(home).path.exists():
        registry.register(CronTool(home))
