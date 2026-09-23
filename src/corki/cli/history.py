"""Local conversation replay is rendering, never tool execution or model sampling."""

from corki.cli.history_projection import HistoryProjection
from corki.models.error_safety import display_model_error
from corki.protocol.items import (
    AssistantMessageItem,
    ReasoningItem,
    RemoteHistoryItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
)
from corki.protocol.proposed_plan import split_proposed_plan
from corki.protocol.response_body import response_body_payload
from corki.protocol.tools import TextContent
from corki.protocol.wire_numbers import dumps_wire
from corki.sessions.models import DisplayHistory, TurnStatus


def _visible_items(items):
    return HistoryProjection().feed(items)


def replay_history(ui, history):
    items = tuple(_visible_items(history.items if isinstance(history, DisplayHistory) else history))
    terminals = (
        {turn.id: turn for turn in history.turns} if isinstance(history, DisplayHistory) else {}
    )
    last = {item.turn_id: index for index, item in enumerate(items)}
    interrupted = {item.turn_id for item in items if isinstance(item, TurnAbortedItem)}
    order = {turn_id: index for index, turn_id in enumerate(terminals)}
    empty_turns = [turn_id for turn_id in terminals if turn_id not in last]
    pending_tools = {}

    def close_tools(turn_id):
        for name in pending_tools.pop(turn_id, {}).values():
            ui.show_notice(f"{name} interrupted; completion not confirmed.")

    def finish(turn):
        if turn.status in {TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED}:
            close_tools(turn.id)
        if turn.status is TurnStatus.FAILED:
            key = getattr(getattr(ui, "_settings", None), "api_key", None)
            ui.show_assistant_message(
                display_model_error(turn.error or "Turn failed.", key), is_error=True
            )
        elif turn.status is TurnStatus.CANCELLED and turn.id not in interrupted:
            ui.show_notice("Turn interrupted.")

    for index, item in enumerate(items):
        while empty_turns and order[empty_turns[0]] < order.get(item.turn_id, -1):
            finish(terminals.pop(empty_turns.pop(0)))
        if isinstance(item, ToolCallItem):
            pending_tools.setdefault(item.turn_id, {})[item.call.id] = item.call.name
        elif isinstance(item, ToolResultItem):
            pending_tools.get(item.turn_id, {}).pop(item.call_id, None)
        elif isinstance(item, TurnAbortedItem):
            close_tools(item.turn_id)
        turn = terminals.get(item.turn_id)
        _replay_items(
            ui, (item,), collaboration_mode=turn.collaboration_mode if turn else "default"
        )
        if last[item.turn_id] == index and item.turn_id in terminals:
            finish(terminals.pop(item.turn_id))
    # Legacy or initialization-failed Turns may have no conversation items.
    for turn in terminals.values():
        finish(turn)


def _replay_items(ui, items, *, collaboration_mode="default"):
    for item in items:
        if isinstance(item, UserMessageItem):
            if item.retained_from_id is not None:
                continue
            text = item.content or "\n".join(
                part.text for part in item.content_items if isinstance(part, TextContent)
            )
            ui._show_submitted_input(text)
            if item.attachments or any(
                not isinstance(part, TextContent) for part in item.content_items
            ):
                ui.show_notice("[Historical message contains attachments]")
        elif isinstance(item, RemoteHistoryItem):
            payload = item.payload
            if payload["type"] != "message":
                continue
            text = "\n".join(
                part["text"]
                for part in payload["content"]
                if part["type"] in {"input_text", "output_text"}
            )
            if payload["role"] == "user":
                ui._show_submitted_input(text)
            else:
                ui.show_assistant_message(text)
            if any(part["type"] in {"input_image", "input_audio"} for part in payload["content"]):
                ui.show_notice("[Historical message contains attachments]")
        elif isinstance(item, AssistantMessageItem):
            if collaboration_mode == "plan":
                visible, plan = split_proposed_plan(item.content)
                if visible.strip():
                    ui.show_assistant_message(visible)
                if plan is not None:
                    ui.show_proposed_plan(plan)
            else:
                ui.show_assistant_message(item.content)
        elif isinstance(item, ReasoningItem):
            summary = item.summary
            if summary is None:
                # Older writers retained the typed provider body but omitted
                # the display field. Never guess from raw/opaque content.
                body = response_body_payload(item.response_body_json, "reasoning")
                if body is not None:
                    summary = "\n\n".join(part["text"] for part in body["summary"])
            if summary:
                ui.begin_reasoning()
                ui.append_reasoning_delta(summary)
                ui.end_reasoning()
        elif isinstance(item, ToolCallItem):
            ui.show_tool_started(
                item.call.name, item.call.raw_arguments or dumps_wire(item.call.arguments)
            )
        elif isinstance(item, ToolResultItem):
            output = (
                item.display_content if item.display_content is not None else item.content[:4000]
            )
            if output:
                ui.show_tool_output(output)
            ui.show_tool_completed(item.tool_name, is_error=item.is_error)
            if item.state_update.plan is not None:
                ui.show_plan(item.state_update.plan, explanation=item.state_update.plan_explanation)
        elif isinstance(item, TurnAbortedItem):
            ui.show_notice("Turn interrupted.")
