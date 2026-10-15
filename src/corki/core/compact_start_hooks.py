"""Installed compaction sources, separate from execution-time hook selection."""

from corki.core.start_hooks import run
from corki.protocol.ids import TurnId
from corki.protocol.items import CompactionItem
from corki.sessions.models import TurnStatus


class StartHookStopped(Exception):
    """Normal turn completion requested by a start hook, not cancellation."""


def _value(saved, fields):
    value, records = saved
    if (
        records
        or set(value) != {"version", *fields}
        or type(value.get("version")) is not int
        or value["version"] != 1
    ):
        raise ValueError("Invalid compaction start source record")
    return value


async def queue(repository, thread, turn, marker):
    await repository.save_hook_batch(
        thread, turn, "compact_start_source:" + marker, {"version": 1, "marker": marker}
    )


async def consume(owner, *, state, runtime, repository, settings, shell, mcp_manager):
    thread, turn = state["thread_id"], state["turn_id"]
    # Walking installed history both orders sources and excludes failed installs.
    for marker in await repository.load_items(thread):
        if not isinstance(marker, CompactionItem):
            continue
        key = "compact_start_source:" + str(marker.id)
        saved = await repository.load_hook_batch(thread, marker.turn_id, key)
        if saved is None:
            continue  # Legacy compactions never acquire new effects retroactively.
        if _value(saved, {"marker"})["marker"] != str(marker.id):
            raise ValueError("Compaction start marker mismatch")
        consumer = await repository.load_hook_batch(thread, marker.turn_id, key + ":consumer")
        if consumer is None:
            consumer_turn = turn
            await repository.save_hook_batch(
                thread, marker.turn_id, key + ":consumer", {"version": 1, "turn": str(turn)}
            )
        else:
            value = _value(consumer, {"turn"})
            if not isinstance(value["turn"], str) or not value["turn"]:
                raise ValueError("Invalid compaction start consumer")
            consumer_turn = TurnId(value["turn"])
        if consumer_turn != turn:
            if consumer_turn in runtime.terminal_pending_turns:
                # This Runtime already owns the terminal result, even while
                # SQLite still says RUNNING. Do not transfer its dequeued hook
                # to a new Turn or pretend its unknown execution completed.
                continue
            status = await repository.load_turn_status(thread, consumer_turn)
            if status is None:
                raise ValueError("Missing compaction start consumer Turn")
            if status in {TurnStatus.COMPLETED, TurnStatus.CANCELLED, TurnStatus.FAILED}:
                # The source was dequeued by its original Turn. Preserve an
                # unknown execution as unknown, but do not transfer that old
                # obligation to a new Turn after its owner has terminated.
                continue
        done = await repository.load_hook_batch(thread, marker.turn_id, key + ":done")
        if done is not None:
            value = _value(done, {"stopped"})
            if type(value["stopped"]) is not bool:
                raise ValueError("Invalid compaction start result")
            if value["stopped"] and consumer_turn == turn:
                raise StartHookStopped
            continue
        stopped = await run(
            owner,
            state={**state, "turn_id": consumer_turn},
            runtime=runtime,
            repository=repository,
            settings=settings,
            shell=shell,
            mcp_manager=mcp_manager,
            operation=str(marker.id),
        )
        await repository.save_hook_batch(
            thread, marker.turn_id, key + ":done", {"version": 1, "stopped": stopped}
        )
        if stopped:
            raise StartHookStopped


async def allows_input(repository, thread, turn):
    for source_turn, key in await repository.load_hook_batch_keys(thread, "compact_start_source:"):
        if not key.endswith(":consumer"):
            continue
        consumer = _value(await repository.load_hook_batch(thread, source_turn, key), {"turn"})
        if consumer["turn"] != str(turn):
            continue
        done = await repository.load_hook_batch(
            thread, source_turn, key.removesuffix(":consumer") + ":done"
        )
        if done is None:
            return False
        value = _value(done, {"stopped"})
        if type(value["stopped"]) is not bool:
            raise ValueError("Invalid compaction start result")
        if value["stopped"]:
            return False
    return True
