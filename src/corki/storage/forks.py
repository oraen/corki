"""Atomic copied history forks; execution ledgers belong only to their source."""

import json
from dataclasses import replace
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from corki.protocol.items import (
    CompactionItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
    item_from_payload,
)
from corki.sessions.fork import ForkSnapshot
from corki.storage.usage import inherit_usage, read_usage_facts


def validate_fork(source, before, start_source=None, source_repository=None):
    if source is not None and (not isinstance(source, str) or not source):
        raise ValueError("fork source must be a nonempty thread id")
    if before is not None and (type(before) is not int or before < 0 or source is None):
        raise ValueError("fork boundary requires a source and non-negative integer")
    if source is not None and start_source is not None:
        raise ValueError("fork and explicit session_start_source are mutually exclusive")
    if source_repository is not None and (
        source is None or not callable(getattr(source_repository, "load_fork_snapshot", None))
    ):
        raise ValueError("fork source repository requires a source and snapshot reader")


def read_fork_snapshot(connection, source):
    """Caller owns one read transaction for items, turns and usage together."""
    if not connection.execute("SELECT 1 FROM threads WHERE id=?", (str(source),)).fetchone():
        raise LookupError(f"fork source does not exist: {source}")
    base = connection.execute(
        "SELECT model,instructions,provenance FROM thread_base_instructions WHERE thread_id=?",
        (str(source),),
    ).fetchone()
    return ForkSnapshot(
        source,
        tuple(
            item_from_payload(row["kind"], json.loads(row["payload_json"]))
            for row in connection.execute(
                "SELECT kind,payload_json FROM conversation_items "
                "WHERE thread_id=? ORDER BY sequence",
                (str(source),),
            )
        ),
        tuple(
            MappingProxyType(dict(row))
            for row in connection.execute(
                "SELECT * FROM turns WHERE thread_id=? ORDER BY rowid", (str(source),)
            )
        ),
        read_usage_facts(connection, source),
        base_instructions=(base["model"], base["instructions"]) if base is not None else None,
        base_instructions_provenance=base["provenance"] if base is not None else None,
    )


