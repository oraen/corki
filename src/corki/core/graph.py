"""Explicit, checkpointable LangGraph coding-agent harness."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import aclosing, suppress
from copy import deepcopy
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any, Protocol, cast, overload

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from corki.code_mode.service import CellEventSink
from corki.config import CorkiSettings
from corki.context import ContextBuilder, ContextWindowManager, active_history
from corki.core.compaction import compact_node, route_at_start
from corki.core.live_tools import LiveTools
from corki.core.model_stream import model_events
from corki.core.output import ModelOutput
from corki.core.retry import retry_delay, wait_retry
from corki.core.state import CorkiState
from corki.core.step_tools import StepToolState
from corki.evaluation import EvaluationDecision, evaluate_model_step
from corki.memory.pollution import has_external_context, mark_polluted
from corki.memory.repository import MemoryRepository
from corki.models import (
    ModelCompleted,
    ModelError,
    ModelErrorKind,
    ModelItemCompleted,
    ModelPort,
    ModelReasoningDelta,
    ModelRequest,
    ModelRetrying,
    ModelTextDelta,
)
from corki.models.failure import ModelFailure
from corki.protocol.events import (
    AssistantMessageInterrupted,
    AssistantReasoningDelta,
    ContextCompacted,
    ModelRetryScheduled,
    PlanUpdated,
    RealtimeInputAccepted,
    RuntimeEvent,
    TokenUsageUpdated,
    ToolCallCompleted,
    ToolCallStarted,
    ToolOutputDelta,
    WarningEvent,
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
from corki.protocol.memory import parse_memory_citation
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.realtime import RealtimeController, RealtimeInput, RealtimeStop
from corki.sessions.repository import SessionRepository
from corki.tools import ToolContext, ToolExecutor, ToolRegistry
from corki.tools.discovery import build_tool_plan, current_discovery_history
from corki.tools.errors import FatalToolError
from corki.tools.registry import ToolRegistrySnapshot


class EventSink(Protocol):
    async def emit(self, event: RuntimeEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class GraphRunContext:
    events: EventSink
    realtime: RealtimeController = field(default_factory=lambda: RealtimeController(16))
    tools: StepToolState = field(default_factory=StepToolState)
    catalog_warnings: set[str] = field(default_factory=set)


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
        code_mode=None,
        media_preparation=None,
        refresh_tools=None,
    ) -> None:
        self._settings = settings
        self._model = model
        self._repository = repository
        self._registry = registry
        self._executor = executor
        self._context_builder = context_builder
        self._window_manager = window_manager
        self._memory_repository = memory_repository
        self._code_mode = code_mode
        self._media = media_preparation
        self._refresh_tools = refresh_tools

    def _tool_plan(self, specs, items):
        return build_tool_plan(
            specs,
            items,
            self._settings.tool_search_mode,
            tool_mode=self._settings.tool_mode if self._code_mode is not None else "direct",
        )

    def compile(self, *, checkpointer: Any = None) -> Any:
        builder = StateGraph(CorkiState, context_schema=GraphRunContext)
        builder.add_node("prepare_model_context", self._prepare_model_context)
        builder.add_node("call_model", self._call_model)
        builder.add_node("retry_model", self._retry_model)
        builder.add_node("evaluate", self._evaluate)
        builder.add_node("execute_tools", self._execute_tools)
        builder.add_node("finalize", self._finalize)
        builder.add_node("fail", self._fail)
        builder.add_node(
            "compact",
            partial(
                compact_node,
                window=self._window_manager,
                context_builder=self._context_builder,
                code_mode=self._code_mode,
                retry_callback=self._compaction_retry_callback,
            ),
        )
        builder.add_conditional_edges(
            START,
            route_at_start,
            {"compact": "compact", "prepare_model_context": "prepare_model_context"},
        )
        builder.add_edge("compact", END)
        builder.add_edge("prepare_model_context", "call_model")
        builder.add_conditional_edges(
            "call_model",
            _route_after_model,
            {"steered": "prepare_model_context", "sampled": "evaluate", "retry": "retry_model"},
        )
        builder.add_edge("retry_model", "call_model")
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
        if self._code_mode is not None:
            self._code_mode.pause()
        if self._refresh_tools is not None:
            await self._refresh_tools()
        tool_snapshot = self._registry.snapshot()
        tool_snapshot_id = runtime.context.tools.bind(tool_snapshot)
        specs = tool_snapshot.specs()

        def resolve_tools(items):
            return self._tool_plan(specs, items).advertised

        realtime_active = state.get("realtime_active", False) and runtime.context.realtime.active
        latest_input = state["user_input"]
        request_tools = tuple(spec for spec in specs if spec.exposure.is_model_visible)
        # Persist the state's own pending input before accepting newer steering
        # so rapid input cannot overtake the original turn-start message.
        snapshot = await self._context_builder.build(
            cwd=Path(state["cwd"]),
            turn_id=state["turn_id"],
            # Explicit skill prompts belong to the original input, not each
            # subsequent sampling step's world-state refresh.
            user_input=latest_input,
            include_input_context=bool(state.get("pending_input_items")),
            realtime_active=realtime_active,
        )
        await self._emit_context_warnings(state, runtime, snapshot.warnings)
        prepared = await self._window_manager.prepare(
            thread_id=state["thread_id"],
            turn_id=state["turn_id"],
            snapshot=snapshot,
            tools=request_tools,
            pending_items=state.get("pending_input_items", ()),
            tool_resolver=resolve_tools,
            on_retry=self._compaction_retry_callback(state, runtime),
        )
        await self._emit_catalog_warnings(state, runtime, prepared.warnings)
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
                include_input_context=False,
                realtime_active=realtime_active,
            )
            await self._emit_context_warnings(state, runtime, snapshot.warnings)
            prepared = await self._window_manager.prepare(
                thread_id=state["thread_id"],
                turn_id=state["turn_id"],
                snapshot=snapshot,
                tools=request_tools,
                tool_resolver=resolve_tools,
                on_retry=self._compaction_retry_callback(state, runtime),
            )
            await self._emit_catalog_warnings(state, runtime, prepared.warnings)
            if prepared.compacted:
                await runtime.context.events.emit(
                    ContextCompacted(
                        state["thread_id"], state["turn_id"], prepared.estimated_tokens
                    )
                )
        tool_plan = self._tool_plan(specs, prepared.items)
        return {
            **realtime_update,
            "context_instructions": snapshot.instructions,
            "request_items": current_discovery_history(
                specs, prepared.items, mode=self._settings.tool_search_mode
            ),
            "request_tools": tool_plan.advertised,
            "dispatch_tools": tool_plan.dispatch,
            "tool_snapshot_id": tool_snapshot_id,
            "pending_input_items": (),
            "status": "running",
            "realtime_active": realtime_active,
            "model_interrupted": False,
            "model_retry_pending": False,
            "model_retry_count": 0,
            "model_connection_retry_count": 0,
        }

    def _tools_for_history(self, items: tuple[ConversationItem, ...]) -> tuple[ToolSpec, ...]:
        return self._tool_plan(self._registry.specs(), items).advertised

    async def _emit_context_warnings(self, state, runtime, warnings: tuple[str, ...]) -> None:
        for message in warnings:
            await runtime.context.events.emit(
                WarningEvent(state["thread_id"], state["turn_id"], message)
            )

    async def _emit_catalog_warnings(self, state, runtime, warnings: tuple[str, ...]) -> None:
        for message in warnings:
            if message not in runtime.context.catalog_warnings:
                await self._emit_context_warnings(state, runtime, (message,))
                runtime.context.catalog_warnings.add(message)

    def _compaction_retry_callback(self, state, runtime):
        async def notify(event):
            await runtime.context.events.emit(
                ModelRetryScheduled(
                    state["thread_id"],
                    state["turn_id"],
                    event.attempt,
                    event.max_attempts,
                    event.delay_seconds,
                    event.error,
                    purpose="compaction",
                )
            )

        return notify

    async def _call_model(
        self, state: CorkiState, runtime: Runtime[GraphRunContext]
    ) -> dict[str, object]:
        tool_snapshot = runtime.context.tools.resolve(state.get("tool_snapshot_id"), self._registry)
        failure = await self._repository.load_model_failure(
            state["thread_id"], state["turn_id"], _sample_index(state)
        )
        if failure is not None:
            return await self._retry_update(state, runtime, failure)
        live = LiveTools(
            lambda item: self._execute_one(state, runtime, item.call, tool_snapshot=tool_snapshot)
        )
        propagation = self._activate_code_mode(state, runtime, tool_snapshot, live.failure)
        keep_worker = False
        try:
            update = await self._call_model_owned(state, runtime, live)
            results = await live.finish()
            await self._repository.append_items(state["thread_id"], results)
            completed = {item.call_id for item in results}
            keep_worker = not update.get("model_interrupted", False) and any(
                isinstance(item, ToolCallItem) and item.call.id not in completed
                for item in update.get("last_model_items", ())
            )
            return {**update, "streamed_tool_results": results}
        except BaseException as error:
            try:
                if isinstance(error, ModelError):
                    # Codex drains in-flight calls even after a failed model
                    # stream. Transport failure is not tool cancellation.
                    await live.finish()
            finally:
                await live.aclose()
                # Reload rather than trust a local list: cancellation may arrive
                # after a durable journal commit but before submit returns.
                try:
                    partial = await self._repository.load_partial_step(
                        state["thread_id"], state["turn_id"], _sample_index(state)
                    )
                    for item in partial:
                        if isinstance(item, ToolCallItem):
                            result = await self._execute_one(
                                state, runtime, item.call, interrupted=True
                            )
                            await self._repository.append_items(state["thread_id"], (result,))
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Partial tool cleanup failed", exc_info=True
                    )
                    # Never resample when the observation barrier did not commit.
                    if isinstance(error, ModelError):
                        raise
            if isinstance(error, ModelError):
                failure = ModelFailure.from_error(
                    error,
                    state.get("model_retry_count", 0),
                    state.get("model_connection_retry_count", 0),
                )
                await self._repository.save_model_failure(
                    state["thread_id"], state["turn_id"], _sample_index(state), failure
                )
                update = await self._retry_update(state, runtime, failure)
                keep_worker = True
                return update
            raise
        finally:
            try:
                await live.aclose()
            finally:
                if propagation is not None:
                    self._code_mode.failure.remove_done_callback(propagation)
                if self._code_mode is not None and not keep_worker:
                    self._code_mode.pause()

    def _activate_code_mode(self, state, runtime, tool_snapshot, failure=None):
        if self._code_mode is None:
            return None

        async def nested(call, spec):
            nested_runtime = Runtime(
                context=GraphRunContext(
                    events=CellEventSink(runtime.context.events, inactive),
                    realtime=runtime.context.realtime,
                )
            )
            try:
                return await self._execute_one(
                    state, nested_runtime, call, nested_spec=spec, tool_snapshot=tool_snapshot
                )
            except BaseException:
                await self._execute_one(
                    state,
                    nested_runtime,
                    call,
                    interrupted=True,
                    nested_spec=spec,
                    tool_snapshot=tool_snapshot,
                )
                raise

        async def notify(call_id, text):
            item = ToolResultItem(call_id, "exec", text, state["turn_id"], input_kind="freeform")
            await self._repository.append_items(state["thread_id"], (item,))
            await CellEventSink(runtime.context.events, inactive).emit(
                ToolOutputDelta(state["thread_id"], state["turn_id"], call_id, text[:4000])
            )

        propagation = self._code_mode.activate(
            state["turn_id"], nested, notify, failure, registry=tool_snapshot
        )
        # Capture the owning Turn's event lifetime, never a later Turn's sink.
        inactive = self._code_mode.inactive
        return propagation

    async def _retry_update(self, state, runtime, failure: ModelFailure):
        unbounded = (
            failure.kind == ModelErrorKind.CONNECTION.value
            and self._settings.model_unbounded_connection_retries
            and (self._settings.provider_name or "").lower() not in {"bedrock", "amazon_bedrock"}
        )
        if not failure.retryable or (
            not unbounded and failure.retries_used >= self._settings.model_max_retries
        ):
            raise failure.error()
        partial = await self._repository.load_partial_step(
            state["thread_id"], state["turn_id"], _sample_index(state)
        )
        tool_update = {}
        if any(isinstance(item, ToolCallItem) for item in partial):
            history = await self._repository.load_items(state["thread_id"])
            results = tuple(item for item in history if isinstance(item, ToolResultItem))
            tool_update = await self._execute_tools(
                {
                    **state,
                    "last_model_items": partial,
                    "streamed_tool_results": results,
                },
                runtime,
                keep_worker=True,
            )
        await runtime.context.events.emit(
            AssistantMessageInterrupted(state["thread_id"], state["turn_id"], reason="retry")
        )
        return {
            **tool_update,
            "sample_count": _sample_index(state) + 1,
            "model_retry_count": failure.retries_used + (not unbounded),
            "model_connection_retry_count": failure.connection_retries_used + unbounded,
            "model_retry_attempt": failure.connection_retries_used + 1
            if unbounded
            else failure.retries_used + 1,
            "model_retry_limit": None if unbounded else self._settings.model_max_retries,
            "model_retry_pending": True,
            "model_retry_delay": min(5 * (2 ** min(failure.connection_retries_used, 4)), 60)
            if unbounded
            else retry_delay(failure, self._settings.model_retry_base_seconds),
            "model_retry_error": failure.message,
            "model_interrupted": False,
            "last_model_items": (),
            "streamed_tool_results": (),
        }

    async def _retry_model(self, state, runtime):
        identity = (state["thread_id"], state["turn_id"], _sample_index(state))
        # A checkpoint can lag a committed attempt. Its old retry wait has
        # already been consumed; let the model node reconcile durable facts
        # without emitting an obsolete event or sleeping a second time.
        recorded = (
            await self._repository.load_model_failure(*identity) is not None
            or await self._repository.load_model_step(*identity) is not None
            or bool(await self._repository.load_partial_step(*identity))
        )
        if recorded:
            return {"model_retry_pending": False}
        await runtime.context.events.emit(
            ModelRetryScheduled(
                state["thread_id"],
                state["turn_id"],
                state["model_retry_attempt"],
                state["model_retry_limit"],
                state["model_retry_delay"],
                state["model_retry_error"],
            )
        )
        realtime = runtime.context.realtime if state.get("realtime_active", False) else None
        command = await wait_retry(state["model_retry_delay"], realtime)
        if isinstance(command, RealtimeStop):
            raise asyncio.CancelledError
        if isinstance(command, RealtimeInput):
            await self._persist_realtime_input(state, runtime, command)
            return await self._prepare_model_context({**state, "user_input": command.text}, runtime)
        tool_snapshot = runtime.context.tools.resolve(state.get("tool_snapshot_id"), self._registry)
        specs = tool_snapshot.specs()
        items = active_history(await self._repository.load_items(state["thread_id"]))
        plan = self._tool_plan(specs, items)
        return {
            "model_retry_pending": False,
            "request_items": current_discovery_history(
                specs, items, mode=self._settings.tool_search_mode
            ),
            "request_tools": plan.advertised,
            "dispatch_tools": plan.dispatch,
            "tool_snapshot_id": state.get("tool_snapshot_id"),
        }

    async def _call_model_owned(
        self, state: CorkiState, runtime: Runtime[GraphRunContext], live: LiveTools
    ) -> dict[str, object]:
        # Checkpoints created before request-level tool plans were introduced
        # can still resume. New checkpoints always carry the exact frozen tuple.
        tool_snapshot = runtime.context.tools.resolve(state.get("tool_snapshot_id"), self._registry)
        request_tools = state.get("request_tools", tool_snapshot.model_visible_specs())
        request_items = self._window_manager.model_history(state["request_items"])
        if self._media is not None:
            request_items = await self._media.prepare_items(request_items, for_model=True)
        request = ModelRequest(
            model=self._settings.model,
            instructions=state["context_instructions"],
            context_items=(),
            items=request_items,
            tools=request_tools,
            tool_search_mode=self._settings.tool_search_mode,
            harness_managed_retries=True,
            tool_freeform_mode=self._settings.tool_freeform_mode,
            tool_namespace_mode=self._settings.tool_namespace_mode,
            client_metadata=await self._window_manager.client_metadata(
                state["thread_id"], state["turn_id"]
            ),
        )
        completed = await self._repository.load_model_step(
            state["thread_id"], state["turn_id"], _sample_index(state)
        )
        sampled = completed is None
        partial = await self._repository.load_partial_step(
            state["thread_id"], state["turn_id"], _sample_index(state)
        )
        if completed is None and partial:
            # A crashed response is not a successful ModelCompleted. Consume
            # its durable calls via the normal tools node, then sample a new
            # step from the reconciled history instead of replaying old input.
            _validate_model_items(ModelCompleted(partial), state["turn_id"])
            if any(has_external_context(item) for item in partial):
                await self._mark_memory_polluted(state["thread_id"])
            return {
                "last_model_items": partial,
                "model_end_turn": False,
                "step_count": state["step_count"] + 1,
                "sample_count": _sample_index(state) + 1,
                "model_interrupted": False,
            }
        partial_items: list[ConversationItem] = []
        output = ModelOutput(state["thread_id"], state["turn_id"], runtime.context.events.emit)
        recorded_citations: set[ThreadId] = set()
        if completed is None:
            realtime = runtime.context.realtime if state.get("realtime_active", False) else None
            async with aclosing(
                model_events(self._model, request, realtime, live.failure)
            ) as stream:
                async for event in stream:
                    if isinstance(event, RealtimeStop):
                        raise asyncio.CancelledError
                    if isinstance(event, RealtimeInput):
                        tool_update = {}
                        if any(isinstance(item, ToolCallItem) for item in partial_items):
                            results = await live.finish()
                            tool_update = await self._execute_tools(
                                {
                                    **state,
                                    "last_model_items": tuple(partial_items),
                                    "streamed_tool_results": results,
                                },
                                runtime,
                            )
                        await runtime.context.events.emit(
                            AssistantMessageInterrupted(state["thread_id"], state["turn_id"])
                        )
                        await self._persist_realtime_input(state, runtime, event)
                        return {
                            **tool_update,
                            "user_input": event.text,
                            "last_model_items": (),
                            "model_interrupted": True,
                            "step_count": state["step_count"] + bool(partial_items),
                            "sample_count": _sample_index(state) + bool(partial_items),
                        }
                    if isinstance(event, ModelItemCompleted):
                        normalized, item_citations, _ = _normalize_memory_citations(
                            ModelCompleted((deepcopy(event.item),))
                        )
                        item = normalized.items[0]
                        candidate = tuple([*partial_items, item])
                        _validate_model_items(ModelCompleted(candidate), state["turn_id"])
                        evaluation = evaluate_model_step(
                            candidate,
                            step_count=state["step_count"] + 1,
                            tool_call_count=state["tool_call_count"],
                            max_steps=self._settings.max_steps,
                            max_tool_calls=self._settings.max_tool_calls,
                        )
                        if evaluation.decision is EvaluationDecision.FAIL:
                            raise ModelError(evaluation.error or "streamed item exceeds budget")
                        await self._repository.append_partial_item(
                            state["thread_id"], state["turn_id"], _sample_index(state), item
                        )
                        partial_items.append(item)
                        if has_external_context(item):
                            await self._mark_memory_polluted(state["thread_id"])
                        if isinstance(item, AssistantMessageItem):
                            await output.complete(item)
                        if self._memory_repository is not None and item_citations:
                            with suppress(Exception):
                                await self._memory_repository.mark_memories_used(item_citations)
                            recorded_citations.update(item_citations)
                        if isinstance(item, ToolCallItem):
                            specs = state.get("dispatch_tools", request_tools)
                            spec = next(
                                (spec for spec in specs if spec.name == item.call.name), None
                            )
                            live.submit(
                                item,
                                parallel=spec is not None
                                and spec.concurrency is ToolConcurrency.PARALLEL,
                            )
                    elif isinstance(event, ModelReasoningDelta):
                        await runtime.context.events.emit(
                            AssistantReasoningDelta(
                                state["thread_id"], state["turn_id"], event.delta
                            )
                        )
                    elif isinstance(event, ModelTextDelta):
                        await output.delta(event)
                    elif isinstance(event, ModelRetrying):
                        if partial_items:
                            raise ModelError("cannot replay a request after completed output items")
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
            raise ModelError("model stream ended without a completed response", retryable=True)
        completed, cited_threads, _ = _normalize_memory_citations(completed)
        if any(item not in completed.items for item in partial_items):
            raise ModelError("completed response changed or omitted an already completed item")
        cited_threads = tuple(
            thread for thread in cited_threads if thread not in recorded_citations
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
                _sample_index(state),
                completed,
            )
        if any(has_external_context(item) for item in completed.items if item not in partial_items):
            await self._mark_memory_polluted(state["thread_id"])
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

        for item in completed.items:
            if isinstance(item, AssistantMessageItem):
                await output.complete(item)
        return {
            "last_model_items": completed.items,
            "model_end_turn": completed.end_turn,
            "step_count": state["step_count"] + 1,
            "sample_count": _sample_index(state) + 1,
            "model_retry_count": 0,
            "model_connection_retry_count": 0,
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
            # Codex's pending-input path does not repeat turn-start skill
            # injection. The model can still discover/read a mentioned skill.
            user_input=text,
            include_input_context=False,
            realtime_active=True,
        )
        await self._emit_context_warnings(state, runtime, snapshot.warnings)
        prepared = await self._window_manager.prepare(
            thread_id=state["thread_id"],
            turn_id=state["turn_id"],
            snapshot=snapshot,
            tools=self._registry.model_visible_specs(),
            pending_items=(item,),
            tool_resolver=self._tools_for_history,
            on_retry=self._compaction_retry_callback(state, runtime),
        )
        runtime.context.realtime.acknowledge((item,))
        await self._emit_catalog_warnings(state, runtime, prepared.warnings)
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
        if evaluation.decision is EvaluationDecision.CONTINUE:
            await self._window_manager.record_budget_notices(
                state["thread_id"], state["turn_id"], needs_follow_up=True
            )
        return {"route": evaluation.decision.value, "error": evaluation.error}

    async def _execute_tools(
        self, state: CorkiState, runtime: Runtime[GraphRunContext], *, keep_worker: bool = False
    ) -> dict[str, object]:
        streamed = {item.call_id for item in state.get("streamed_tool_results", ())}
        pending = any(
            isinstance(item, ToolCallItem) and item.call.id not in streamed
            for item in state["last_model_items"]
        )
        if self._code_mode is not None and pending and not self._code_mode.active.is_set():
            tool_snapshot = runtime.context.tools.resolve(
                state.get("tool_snapshot_id"), self._registry
            )
            self._activate_code_mode(state, runtime, tool_snapshot)
        try:
            result = await self._execute_tools_owned(state, runtime)
            if not keep_worker:
                await self._window_manager.record_budget_notices(
                    state["thread_id"], state["turn_id"], needs_follow_up=True
                )
            return result
        finally:
            if self._code_mode is not None and not keep_worker:
                self._code_mode.pause()

    async def _execute_tools_owned(
        self, state: CorkiState, runtime: Runtime[GraphRunContext]
    ) -> dict[str, object]:
        calls = tuple(
            item.call for item in state["last_model_items"] if isinstance(item, ToolCallItem)
        )
        if not calls:
            return {"route": EvaluationDecision.FAIL.value, "error": "missing tool calls"}

        results: list[ToolResultItem] = []
        plan = state["plan"]
        tool_snapshot = runtime.context.tools.resolve(state.get("tool_snapshot_id"), self._registry)
        request_tools = state.get(
            "dispatch_tools", state.get("request_tools", tool_snapshot.model_visible_specs())
        )
        advertised = {spec.name: spec for spec in request_tools}
        streamed = {item.call_id: item for item in state.get("streamed_tool_results", ())}
        index = 0
        while index < len(calls):
            call = calls[index]
            if call.id in streamed:
                item = streamed[call.id]
                await self._repository.append_items(state["thread_id"], (item,))
                results.append(item)
                index += 1
                continue
            spec = advertised.get(call.name)
            if spec is not None and spec.concurrency is ToolConcurrency.PARALLEL:
                batch: list[ToolCall] = []
                while index < len(calls):
                    candidate = calls[index]
                    candidate_spec = advertised.get(candidate.name)
                    if (
                        candidate_spec is None
                        or candidate.id in streamed
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

    @overload
    async def _execute_one(
        self,
        state: CorkiState,
        runtime: Runtime[GraphRunContext],
        call: ToolCall,
        *,
        interrupted: bool = False,
        nested_spec: None = None,
    ) -> ToolResultItem: ...

    @overload
    async def _execute_one(
        self,
        state: CorkiState,
        runtime: Runtime[GraphRunContext],
        call: ToolCall,
        *,
        interrupted: bool = False,
        nested_spec: ToolSpec,
    ) -> ToolResult: ...

    async def _execute_one(
        self,
        state: CorkiState,
        runtime: Runtime[GraphRunContext],
        call: ToolCall,
        *,
        interrupted: bool = False,
        nested_spec: ToolSpec | None = None,
        tool_snapshot: ToolRegistrySnapshot | None = None,
    ) -> ToolResultItem | ToolResult:
        if tool_snapshot is None:
            tool_snapshot = runtime.context.tools.resolve(
                state.get("tool_snapshot_id"), self._registry
            )
        preview = call.raw_arguments or json.dumps(dict(call.arguments or {}), ensure_ascii=False)
        if not interrupted:
            await runtime.context.events.emit(
                ToolCallStarted(
                    state["thread_id"],
                    state["turn_id"],
                    call.id,
                    call.name,
                    preview[:1_000],
                )
            )
        cached = await self._repository.claim_tool_call(state["thread_id"], state["turn_id"], call)
        request_tools = state.get(
            "dispatch_tools", state.get("request_tools", tool_snapshot.model_visible_specs())
        )
        advertised = {spec.name: spec for spec in request_tools}
        if interrupted:
            result = cached or self._executor.error(
                call, "Tool execution cancelled before dispatch; not executed."
            )
        elif nested_spec is not None:
            try:
                result = cached or await self._executor.execute(
                    call,
                    ToolContext(
                        cwd=Path(state["cwd"]),
                        on_external_context=lambda: self._mark_memory_polluted(state["thread_id"]),
                        model_output_policy=self._settings.model_context_info(
                            self._settings.model
                        ).truncation_policy,
                        supports_image_input=self._settings.supports_image_input,
                        supports_audio_input=self._settings.supports_audio_input,
                        supports_image_detail_original=self._settings.supports_image_detail_original,
                    ),
                    spec=nested_spec,
                    snapshot=tool_snapshot,
                )
            except FatalToolError as error:
                # The Code Mode host returns dispatch failures to the cell,
                # including Fatal. Commit the actual failure before rejecting
                # the promise; do not enter the cancellation cleanup path.
                result = self._executor.error(call, str(error), spec=nested_spec)
        elif call.name not in advertised:
            result = self._executor.error(
                call, f"tool was not advertised for this step: {call.name}"
            )
        else:
            result = cached or await self._executor.execute(
                call,
                ToolContext(
                    cwd=Path(state["cwd"]),
                    on_external_context=lambda: self._mark_memory_polluted(state["thread_id"]),
                    model_output_policy=self._settings.model_context_info(
                        self._settings.model
                    ).truncation_policy,
                    supports_image_input=self._settings.supports_image_input,
                    supports_audio_input=self._settings.supports_audio_input,
                    supports_image_detail_original=self._settings.supports_image_detail_original,
                ),
                spec=advertised[call.name],
                snapshot=tool_snapshot,
            )
        if cached is None:
            await self._repository.complete_tool_call(state["thread_id"], state["turn_id"], result)
        if result.contains_external_context:
            await self._mark_memory_polluted(state["thread_id"])
        if result.display_content and not interrupted:
            await runtime.context.events.emit(
                ToolOutputDelta(
                    state["thread_id"], state["turn_id"], call.id, result.display_content
                )
            )
        if not interrupted:
            await runtime.context.events.emit(
                ToolCallCompleted(
                    state["thread_id"], state["turn_id"], call.id, call.name, result.is_error
                )
            )
        # A nested call needs the authoritative output, not its lossy history
        # projection. Both paths already passed through the same durable
        # claim/execute/complete and event lifecycle above. Do not duplicate
        # structured data into the model-visible conversation.
        if nested_spec is not None:
            return result
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
            input_kind=call.input_kind,
            content_items=result.content_items,
            contains_external_context=result.contains_external_context,
            fallback_token_limit_override=result.fallback_token_limit_override,
            legacy_output_char_budget=result.legacy_output_char_budget,
            is_tool_search_output=result.is_tool_search_output,
        )
        return item

    async def _mark_memory_polluted(self, thread_id: ThreadId) -> None:
        await mark_polluted(self._memory_repository, self._settings, thread_id)

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
        await self._window_manager.record_budget_notices(
            state["thread_id"], state["turn_id"], needs_follow_up=needs_follow_up
        )
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
        # Codex tracks the last nonempty agent message, not the concatenation
        # of earlier commentary and the terminal answer in the same response.
        answer = next(
            (
                item.content
                for item in reversed(state["last_model_items"])
                if isinstance(item, AssistantMessageItem) and item.content.strip()
            ),
            "",
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
    if state.get("model_retry_pending", False):
        return "retry"
    return "steered" if state.get("model_interrupted", False) else "sampled"


def _sample_index(state: CorkiState) -> int:
    # Old checkpoints used logical step_count directly as the durable index.
    return state.get("sample_count", state["step_count"])


def _validate_model_items(completed: ModelCompleted, turn_id: TurnId) -> None:
    """Protect durable history from a malformed provider adapter result."""

    from corki.protocol.items import HostedToolItem

    allowed = (AssistantMessageItem, ReasoningItem, ToolCallItem, HostedToolItem)
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
