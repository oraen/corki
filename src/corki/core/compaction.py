"""Standalone compaction node using the same owned, checkpointed Turn lifecycle."""

from pathlib import Path

from langgraph.runtime import Runtime

from corki.core.state import CorkiState
from corki.prompting import PromptStore
from corki.protocol.events import ContextCompacted, ContextCompactionStarted, WarningEvent


async def compact_node(
    state: CorkiState, runtime: Runtime, *, window, context_builder, code_mode, retry_callback
):
    if code_mode is not None:
        code_mode.pause()
    thread, turn = state["thread_id"], state["turn_id"]
    await runtime.context.events.emit(ContextCompactionStarted(thread, turn))
    options = {}
    if window.token_budget_enabled:
        options["snapshot"] = await context_builder.build(
            cwd=Path(state["cwd"]),
            turn_id=turn,
            include_input_context=False,
        )
    prepared = await window.compact(
        thread_id=thread,
        turn_id=turn,
        instructions=context_builder.base_instructions(),
        on_retry=retry_callback(state, runtime),
        **options,
    )
    await runtime.context.events.emit(ContextCompacted(thread, turn, prepared.estimated_tokens))
    if not window.token_budget_enabled:
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
