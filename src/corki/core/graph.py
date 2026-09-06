"""Explicit, checkpointable LangGraph coding-agent harness."""

from __future__ import annotations

import asyncio
import json
from contextlib import aclosing, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, cast

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from corki.config import CorkiSettings
from corki.context import ContextBuilder, ContextWindowManager, active_history
from corki.core.model_stream import model_events
from corki.core.state import CorkiState
from corki.evaluation import EvaluationDecision, evaluate_model_step
from corki.memory.repository import MemoryRepository
from corki.models import (
    ModelCompleted,
    ModelError,
    ModelErrorKind,
    ModelPort,
    ModelReasoningDelta,
    ModelRequest,
    ModelRetrying,
    ModelTextDelta,
)
from corki.protocol.events import (
    AssistantMessageCompleted,
    AssistantMessageInterrupted,
    AssistantReasoningDelta,
    AssistantTextDelta,
    ContextCompacted,
    ModelRetryScheduled,
    PlanUpdated,
    RealtimeInputAccepted,
    RuntimeEvent,
    TokenUsageUpdated,
    ToolCallCompleted,
    ToolCallStarted,
    ToolOutputDelta,
)
from corki.protocol.ids import ThreadId, TurnId, stable_tool_result_item_id
from corki.protocol.items import (
    AssistantMessageItem,
    ConversationItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
)
from corki.protocol.memory import MemoryCitationStreamFilter, parse_memory_citation
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolSpec
from corki.realtime import RealtimeController, RealtimeInput, RealtimeStop
from corki.sessions.repository import SessionRepository
from corki.tools import ToolContext, ToolExecutor, ToolRegistry
from corki.tools.discovery import build_tool_plan, current_discovery_history


