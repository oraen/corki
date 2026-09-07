"""Raw input wire adaptation; this module does not execute JavaScript."""

import json
from dataclasses import replace
from typing import Any

from corki.models.base import ModelError
from corki.models.namespaces import request_tool_aliases
from corki.models.tool_search import response_tool_name
from corki.models.types import ModelRequest
from corki.protocol.ids import ToolCallId
from corki.protocol.items import ToolCallItem, ToolResultItem
from corki.protocol.tools import ToolCall


def compatible_call(call: ToolCall, request: ModelRequest) -> ToolCall:
    if request.tool_namespace_mode != "native":
        call = replace(call, name=request_tool_aliases(request).get(call.name, call.name))
    if request.tool_freeform_mode == "native":
        return call
    specs = {
        spec.name: spec
        for item in request.items
        if isinstance(item, ToolResultItem) and not item.is_error
        for spec in item.discovered_tools
    }
    specs.update((spec.name, spec) for spec in request.tools)
    spec = specs.get(call.name)
    if spec is None or spec.input_kind != "freeform":
        return call
    args = call.arguments
    if (
        call.parse_error is None
        and args is not None
        and set(args) == {"input"}
        and isinstance(args["input"], str)
    ):
        return replace(call, arguments=None, raw_arguments=args["input"], input_kind="freeform")
    return replace(
        call,
        arguments=None,
        input_kind="freeform",
        parse_error=call.parse_error or "freeform arguments require exactly one string input",
    )


def compatible_arguments(call: ToolCall) -> str:
    if call.input_kind == "freeform" and call.parse_error is None:
        return json.dumps({"input": call.raw_arguments}, ensure_ascii=False)
    return call.raw_arguments or json.dumps(dict(call.arguments or {}), ensure_ascii=False)


class CustomCalls:
    """Complete items alone authorize execution; deltas only budget pending input."""

    def __init__(self, turn_id, step_id, *, name_aliases=None):
        self.turn_id, self.step_id = turn_id, step_id
        self.name_aliases = name_aliases or {}
        self.aliases: dict[str, str] = {}
        self.sizes: dict[str, int] = {}
        self.finished: dict[str, ToolCallItem] = {}

    @property
    def chars(self) -> int:
        return sum(self.sizes.values())

    def _key(self, item: dict[str, Any], event: dict[str, Any]) -> str:
        aliases = []
        for prefix, value in (
            ("item", item.get("id") or event.get("item_id")),
            ("call", item.get("call_id") or event.get("call_id")),
            ("index", event.get("output_index")),
        ):
            if value is not None:
                aliases.append(f"{prefix}:{value}")
        if not aliases:
            raise ModelError("custom tool item requires an identity")
        existing = {self.aliases[a] for a in aliases if a in self.aliases}
        if len(existing) > 1:
            raise ModelError("custom tool item identities conflict")
        key = next(iter(existing), aliases[0])
        self.aliases.update((a, key) for a in aliases)
        return key

    def added(self, item: dict[str, Any], event: dict[str, Any]) -> None:
        key = self._key(item, event)
        if key in self.finished:
            raise ModelError("custom tool added after completed item")
        value = item.get("input", "")
        if not isinstance(value, str):
            raise ModelError("custom tool input must be a string")
        self.sizes[key] = max(self.sizes.get(key, 0), len(value))

    def delta(self, event: dict[str, Any]) -> None:
        key = self._key({}, event)
        if key in self.finished:
            raise ModelError("custom input delta after completed tool item")
        value = event.get("delta")
        if not isinstance(value, str):
            raise ModelError("custom input delta must be a string")
        self.sizes[key] = self.sizes.get(key, 0) + len(value)

    def complete(self, item: dict[str, Any], event: dict[str, Any]) -> ToolCallItem | None:
        if not isinstance(item.get("input"), str):
            raise ModelError("completed custom tool requires string input")
        if not isinstance(item.get("call_id"), str) or not item["call_id"]:
            raise ModelError("completed custom tool requires call_id")
        if not isinstance(item.get("name"), str) or not item["name"]:
            raise ModelError("completed custom tool requires name")
        key = self._key(item, event)
        call = ToolCall(
            ToolCallId(item["call_id"]),
            self.name_aliases.get(response_tool_name(item), response_tool_name(item)),
            None,
            raw_arguments=item["input"],
            input_kind="freeform",
        )
        if key in self.finished:
            if self.finished[key].call != call:
                raise ModelError("completed custom tool item changed")
            return None
        self.sizes[key] = max(self.sizes.get(key, 0), len(item["input"]))
        completed = ToolCallItem(call, self.turn_id, self.step_id)
        self.finished[key] = completed
        return completed
