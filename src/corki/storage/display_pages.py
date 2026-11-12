"""Bound payload decoding for reverse navigation over append-only local history."""

import json

from corki.protocol.ids import TurnId
from corki.protocol.items import item_from_payload
from corki.protocol.settings import ModelSettingsSnapshot
from corki.sessions.display import (
    DisplayItemsCursor,
    DisplayItemsPage,
    DisplayTurnsCursor,
    DisplayTurnsPage,
)
from corki.sessions.models import DisplayTurn, TurnStatus

MAX_DISPLAY_PAGE_ITEMS = 200


def display_turn_from_row(row):
    """Project the admitted mode, never the thread's mutable current default."""
    payload = row["model_settings_json"]
    mode = (
        ModelSettingsSnapshot.from_payload(json.loads(payload)).collaboration_mode
        if payload is not None
        else "default"
    )
    return DisplayTurn(TurnId(row["id"]), TurnStatus(row["status"]), row["error"], mode)


def read_display_items_page(connect, thread_id, cursor, limit):
    if type(limit) is not int or not 1 <= limit <= MAX_DISPLAY_PAGE_ITEMS:
        raise ValueError(f"display page limit must be between 1 and {MAX_DISPLAY_PAGE_ITEMS}")
    if cursor is not None and (
        not isinstance(cursor, DisplayItemsCursor) or cursor.thread_id != thread_id
    ):
        raise ValueError("display cursor belongs to a different thread or is invalid")
    with connect() as connection:
        connection.execute("BEGIN")
        table = display_table(connection)
        args = [str(thread_id)]
        boundary = ""
        if cursor is not None:
            boundary = " AND sequence<?"
            args.append(cursor.before_sequence)
        rows = connection.execute(
            f"SELECT sequence,kind,payload_json FROM {table} WHERE thread_id=?"
            + boundary
            + " ORDER BY sequence DESC LIMIT ?",
            (*args, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        if not rows:
            return DisplayItemsPage((), None)
        rows.reverse()
        return DisplayItemsPage(
            tuple(item_from_payload(row["kind"], json.loads(row["payload_json"])) for row in rows),
            DisplayItemsCursor(thread_id, rows[0]["sequence"]) if has_more else None,
            newest_sequence=rows[-1]["sequence"],
        )


def contains_display_item(connect, thread_id, kind, identity, through_sequence):
    if type(through_sequence) is not int or not -1 <= through_sequence < 2**63:
        raise ValueError("invalid display snapshot boundary")
    fields = {
        "assistant_message": ("assistant_message", "id"),
        "reasoning": ("reasoning", "id"),
        "tool_call": ("tool_call", "json_extract(payload_json,'$.call.id')"),
        "tool_result": ("tool_result", "json_extract(payload_json,'$.call_id')"),
        "assistant_turn": ("assistant_message", "turn_id"),
    }
    if kind not in fields:
        raise ValueError("invalid display identity kind")
    stored_kind, field = fields[kind]
    with connect() as connection:
        table = display_table(connection)
        return (
            connection.execute(
                f"SELECT 1 FROM {table} WHERE thread_id=? AND kind=? "
                f"AND sequence<=? AND {field}=? LIMIT 1",
                (str(thread_id), stored_kind, through_sequence, str(identity)),
            ).fetchone()
            is not None
        )


def read_display_turns_page(connect, thread_id, cursor, limit):
    if type(limit) is not int or not 1 <= limit <= MAX_DISPLAY_PAGE_ITEMS:
        raise ValueError(f"display page limit must be between 1 and {MAX_DISPLAY_PAGE_ITEMS}")
    if cursor is not None and (
        not isinstance(cursor, DisplayTurnsCursor) or cursor.thread_id != thread_id
    ):
        raise ValueError("display turn cursor belongs to a different thread or is invalid")
    with connect() as connection:
        connection.execute("BEGIN")
        query = "SELECT id,status,error,model_settings_json FROM chat_turns WHERE thread_id=?"
        args = [str(thread_id)]
        if cursor is not None:
            boundary = connection.execute(
                "SELECT rowid FROM chat_turns WHERE thread_id=? AND id=?",
                (str(thread_id), str(cursor.before_turn_id)),
            ).fetchone()
            if boundary is None:
                raise ValueError("display turn cursor boundary no longer exists")
            query += " AND rowid<?"
            args.append(boundary[0])
        query += " ORDER BY rowid DESC LIMIT ?"
        args.append(limit + 1)
        rows = connection.execute(query, args).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()
        return DisplayTurnsPage(
            tuple(display_turn_from_row(row) for row in rows),
            DisplayTurnsCursor(thread_id, TurnId(rows[0]["id"])) if has_more else None,
        )


def display_table(connection):
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_history'"
    ).fetchone():
        raise ValueError("聊天历史尚未迁移，请先启动新版 Corki 完成初始化")
    return "chat_history"