class EventSink(Protocol):
    async def emit(self, event: RuntimeEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class GraphRunContext:
    events: EventSink
    realtime: RealtimeController = field(default_factory=lambda: RealtimeController(16))


class CorkiGraph:
    """Build the graph while keeping services outside serializable state."""

    def __init__(
        self,
        *,
        settings: CorkiSettings,
        model: ModelPort,
        repository: SessionRepository,
        registry: ToolRegistry,
        executor: ToolExecutor,
        context_builder: ContextBuilder,
        window_manager: ContextWindowManager,
        memory_repository: MemoryRepository | None = None,
    ) -> None:
        self._settings = settings
        self._model = model
        self._repository = repository
        self._registry = registry
        self._executor = executor
        self._context_builder = context_builder
        self._window_manager = window_manager
        self._memory_repository = memory_repository

    def compile(self, *, checkpointer: Any = None) -> Any:
        builder = StateGraph(CorkiState, context_schema=GraphRunContext)
        builder.add_node("prepare_model_context", self._prepare_model_context)
        builder.add_node("call_model", self._call_model)
        builder.add_node("evaluate", self._evaluate)
        builder.add_node("execute_tools", self._execute_tools)
        builder.add_node("finalize", self._finalize)
        builder.add_node("fail", self._fail)
        builder.add_edge(START, "prepare_model_context")
        builder.add_edge("prepare_model_context", "call_model")
        builder.add_conditional_edges(
            "call_model",
            _route_after_model,
            {"steered": "prepare_model_context", "sampled": "evaluate"},
        )
        builder.add_conditional_edges(
            "evaluate",
            _route_after_evaluation,
            {
                EvaluationDecision.EXECUTE_TOOLS.value: "execute_tools",
                EvaluationDecision.CONTINUE.value: "prepare_model_context",
                EvaluationDecision.FINALIZE.value: "finalize",
                EvaluationDecision.FAIL.value: "fail",
            },
        )
        builder.add_edge("execute_tools", "prepare_model_context")
        builder.add_conditional_edges(
            "finalize",
            _route_after_evaluation,
            {
                EvaluationDecision.FINALIZE.value: END,
                EvaluationDecision.CONTINUE.value: "prepare_model_context",
                EvaluationDecision.FAIL.value: "fail",
            },
        )
        builder.add_edge("fail", END)
        return builder.compile(name="corki-agent", checkpointer=checkpointer)

    async def _prepare_model_context(
        self, state: CorkiState, runtime: Runtime[GraphRunContext]
    ) -> dict[str, object]:
        realtime_active = state.get("realtime_active", False) and runtime.context.realtime.active
        latest_input = state["user_input"]
        request_tools = self._registry.model_visible_specs()
        # Persist the state's own pending input before accepting newer steering
        # so rapid input cannot overtake the original turn-start message.
        snapshot = await self._context_builder.build(
            cwd=Path(state["cwd"]),
            turn_id=state["turn_id"],
            user_input=latest_input,
            realtime_active=realtime_active,
        )
        prepared = await self._window_manager.prepare(
            thread_id=state["thread_id"],
            turn_id=state["turn_id"],
            snapshot=snapshot,
            tools=request_tools,
            pending_items=state.get("pending_input_items", ()),
            tool_resolver=self._tools_for_history,
        )
        if prepared.compacted:
            await runtime.context.events.emit(
                ContextCompacted(state["thread_id"], state["turn_id"], prepared.estimated_tokens)
            )
        realtime_update = await self._accept_pending_realtime(state, runtime)
        if "user_input" in realtime_update:
            latest_input = str(realtime_update["user_input"])
            snapshot = await self._context_builder.build(
                cwd=Path(state["cwd"]),
                turn_id=state["turn_id"],
                user_input=latest_input,
                realtime_active=realtime_active,
            )
            prepared = await self._window_manager.prepare(
                thread_id=state["thread_id"],
                turn_id=state["turn_id"],
                snapshot=snapshot,
                tools=request_tools,
                tool_resolver=self._tools_for_history,
            )
            if prepared.compacted:
                await runtime.context.events.emit(
                    ContextCompacted(
                        state["thread_id"], state["turn_id"], prepared.estimated_tokens
                    )
                )
        tool_plan = build_tool_plan(
            self._registry.specs(), prepared.items, self._settings.tool_search_mode
        )
        return {
            **realtime_update,
            "context_instructions": snapshot.instructions,
            "request_items": current_discovery_history(self._registry.specs(), prepared.items),
            "request_tools": tool_plan.advertised,
            "dispatch_tools": tool_plan.dispatch,
            "pending_input_items": (),
            "status": "running",
            "realtime_active": realtime_active,
            "model_interrupted": False,
        }

    def _tools_for_history(self, items: tuple[ConversationItem, ...]) -> tuple[ToolSpec, ...]:
        return build_tool_plan(
            self._registry.specs(), items, self._settings.tool_search_mode
        ).advertised

    async def _call_model(
        self, state: CorkiState, runtime: Runtime[GraphRunContext]
    ) -> dict[str, object]:
        # Checkpoints created before request-level tool plans were introduced
        # can still resume. New checkpoints always carry the exact frozen tuple.
        request_tools = state.get("request_tools", self._registry.model_visible_specs())
        request = ModelRequest(
            model=self._settings.model,
            instructions=state["context_instructions"],
            context_items=(),
            items=state["request_items"],
            tools=request_tools,
            tool_search_mode=self._settings.tool_search_mode,
        )
        completed = await self._repository.load_model_step(
            state["thread_id"], state["turn_id"], state["step_count"]
        )
        sampled = completed is None
        streamed_parts: list[str] = []
        citation_filter = MemoryCitationStreamFilter()
        if completed is None:
            realtime = runtime.context.realtime if state.get("realtime_active", False) else None
            async with aclosing(model_events(self._model, request, realtime)) as stream:
                async for event in stream:
                    if isinstance(event, RealtimeStop):
                        raise asyncio.CancelledError
                    if isinstance(event, RealtimeInput):
                        await runtime.context.events.emit(
                            AssistantMessageInterrupted(state["thread_id"], state["turn_id"])
                        )
                        await self._persist_realtime_input(state, runtime, event)
                        return {
                            "user_input": event.text,
                            "last_model_items": (),
                            "model_interrupted": True,
                        }
                    if isinstance(event, ModelReasoningDelta):
                        await runtime.context.events.emit(
                            AssistantReasoningDelta(
                                state["thread_id"], state["turn_id"], event.delta
                            )
                        )
                    elif isinstance(event, ModelTextDelta):
                        visible = citation_filter.push(event.delta)
                        if visible:
                            streamed_parts.append(visible)
                            await runtime.context.events.emit(
                                AssistantTextDelta(state["thread_id"], state["turn_id"], visible)
                            )
                    elif isinstance(event, ModelRetrying):
                        await runtime.context.events.emit(
                            ModelRetryScheduled(
                                state["thread_id"],
                                state["turn_id"],
                                event.attempt,
                                event.max_attempts,
                                event.delay_seconds,
                                event.error,
                            )
                        )
                    elif isinstance(event, ModelCompleted):
                        completed = event
        if completed is None:
            raise RuntimeError("model stream ended without a completed response")
        completed, cited_threads, citation_valid = _normalize_memory_citations(completed)
        final_stream_tail = citation_filter.finish(citation_valid=citation_valid)
        if final_stream_tail:
            streamed_parts.append(final_stream_tail)
            await runtime.context.events.emit(
                AssistantTextDelta(state["thread_id"], state["turn_id"], final_stream_tail)
            )
        if self._memory_repository is not None and cited_threads:
            # Usage ranking is helpful but never important enough to fail an
            # otherwise valid user turn when telemetry persistence is degraded.
            with suppress(Exception):
                await self._memory_repository.mark_memories_used(cited_threads)
        _validate_model_items(completed, state["turn_id"])

        if sampled:
            await self._repository.commit_model_step(
                state["thread_id"],
                state["turn_id"],
                state["step_count"],
                completed,
            )
        await runtime.context.events.emit(
            TokenUsageUpdated(
                state["thread_id"],
                state["turn_id"],
                completed.usage.input_tokens,
                completed.usage.output_tokens,
                completed.usage.cached_tokens,
                completed.usage.reasoning_tokens,
            )
        )

        completed_text = "".join(
            item.content for item in completed.items if isinstance(item, AssistantMessageItem)
        )
        streamed_text = "".join(streamed_parts)
        missing_suffix = (
            completed_text[len(streamed_text) :] if completed_text.startswith(streamed_text) else ""
        )
        if completed_text and not streamed_text:
            await runtime.context.events.emit(
                AssistantTextDelta(state["thread_id"], state["turn_id"], completed_text)
            )
        elif missing_suffix:
            await runtime.context.events.emit(
                AssistantTextDelta(state["thread_id"], state["turn_id"], missing_suffix)
            )
        if completed_text:
            await runtime.context.events.emit(
                AssistantMessageCompleted(state["thread_id"], state["turn_id"], completed_text)
            )
        return {
            "last_model_items": completed.items,
            "model_end_turn": completed.end_turn,
            "step_count": state["step_count"] + 1,
            "model_interrupted": False,
        }

    async def _accept_pending_realtime(
        self, state: CorkiState, runtime: Runtime[GraphRunContext]
    ) -> dict[str, object]:
        if not state.get("realtime_active", False) or not runtime.context.realtime.active:
            return {}
        latest: str | None = None
        for command in runtime.context.realtime.take_pending():
            if isinstance(command, RealtimeStop):
                raise asyncio.CancelledError
            latest = command.text
            await self._persist_realtime_input(state, runtime, command)
        return {"user_input": latest} if latest is not None else {}

    async def _persist_realtime_input(
        self,
        state: CorkiState,
        runtime: Runtime[GraphRunContext],
        command: RealtimeInput,
    ) -> None:
        text = command.text
        item = command.item or UserMessageItem(text, state["turn_id"])
        snapshot = await self._context_builder.build(
            cwd=Path(state["cwd"]),
            turn_id=state["turn_id"],
            user_input=text,
            realtime_active=True,
        )
        prepared = await self._window_manager.prepare(
            thread_id=state["thread_id"],
            turn_id=state["turn_id"],
            snapshot=snapshot,
            tools=self._registry.model_visible_specs(),
            pending_items=(item,),
            tool_resolver=self._tools_for_history,
        )
        runtime.context.realtime.acknowledge((item,))
        if prepared.compacted:
            await runtime.context.events.emit(
                ContextCompacted(state["thread_id"], state["turn_id"], prepared.estimated_tokens)
            )
        await runtime.context.events.emit(
            RealtimeInputAccepted(state["thread_id"], state["turn_id"], text)
        )

    async def _evaluate(
        self, state: CorkiState, runtime: Runtime[GraphRunContext]
    ) -> dict[str, object]:
        del runtime
        evaluation = evaluate_model_step(
            state["last_model_items"],
            step_count=state["step_count"],
            tool_call_count=state["tool_call_count"],
            max_steps=self._settings.max_steps,
            max_tool_calls=self._settings.max_tool_calls,
            end_turn=state.get("model_end_turn"),
        )
        return {"route": evaluation.decision.value, "error": evaluation.error}

    async def _execute_tools(
        self, state: CorkiState, runtime: Runtime[GraphRunContext]
    ) -> dict[str, object]:
        calls = tuple(
            item.call for item in state["last_model_items"] if isinstance(item, ToolCallItem)
        )
        if not calls:
            return {"route": EvaluationDecision.FAIL.value, "error": "missing tool calls"}

        results: list[ToolResultItem] = []
        plan = state["plan"]
        request_tools = state.get(
            "dispatch_tools", state.get("request_tools", self._registry.model_visible_specs())
        )
        advertised = {spec.name: spec for spec in request_tools}
        index = 0
        while index < len(calls):
            call = calls[index]
            spec = advertised.get(call.name)
            if spec is not None and spec.concurrency is ToolConcurrency.PARALLEL:
                batch: list[ToolCall] = []
                while index < len(calls):
                    candidate = calls[index]
                    candidate_spec = advertised.get(candidate.name)
                    if (
                        candidate_spec is None
                        or candidate_spec.concurrency is not ToolConcurrency.PARALLEL
                    ):
                        break
                    batch.append(candidate)
                    index += 1
                tasks = [
                    asyncio.create_task(self._execute_one(state, runtime, candidate))
                    for candidate in batch
                ]
                try:
                    batch_results = await asyncio.gather(*tasks)
                finally:
                    # gather propagates a child's failure without cancelling
                    # siblings. No tool may outlive its failed owning node.
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                await self._repository.append_items(state["thread_id"], tuple(batch_results))
                results.extend(batch_results)
            else:
                item = await self._execute_one(state, runtime, call)
                await self._repository.append_items(state["thread_id"], (item,))
                results.append(item)
                index += 1

        for item in results:
            if item.state_update.plan is not None:
                plan = tuple(dict(value) for value in item.state_update.plan)
                await runtime.context.events.emit(
                    PlanUpdated(state["thread_id"], state["turn_id"], plan)
                )
        return {
            "tool_call_count": state["tool_call_count"] + len(calls),
            "plan": plan,
        }

    async def _execute_one(
        self,
        state: CorkiState,
        runtime: Runtime[GraphRunContext],
        call: ToolCall,
    ) -> ToolResultItem:
        preview = call.raw_arguments or json.dumps(dict(call.arguments or {}), ensure_ascii=False)
        await runtime.context.events.emit(
            ToolCallStarted(
                state["thread_id"],
                state["turn_id"],
                call.id,
                call.name,
                preview[:1_000],
            )
        )
        if (
            self._settings.memories_enabled
            and self._memory_repository is not None
            and self._settings.memories_disable_on_external_context
            and call.name.startswith("mcp__")
        ):
            # External context may be unrelated or adversarial.  Mark before
            # execution so even an interrupted tool call cannot later seed
            # cross-thread memory from this thread.
            await self._memory_repository.mark_thread_mode(state["thread_id"], "polluted")
        cached = await self._repository.claim_tool_call(state["thread_id"], state["turn_id"], call)
        request_tools = state.get(
            "dispatch_tools", state.get("request_tools", self._registry.model_visible_specs())
        )
        advertised = {spec.name: spec for spec in request_tools}
        if call.name not in advertised:
            result = self._executor.error(
                call, f"tool was not advertised for this step: {call.name}"
            )
        else:
            result = cached or await self._executor.execute(
                call, ToolContext(cwd=Path(state["cwd"])), spec=advertised[call.name]
            )
        if cached is None:
            await self._repository.complete_tool_call(state["thread_id"], state["turn_id"], result)
        if result.display_content:
            await runtime.context.events.emit(
                ToolOutputDelta(
                    state["thread_id"], state["turn_id"], call.id, result.display_content
                )
            )
        await runtime.context.events.emit(
            ToolCallCompleted(
                state["thread_id"], state["turn_id"], call.id, call.name, result.is_error
            )
        )
        item = ToolResultItem(
            result.call_id,
            result.tool_name,
            result.content,
            state["turn_id"],
            id=stable_tool_result_item_id(state["thread_id"], state["turn_id"], result.call_id),
            is_error=result.is_error,
            display_content=result.display_content,
            attachments=result.attachments,
            state_update=result.state_update,
            discovered_tools=result.discovered_tools,
        )
        return item

    async def _finalize(
        self, state: CorkiState, runtime: Runtime[GraphRunContext]
    ) -> dict[str, object]:
        realtime = runtime.context.realtime
        # A crash can happen after a steering input's durable append but before
        # the continuation edge is checkpointed. Reconcile against the request
        # snapshot, not just the ephemeral queue, before declaring completion.
        seen_inputs = {
            item.id for item in state["request_items"] if isinstance(item, UserMessageItem)
        }
        history = active_history(await self._repository.load_items(state["thread_id"]))
        pending = tuple(
            item
            for item in history
            if isinstance(item, UserMessageItem)
            and item.turn_id == state["turn_id"]
            and item.id not in seen_inputs
        )
        needs_follow_up = bool(pending)
        live = state.get("realtime_active", False) and realtime.active
        if live:
            if realtime.stop_requested:
                raise asyncio.CancelledError
            if not needs_follow_up:
                needs_follow_up = not realtime.finish_if_idle()
        if needs_follow_up:
            evaluation = evaluate_model_step(
                state["last_model_items"],
                step_count=state["step_count"],
                tool_call_count=state["tool_call_count"],
                max_steps=self._settings.max_steps,
                max_tool_calls=self._settings.max_tool_calls,
                end_turn=False,
            )
            update = {"user_input": pending[-1].content} if pending else {}
            if live and evaluation.decision is EvaluationDecision.CONTINUE:
                update.update(await self._accept_pending_realtime(state, runtime))
            return {**update, "route": evaluation.decision.value, "error": evaluation.error}
        answer = "".join(
            item.content
            for item in state["last_model_items"]
            if isinstance(item, AssistantMessageItem)
        )
        return {
            "status": "completed",
            "final_answer": answer,
            "route": EvaluationDecision.FINALIZE.value,
        }

    async def _fail(
        self, state: CorkiState, runtime: Runtime[GraphRunContext]
    ) -> dict[str, object]:
        if state.get("realtime_active", False):
            runtime.context.realtime.close_input()
            if runtime.context.realtime.stop_requested:
                raise asyncio.CancelledError
        return {"status": "failed", "final_answer": None}


def _route_after_evaluation(state: CorkiState) -> str:
    return cast(str, state["route"])


def _route_after_model(state: CorkiState) -> str:
    return "steered" if state.get("model_interrupted", False) else "sampled"


def _validate_model_items(completed: ModelCompleted, turn_id: TurnId) -> None:
    """Protect durable history from a malformed provider adapter result."""

    allowed = (AssistantMessageItem, ReasoningItem, ToolCallItem)
    if any(not isinstance(item, allowed) for item in completed.items):
        raise ModelError(
            "provider adapter returned a non-model conversation item",
            kind=ModelErrorKind.PROTOCOL,
        )
    if any(item.turn_id != turn_id for item in completed.items):
        raise ModelError(
            "provider adapter returned an item for a different turn",
            kind=ModelErrorKind.PROTOCOL,
        )
    step_ids = {item.step_id for item in completed.items}
    if len(step_ids) > 1:
        raise ModelError(
            "provider adapter returned multiple step ids for one response",
            kind=ModelErrorKind.PROTOCOL,
        )
    item_ids = [item.id for item in completed.items]
    if len(item_ids) != len(set(item_ids)):
        raise ModelError(
            "provider adapter returned duplicate conversation item ids",
            kind=ModelErrorKind.PROTOCOL,
        )
    calls = [item.call for item in completed.items if isinstance(item, ToolCallItem)]
    call_ids = [call.id for call in calls]
    if len(call_ids) != len(set(call_ids)):
        raise ModelError(
            "provider adapter returned duplicate tool call ids",
            kind=ModelErrorKind.PROTOCOL,
        )
    if any(not call.name.strip() for call in calls):
        raise ModelError(
            "provider adapter returned a tool call without a name",
            kind=ModelErrorKind.PROTOCOL,
        )


def _normalize_memory_citations(
    completed: ModelCompleted,
) -> tuple[ModelCompleted, tuple[ThreadId, ...], bool]:
    """Strip internal citation blocks and preserve structured provenance."""

    normalized = []
    thread_ids: list[ThreadId] = []
    found = False
    for item in completed.items:
        if not isinstance(item, AssistantMessageItem):
            normalized.append(item)
            continue
        if item.memory_citation is not None:
            found = True
            thread_ids.extend(item.memory_citation.thread_ids)
            normalized.append(item)
            continue
        content, citation = parse_memory_citation(item.content)
        if citation is None:
            normalized.append(item)
            continue
        found = True
        thread_ids.extend(citation.thread_ids)
        normalized.append(replace(item, content=content, memory_citation=citation))
    return (
        replace(completed, items=tuple(normalized)),
        tuple(dict.fromkeys(thread_ids)),
        found,
    )
