"""Complete/deduplicate hosted items at the Responses stream boundary."""

import json

from corki.models.base import ModelError
from corki.protocol.hosted import is_hosted_tool_payload
from corki.protocol.items import HostedToolItem


class HostedItems:
    def __init__(self, turn_id, step_id):
        self.turn_id, self.step_id = turn_id, step_id
        self.completed: dict[str, HostedToolItem] = {}
        self.chars = 0

    def complete(self, payload, event):
        if not is_hosted_tool_payload(payload):
            return None
        key = str(payload.get("id") or event.get("output_index", len(self.completed)))
        try:
            value = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            item = HostedToolItem(value, self.turn_id, self.step_id)
        except (TypeError, ValueError) as exc:
            raise ModelError(f"invalid hosted tool item: {exc}") from exc
        previous = self.completed.get(key)
        if previous is not None:
            if previous.payload_json != value:
                raise ModelError("completed hosted tool item changed")
            return None
        self.completed[key] = item
        self.chars += len(value)
        return item
