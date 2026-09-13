"""Standalone compaction node using the same owned, checkpointed Turn lifecycle."""

from langgraph.runtime import Runtime

from corki.core.state import CorkiState
from corki.prompting import PromptStore
from corki.protocol.events import ContextCompacted, ContextCompactionStarted, WarningEvent


async def compact_node(
    state: CorkiState,
    runtime: Runtime,
    *,
    window,
    context_builder,
    code_mode,
    retry_callback,
    registry,
    refresh_tools=None,
    tool_plan=None,
    tool_inventory=None,
    make_tool_snapshot=None,
    on_compact=None,
):
    if code_mode is not None:
        code_mode.pause()
    thread, turn = state["thread_id"], state["turn_id"]
    await runtime.context.events.emit(ContextCompactionStarted(thread, turn))
    prepared = await window.compact(
        thread_id=thread,
        turn_id=turn,
        instructions=state.get("turn_base_instructions", context_builder.base_instructions()),
        on_retry=retry_callback(state, runtime),
        on_compact=on_compact,
    )
    await runtime.context.events.emit(ContextCompacted(thread, turn, prepared.estimated_tokens))
    await runtime.context.events.emit(
        WarningEvent(thread, turn, PromptStore().render("context/compaction_warning").strip())
    )
    return {"status": "completed", "final_answer": "", "request_items": prepared.items}


def route_at_start(state: CorkiState) -> str:
    operation = state.get("operation", "normal")
    if operation == "compact":
        return "compact"
    if operation == "normal":
        return "prepare_model_context"
    raise ValueError(f"unknown turn operation: {operation}")