def publish_fork(
    repository, target, cwd, memory_mode, session_id, session_source, source, before, snapshot=None
):
    """Read and publish in one transaction; a committed retry reuses its snapshot."""
    validate_fork(source, before)
    if source == target:
        raise ValueError("fork requires a different target thread")
    if snapshot is not None and (
        not isinstance(snapshot, ForkSnapshot) or snapshot.source_thread_id != source
    ):
        raise ValueError("fork snapshot does not match source")
    request = json.dumps(
        [
            str(source),
            before,
            str(cwd.resolve()),
            memory_mode.value,
            str(session_id),
            session_source.storage_value,
        ],
        separators=(",", ":"),
    )
    with repository._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        receipt = connection.execute(
            "SELECT request_json FROM thread_forks WHERE thread_id=?", (str(target),)
        ).fetchone()
        if receipt is not None:
            if receipt[0] != request:
                raise ValueError("fork request differs from committed snapshot")
            return
        if connection.execute("SELECT 1 FROM threads WHERE id=?", (str(target),)).fetchone():
            raise ValueError("fork requires a new target thread")
        snapshot = snapshot if snapshot is not None else read_fork_snapshot(connection, source)
        items, turns = snapshot.items, snapshot.turns
        active = {row["id"] for row in turns if row["status"] in {"created", "running"}}
        positions = [
            i
            for i, item in enumerate(items)
            if isinstance(item, UserMessageItem) and item.retained_from_id is None
        ]
        legacy_turn = None
        if positions:
            last_user = positions[-1]
            candidate = items[last_user].turn_id
            if candidate not in {row["id"] for row in turns} and not any(
                isinstance(item, TurnAbortedItem) for item in items[last_user + 1 :]
            ):
                legacy_turn = candidate
        cut = len(items)
        if before is not None:
            if before < len(positions):
                cut = positions[before]
            elif active:
                cut = next((i for i, item in enumerate(items) if item.turn_id in active), cut)
            elif legacy_turn is not None:
                cut = positions[-1]
        # Never publish half of a compaction replacement group.
        for i, item in enumerate(items):
            if isinstance(item, CompactionItem):
                if i + item.replacement_item_count >= len(items):
                    raise ValueError("fork source has an incomplete compaction replacement")
                if i < cut <= i + item.replacement_item_count:
                    cut = i
        selected = items[:cut]
        # Later compaction copies can refer to already completed earlier turns.
        # Only the turn at the actual cut boundary lost its completion suffix.
        truncated_turns = (
            {item.turn_id for item in selected} & {items[cut].turn_id}
            if cut < len(items)
            else set()
        )

        def identity(kind, value):
            return str(uuid5(NAMESPACE_URL, f"corki:fork:{target}:{kind}:{value}"))

        mapped = []
        for item in selected:
            fields = {"id": identity("item", item.id), "turn_id": identity("turn", item.turn_id)}
            if hasattr(item, "step_id"):
                fields["step_id"] = identity("step", item.step_id)
            if isinstance(item, UserMessageItem) and item.retained_from_id is not None:
                fields["retained_from_id"] = identity("item", item.retained_from_id)
            if isinstance(item, ContextItem):
                for field in ("source_input_id", "message_group_id"):
                    if getattr(item, field) is not None:
                        fields[field] = identity("item", getattr(item, field))
            if isinstance(item, CompactionItem) and item.through_item_id is not None:
                fields["through_item_id"] = identity("item", item.through_item_id)
            if isinstance(item, ToolCallItem):
                fields["call"] = replace(item.call, id=identity("call", item.call.id))
            if isinstance(item, ToolResultItem):
                fields["call_id"] = identity("call", item.call_id)
            mapped.append(replace(item, **fields))
        inherited_turns = {item.turn_id for item in selected}
        if before is None:
            if legacy_turn is not None:
                mapped.append(
                    TurnAbortedItem(
                        "The source turn was interrupted when this conversation was forked.",
                        identity("turn", legacy_turn),
                        id=identity("item", f"interrupt:{legacy_turn}"),
                    )
                )
            for row in turns:
                if row["id"] in active:
                    inherited_turns.add(row["id"])
                    if not any(
                        isinstance(i, TurnAbortedItem) and i.turn_id == row["id"] for i in selected
                    ):
                        mapped.append(
                            TurnAbortedItem(
                                "The source turn was interrupted "
                                "when this conversation was forked.",
                                identity("turn", row["id"]),
                                id=identity("item", f"interrupt:{row['id']}"),
                            )
                        )
        connection.execute(
            "INSERT INTO threads(id,cwd,memory_mode,session_id,source) VALUES (?,?,?,?,?)",
            (
                str(target),
                str(cwd.resolve()),
                memory_mode.value,
                str(session_id),
                session_source.storage_value,
            ),
        )
        if snapshot.base_instructions is not None:
            connection.execute(
                "INSERT INTO thread_base_instructions(thread_id,model,instructions,provenance) "
                "VALUES (?,?,?,?)",
                (str(target), *snapshot.base_instructions, snapshot.base_instructions_provenance),
            )
        for row in turns:
            if row["id"] not in inherited_turns:
                continue
            connection.execute(
                "INSERT INTO turns(id,thread_id,status,user_input,final_answer,error,operation,"
                "model_settings_json,base_instructions) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identity("turn", row["id"]),
                    str(target),
                    "cancelled" if row["id"] in active | truncated_turns else row["status"],
                    next(
                        (
                            item.content
                            for item in selected
                            if isinstance(item, UserMessageItem)
                            and item.turn_id == row["id"]
                            and item.retained_from_id is None
                        ),
                        "",
                    )
                    if row["id"] in truncated_turns
                    else row["user_input"],
                    None if row["id"] in active | truncated_turns else row["final_answer"],
                    None if row["id"] in active | truncated_turns else row["error"],
                    row["operation"],
                    row["model_settings_json"],
                    row.get("base_instructions"),
                ),
            )
        repository._append_items_in_connection(connection, target, tuple(mapped))
        inherit_usage(
            connection,
            source,
            target,
            selected,
            identity,
            complete=cut == len(items),
            facts=snapshot.usage,
        )
        connection.execute(
            "INSERT INTO thread_forks(thread_id,source_thread_id,request_json) VALUES (?,?,?)",
            (str(target), str(source), request),
        )
