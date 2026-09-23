"""Explicit, checkpointable LangGraph coding-agent harness."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import aclosing, nullcontext, suppress
from copy import copy, deepcopy
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any, Protocol, cast, overload

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from corki.code_mode.service import CellEventSink
from corki.code_mode.tools import CodeModeExecTool, CodeModeWaitTool
from corki.config import CorkiSettings
from corki.context import ContextBuilder, ContextWindowManager, active_history, local_time
from corki.context.input_context import is_input_context
from corki.context.world_state import changed_context_items, snapshot_content
from corki.core.async_prompt_hooks import drain as drain_async_prompt_hooks
from corki.core.compaction import compact_node, route_at_start
from corki.core.live_tools import LiveTools
from corki.core.model_settings import bind_model_settings, capture_model_settings
from corki.core.model_stream import model_events
from corki.core.output import ModelOutput
from corki.core.prompt_hooks import ordered_inputs
from corki.core.retry import retry_delay, wait_retry
from corki.core.state import CorkiState
from corki.core.step_settings import StepSettingsState
from corki.core.step_shell import step_shell
from corki.core.step_tools import StepToolState
from corki.core.stop_hooks import StopDecision, StopHooks
from corki.core.tool_readiness import StepToolReadiness
from corki.core.turn_diff import TurnDiffTracker
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
    ContextItem,
    ConversationItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
)
from corki.protocol.memory import parse_memory_citation
from corki.protocol.response_body import capture_response_body
from corki.protocol.session_source import DEFAULT_SESSION_SOURCE, SessionSource
from corki.protocol.settings import ModelSettingsSnapshot
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.realtime import RealtimeController, RealtimeInput, RealtimeStop
from corki.sessions.repository import SessionRepository
from corki.shell import Shell, default_user_shell
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
    models: StepSettingsState | None = None
    turn_diff: TurnDiffTracker = field(default_factory=TurnDiffTracker)
    request_user_input: Callable | None = None
    is_non_root_agent: bool = False
    session_source: SessionSource = DEFAULT_SESSION_SOURCE
    legacy_base_unknown: bool = False
    # Runtime-owned terminal facts whose durable writes are still pending.
    # Never reconstructed from model history or carried across a cold restart.
    terminal_pending_turns: frozenset[TurnId] = frozenset()
    # Only the latest nested plan is retained to suppress its outer-cell echo.
    nested_plan_updates: list[PlanUpdated] = field(default_factory=list)


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
        tool_router=None,
        media_preparation=None,
        refresh_tools=None,
        refresh_input_tools=None,
        session_shell: Shell | None = None,
        plugins=(),
        mcp_manager=None,
    ) -> None:
        self._settings = settings
        self._mcp_manager = mcp_manager
        self._turn_settings = settings
        self._turn_window = window_manager
        self._session_shell = session_shell or default_user_shell()
        self._model = model
        self._repository = repository
        self._registry = registry
        self._executor = executor
        self._context_builder = context_builder
        self._window_manager = window_manager
        self._memory_repository = memory_repository
        self._code_mode = code_mode
        self._tool_router = tool_router
        self._media = media_preparation
        self._refresh_tools = refresh_tools
        self._refresh_input_tools = refresh_input_tools
        self._stop_hooks = StopHooks()
        from corki.core.async_post_hooks import AsyncPostHooks

        # All asynchronous event types consume the same session-wide budget.
        hook_limiter = self._stop_hooks._async._limit
        self._async_post_hooks = AsyncPostHooks(limiter=hook_limiter)
        self._async_pre_hooks = AsyncPostHooks(event_name="PreToolUse", limiter=hook_limiter)
        self._stop_hooks.publish(
            self._stop_hooks.prepare(
                settings.configuration,
                plugins,
                managed_policy=settings.managed_hook_policy,
                enabled=settings.hooks_enabled,
            )
        )

    def with_context_builder(self, builder: ContextBuilder) -> CorkiGraph:
        graph = copy(self)
        graph._context_builder = builder
        return graph

    def with_model_settings(self, settings: CorkiSettings) -> CorkiGraph:
        """Bind a new admitted Turn; old bound nodes/cell callbacks retain their graph."""
        graph = copy(self)
        graph._settings = settings
        graph._turn_settings = settings
        graph._window_manager = self._window_manager.with_model_settings(settings)
        graph._turn_window = graph._window_manager
        graph._context_builder = self._context_builder.with_context_window(
            settings.main_context_limits.raw_tokens
        ).with_instruction_settings(settings)
        return graph

    async def _capture_step_settings(
        self, state: CorkiState, runtime: Runtime[GraphRunContext]
    ) -> dict:
        # Its own graph node commits the capture before any asynchronous prepare
        # work. A retry resumes call_model, not this boundary.
        owner = runtime.context.models
        return {
            "step_model_settings": owner.current
            if owner is not None
            else state.get("turn_model_settings", capture_model_settings(self._settings))
        }

    async def _step_node(
        self, state: CorkiState, runtime: Runtime[GraphRunContext], *, method: str
    ) -> dict:
        snapshot = state.get("step_model_settings")
        if "step_model_settings" in state and not isinstance(snapshot, ModelSettingsSnapshot):
            raise ValueError("invalid checkpoint Step model settings")
        view = self
        if snapshot is not None:
            owner = runtime.context.models
            if owner is not None and owner.view_snapshot == snapshot and owner.view is not None:
                view = owner.view
            elif snapshot != capture_model_settings(self._settings):
                view = self.with_model_settings(bind_model_settings(self._settings, snapshot))
                # These native consumers still belong to the admitted Turn.
                view._turn_settings, view._turn_window = self._turn_settings, self._turn_window
                # Skills reads ModelInfo from thread_extension_data, updated at
                # Turn construction, not by current_settings publication.
                view._context_builder = self._context_builder
                view._window_manager.retain_turn_metadata(
                    self._turn_settings.model_context_info(self._turn_settings.model),
                    local_compaction_owner=self._turn_window,
                )
            if owner is not None:
                owner.view_snapshot, owner.view = snapshot, view
        refreshed = {}
        if method == "_call_model" and state.get("refresh_recovered_context"):
            refreshed = await view._refresh_recovered_context(state, runtime)
            state = {**state, **refreshed}
        return {**refreshed, **await getattr(view, method)(state, runtime)}

    async def _refresh_recovered_context(self, state, runtime):
        """Refresh world state without rerunning input hooks or rebinding Step tools."""
        identity = (state["thread_id"], state["turn_id"], _sample_index(state))
        if await self._repository.load_model_failure(*identity) is not None:
            # Keep the refresh pending until retry advances past the failed sample.
            return {"refresh_recovered_context": True}
        if await self._repository.load_model_step(
            *identity
        ) is not None or await self._repository.load_partial_step(*identity):
            # Reconcile committed sampling facts before preparing any new sample.
            return {"refresh_recovered_context": False}
        tool_snapshot = self._resolve_tool_snapshot(state, runtime)
        tools = state.get("request_tools", tool_snapshot.model_visible_specs())
        snapshot = self._context_builder.managed_instructions_snapshot(
            turn_id=state["turn_id"],
            instructions=state["context_instructions"],
            project_root=Path(state["cwd"]),
        )
        policy_key = "managed_developer_instructions"
        if not any(
            item.key == policy_key
            for item in changed_context_items(
                tuple(state["request_items"]),
                snapshot.items,
                state["turn_id"],
                omitted_sections=snapshot.omitted_sections,
            )
        ):
            # A prepared Step is immutable unless the current host policy
            # invalidates it. In particular, catalog/date/shell changes alone
            # must not rewrite its saved tool observations or environment.
            # Compare the request, not only the journal: a crash can persist a
            # policy update before the refreshed request reaches its checkpoint.
            return {"refresh_recovered_context": False}
        frozen_sections = {}
        frozen_results = {}
        for item in state["request_items"]:
            if (
                isinstance(item, ContextItem)
                and not is_input_context(item)
                and item.key != policy_key
            ):
                key = (
                    "collaboration_mode" if item.key in {"mode.default", "mode.plan"} else item.key
                )
                frozen_sections[key] = replace(
                    item, content=snapshot_content(item), snapshot_content=None
                )
            elif isinstance(item, ToolResultItem):
                frozen_results[item.id] = item
        snapshot = replace(
            snapshot,
            items=(*frozen_sections.values(), *(i for i in snapshot.items if i.key == policy_key)),
            warnings=(),
            section_warnings=(),
        )
        prepared = await self._window_manager.prepare(
            thread_id=state["thread_id"],
            turn_id=state["turn_id"],
            snapshot=snapshot,
            tools=tools,
            history_projector=lambda items: tuple(frozen_results.get(i.id, i) for i in items),
            tool_resolver=lambda items: tools,
            on_retry=self._compaction_retry_callback(state, runtime),
            on_compact=self._compaction_hook_callback(state, runtime),
        )
        await self._emit_context_warnings(state, runtime, snapshot.warnings)
        await self._emit_catalog_warnings(state, runtime, prepared.warnings)
        if prepared.compacted:
            await runtime.context.events.emit(
                ContextCompacted(state["thread_id"], state["turn_id"], prepared.estimated_tokens)
            )
        return {
            "refresh_recovered_context": False,
            "request_items": prepared.items,
        }

    async def _compact(self, state: CorkiState, runtime: Runtime[GraphRunContext]) -> dict:
        return await compact_node(
            state,
            runtime,
            window=self._window_manager,
            context_builder=self._context_builder,
            code_mode=self._code_mode,
            retry_callback=self._compaction_retry_callback,
            on_compact=self._compaction_hook_callback(state, runtime),
            registry=self._registry,
            refresh_tools=self._refresh_tools,
            tool_plan=self._tool_plan,
            tool_inventory=self._tool_inventory,
            make_tool_snapshot=self._make_tool_snapshot,
        )

    def _tool_plan(self, specs, items, *, tool_mode=None):
        if tool_mode is None:
            tool_mode = (
                self._tool_router.mode(self._turn_settings)
                if self._tool_router is not None
                else self._settings.tool_mode
                if self._code_mode is not None
                else "direct"
            )
        return build_tool_plan(
            specs,
            items,
            self._settings.tool_search_mode,
            tool_mode=tool_mode,
        )

    def _make_tool_snapshot(self, base=None, info=None, *, mode=None, search=None):
        base = self._registry.snapshot() if base is None else base
        if self._tool_router is None:
            return base
        # Pinned Codex uses admitted Turn model metadata for tool availability,
        # even when the sampling Step has switched to another model.
        return self._tool_router.build(base, self._turn_settings, info, mode=mode, search=search)

    def _resolve_tool_snapshot(self, state, runtime):
        return runtime.context.tools.resolve(
            state.get("tool_snapshot_id"),
            self._registry,
            factory=lambda: self._make_tool_snapshot(
                mode=state.get("tool_mode"), search=state.get("tool_search_enabled")
            ),
        )

    def _tool_inventory(self, snapshot, plan, info=None):
        """Legacy checkpoint slot; ordinary requests carry no Lite inventory."""
        return None

    def compile(self, *, checkpointer: Any = None) -> Any:
        builder = StateGraph(CorkiState, context_schema=GraphRunContext)
        builder.add_node("capture_step_settings", self._capture_step_settings)
        for node in (
            "prepare_model_context",
            "call_model",
            "retry_model",
            "evaluate",
            "execute_tools",
            "finalize",
            "fail",
            "compact",
        ):
            builder.add_node(node, partial(self._step_node, method=f"_{node}"))
        builder.add_edge(START, "capture_step_settings")
        builder.add_conditional_edges(
            "capture_step_settings",
            route_at_start,
            {"compact": "compact", "prepare_model_context": "prepare_model_context"},
        )
        builder.add_edge("compact", END)
        builder.add_conditional_edges(
            "prepare_model_context",
            lambda state: "stopped" if state.get("prompt_stopped") else "sample",
            {"stopped": END, "sample": "call_model"},
        )
        builder.add_conditional_edges(
            "call_model",
            _route_after_model,
            {"steered": "capture_step_settings", "sampled": "evaluate", "retry": "retry_model"},
        )
        builder.add_edge("retry_model", "call_model")
        builder.add_conditional_edges(
            "evaluate",
            _route_after_evaluation,
            {
                EvaluationDecision.EXECUTE_TOOLS.value: "execute_tools",
                EvaluationDecision.CONTINUE.value: "capture_step_settings",
                EvaluationDecision.FINALIZE.value: "finalize",
                EvaluationDecision.FAIL.value: "fail",
            },
        )
        builder.add_edge("execute_tools", "capture_step_settings")
        builder.add_conditional_edges(
            "finalize",
            _route_after_evaluation,
            {
                EvaluationDecision.FINALIZE.value: END,
                EvaluationDecision.CONTINUE.value: "capture_step_settings",
                EvaluationDecision.FAIL.value: "fail",
            },
        )
        builder.add_edge("fail", END)
        return builder.compile(name="corki-agent", checkpointer=checkpointer)

    async def _prepare_model_context(
        self, state: CorkiState, runtime: Runtime[GraphRunContext]
    ) -> dict[str, object]:
        if runtime.context.legacy_base_unknown:
            index = _sample_index(state)
            completed = await self._repository.load_model_step(
                state["thread_id"], state["turn_id"], index
            )
            partial = await self._repository.load_partial_step(
                state["thread_id"], state["turn_id"], index
            )
            if completed is None and not partial:
                raise ValueError("missing admitted Turn base instructions")
        from corki.core.start_hooks import drain as drain_start
        from corki.core.start_hooks import run as run_start

        await drain_start(
            self._repository, state["thread_id"], state["turn_id"], runtime.context.events
        )
        if await run_start(
            self._stop_hooks,
            state=state,
            runtime=runtime.context,
            repository=self._repository,
            settings=self._turn_settings,
            shell=self._session_shell,
            mcp_manager=self._mcp_manager,
        ):
            return {
                "pending_input_items": (),
                "prompt_stopped": True,
                "status": "completed",
                "final_answer": "",
            }
        from corki.core.compact_start_hooks import consume as consume_compact_starts

        await consume_compact_starts(
            self._stop_hooks,
            state=state,
            runtime=runtime.context,
            repository=self._repository,
            settings=self._turn_settings,
            shell=self._session_shell,
            mcp_manager=self._mcp_manager,
        )
        await drain_async_prompt_hooks(
            self._repository, state["thread_id"], state["turn_id"], runtime.context.events
        )
        pending = state.get("pending_input_items", ())
        if pending:
            accepted = []
            for item in pending:
                if not await self._inspect_user_prompt(item, state, runtime):
                    accepted.append(item)
            state = {**state, "pending_input_items": tuple(accepted)}
            if not accepted:
                return {
                    "pending_input_items": (),
                    "prompt_stopped": True,
                    "status": "completed",
                    "final_answer": "",
                }
        await self._async_pre_hooks.recover(self._repository, state["thread_id"])
        await self._async_pre_hooks.drain(
            self._repository,
            runtime.context.events,
            active_thread=state["thread_id"],
            active_turn=state["turn_id"],
        )
        await self._async_post_hooks.recover(self._repository, state["thread_id"])
        await self._async_post_hooks.drain(
            self._repository,
            runtime.context.events,
            active_thread=state["thread_id"],
            active_turn=state["turn_id"],
        )
        await self._stop_hooks.drain(state["thread_id"], state["turn_id"], runtime.context.events)
        # New turns capture at admission. Legacy checkpoints cannot recover a
        # never-stored OS name: capture once at their next prepare boundary.
        timezone_name = state.get("timezone_name")
        if timezone_name is None:
            timezone_name = local_time.timezone_name()
            state = {**state, "timezone_name": timezone_name}
        if self._code_mode is not None:
            self._code_mode.pause()
        input_requirements = {}
        if self._refresh_input_tools is not None:
            input_requirements = await self._refresh_input_tools(state)
            state = {**state, **input_requirements}
        elif self._refresh_tools is not None:
            await self._refresh_tools()
        base_tool_snapshot = self._registry.snapshot()
        tool_snapshot = self._make_tool_snapshot(base_tool_snapshot)
        if self._code_mode is not None:
            requested = (
                self._turn_settings.model_context_info(self._turn_settings.model).tool_mode
                or self._turn_settings.tool_mode
            )
            warning = self._code_mode.take_unavailable_warning(requested, tool_snapshot.tool_mode)
            if warning is not None:
                await self._emit_context_warnings(state, runtime, (warning,))
        tool_snapshot_id = runtime.context.tools.bind(tool_snapshot)
        specs = tool_snapshot.specs()

        def resolve_tools(items):
            return self._tool_plan(specs, items, tool_mode=tool_snapshot.tool_mode).advertised

        def tools_for_model(info, items):
            candidate = self._make_tool_snapshot(base_tool_snapshot, info)
            return self._tool_plan(
                candidate.specs(), items, tool_mode=candidate.tool_mode
            ).advertised

        def inventory_for_model(info, items):
            candidate = self._make_tool_snapshot(base_tool_snapshot, info)
            plan = self._tool_plan(candidate.specs(), items, tool_mode=candidate.tool_mode)
            return self._tool_inventory(candidate, plan, info)

        realtime_active = state.get("realtime_active", False) and runtime.context.realtime.active
        latest_input = state["user_input"]
        request_tools = tuple(spec for spec in specs if spec.exposure.is_model_visible)
        # Persist the state's own pending input before accepting newer steering
        # so rapid input cannot overtake the original turn-start message.
        input_mentions = tuple(
            m for item in state.get("pending_input_items", ()) for m in item.mentions
        )
        snapshot = await self._context_builder.build(
            defer_initial_context=True,
            collaboration_mode=self._settings.collaboration_mode,
            collaboration_instructions=self._settings.collaboration_instructions,
            cwd=Path(state["cwd"]),
            turn_id=state["turn_id"],
            # Explicit skill prompts belong to the original input, not each
            # subsequent sampling step's world-state refresh.
            user_input=latest_input,
            include_input_context=bool(state.get("pending_input_items")),
            **({"input_mentions": input_mentions} if input_mentions else {}),
            realtime_active=realtime_active,
            tool_specs=specs,
            tool_snapshot=tool_snapshot,
            timezone_name=timezone_name,
        )
        turn_base = state.get("turn_base_instructions")
        if turn_base is None and state.get("request_items"):
            # Legacy checkpoints already contain the prepared request prefix.
            turn_base = state["context_instructions"]
        if turn_base is None:
            turn_base = snapshot.instructions
        if not isinstance(turn_base, str):
            raise ValueError("invalid checkpoint Turn base instructions")
        snapshot = replace(snapshot, instructions=turn_base)
        await self._emit_context_warnings(state, runtime, snapshot.warnings)
        prepared = await self._window_manager.prepare(
            thread_id=state["thread_id"],
            turn_id=state["turn_id"],
            snapshot=snapshot,
            tools=request_tools,
            pending_items=await ordered_inputs(
                self._repository, state["thread_id"], state.get("pending_input_items", ())
            ),
            history_projector=lambda items, specs=specs: current_discovery_history(specs, items),
            tool_resolver=resolve_tools,
            tool_inventory_for_model=inventory_for_model,
            tool_resolver_for_model=tools_for_model,
            on_retry=self._compaction_retry_callback(state, runtime),
            on_compact=self._compaction_hook_callback(state, runtime),
            legacy_base_unknown=runtime.context.legacy_base_unknown,
        )
        await self._emit_catalog_warnings(state, runtime, prepared.warnings)
        if prepared.compacted:
            await runtime.context.events.emit(
                ContextCompacted(state["thread_id"], state["turn_id"], prepared.estimated_tokens)
            )
        # The original Turn input gets the first sampling request. After a
        # mid-Turn compaction, model/tool continuation also gets one request
        # before pending user input (native can_drain_pending_input boundary).
        model_needs_follow_up = state.get("model_end_turn") is False or any(
            isinstance(item, ToolCallItem) for item in state.get("last_model_items", ())
        )
        drain_pending = state["step_count"] > 0 and not (
            prepared.compacted and model_needs_follow_up
        )
        realtime_update = (
            await self._accept_pending_realtime(state, runtime, tool_snapshot=tool_snapshot)
            if drain_pending
            else {}
        )
        if realtime_update.get("prompt_stopped"):
            return {
                "pending_input_items": (),
                "prompt_stopped": True,
                "status": "completed",
                "final_answer": "",
            }
        if "user_input" in realtime_update:
            latest_input = str(realtime_update["user_input"])
            if self._refresh_input_tools is not None:
                input_requirements = await self._refresh_input_tools(state)
                base_tool_snapshot = self._registry.snapshot()
                tool_snapshot = self._make_tool_snapshot(base_tool_snapshot)
                tool_snapshot_id = runtime.context.tools.bind(tool_snapshot)
                specs = tool_snapshot.specs()
                request_tools = tuple(spec for spec in specs if spec.exposure.is_model_visible)
            snapshot = await self._context_builder.build(
                defer_initial_context=True,
                collaboration_mode=self._settings.collaboration_mode,
                collaboration_instructions=self._settings.collaboration_instructions,
                cwd=Path(state["cwd"]),
                turn_id=state["turn_id"],
                user_input=latest_input,
                include_input_context=False,
                realtime_active=realtime_active,
                tool_specs=specs,
                tool_snapshot=tool_snapshot,
                timezone_name=timezone_name,
            )
            snapshot = replace(snapshot, instructions=turn_base)
            await self._emit_context_warnings(state, runtime, snapshot.warnings)
            prepared = await self._window_manager.prepare(
                thread_id=state["thread_id"],
                turn_id=state["turn_id"],
                snapshot=snapshot,
                tools=request_tools,
                history_projector=lambda items, specs=specs: current_discovery_history(
                    specs, items
                ),
                tool_resolver=resolve_tools,
                tool_inventory_for_model=inventory_for_model,
                tool_resolver_for_model=tools_for_model,
                on_retry=self._compaction_retry_callback(state, runtime),
                on_compact=self._compaction_hook_callback(state, runtime),
            )
            await self._emit_catalog_warnings(state, runtime, prepared.warnings)
            if prepared.compacted:
                await runtime.context.events.emit(
                    ContextCompacted(
                        state["thread_id"], state["turn_id"], prepared.estimated_tokens
                    )
                )
        tool_plan = self._tool_plan(specs, prepared.items, tool_mode=tool_snapshot.tool_mode)
        return {
            **realtime_update,
            **input_requirements,
            "timezone_name": timezone_name,
            "shell_snapshot": {
                "version": 1,
                "kind": self._session_shell.name,
                "path": str(self._session_shell.path),
            },
            "context_instructions": snapshot.instructions,
            "turn_base_instructions": turn_base,
            "request_items": current_discovery_history(
                specs, prepared.items, mode=self._settings.tool_search_mode
            ),
            "request_tools": tool_plan.advertised,
            "dispatch_tools": tool_plan.dispatch,
            "tool_snapshot_id": tool_snapshot_id,
            "tool_snapshot_mcp_servers": tuple(
                sorted(
                    {
                        server
                        for spec in specs
                        if (server := tool_snapshot.mcp_server_name(spec.name)) is not None
                    }
                )
            ),
            "tool_mode": tool_snapshot.tool_mode,
            "tool_search_enabled": tool_snapshot.search_enabled,
            "tool_inventory_json": self._tool_inventory(tool_snapshot, tool_plan),
            "pending_input_items": (),
            "status": "running",
            "realtime_active": realtime_active,
            "model_interrupted": False,
            "model_retry_pending": False,
            "model_retry_count": 0,
            "model_connection_retry_count": 0,
        }

    def _tools_for_history(self, items: tuple[ConversationItem, ...]) -> tuple[ToolSpec, ...]:
        snapshot = self._make_tool_snapshot()
        return self._tool_plan(snapshot.specs(), items, tool_mode=snapshot.tool_mode).advertised

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

    def _compaction_hook_callback(self, state, runtime):
        async def callback(event, operation, model, trigger, *, stage="run"):
            from corki.core.compact_hooks import run
            from corki.core.compact_start_hooks import consume, queue

            if event == "PostCompact" and stage == "prepare":
                await queue(self._repository, state["thread_id"], state["turn_id"], operation)
            await run(
                self._stop_hooks,
                event,
                operation,
                model,
                trigger,
                state=state,
                runtime=runtime.context,
                repository=self._repository,
                settings=self._turn_settings,
                shell=step_shell(state, self._session_shell),
                mcp_manager=self._mcp_manager,
                stage=stage,
            )
            if (
                event == "PostCompact"
                and stage != "prepare"
                and state.get("operation") != "compact"
            ):
                await consume(
                    self._stop_hooks,
                    state=state,
                    runtime=runtime.context,
                    repository=self._repository,
                    settings=self._turn_settings,
                    shell=step_shell(state, self._session_shell),
                    mcp_manager=self._mcp_manager,
                )

        return callback

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
        tool_snapshot = self._resolve_tool_snapshot(state, runtime)
        failure = await self._repository.load_model_failure(
            state["thread_id"], state["turn_id"], _sample_index(state)
        )
        if failure is not None:
            return await self._retry_update(state, runtime, failure)
        readiness = StepToolReadiness(
            self._repository, state["thread_id"], state["turn_id"], tool_snapshot
        )
        advertised = {
            spec.name: spec for spec in state.get("dispatch_tools", state["request_tools"])
        }
        live = LiveTools(
            lambda item: self._execute_one(state, runtime, item.call, tool_snapshot=tool_snapshot),
            readiness=lambda item: readiness(item.call, advertised.get(item.call.name)),
        )
        propagation = self._activate_code_mode(state, runtime, tool_snapshot, live.failure)
        keep_worker = False
        try:
            update = await self._call_model_owned(state, runtime, live)
            results = await live.finish(
                on_result=lambda result: self._repository.append_items(
                    state["thread_id"], (result,)
                )
            )
            completed = {item.call_id for item in results}
            keep_worker = not update.get("model_interrupted", False) and any(
                isinstance(item, ToolCallItem) and item.call.id not in completed
                for item in update.get("last_model_items", ())
            )
            if not keep_worker and update.get("model_response_completed", False):
                await runtime.context.turn_diff.step_completed(
                    state["thread_id"], state["turn_id"], runtime.context.events
                )
                update["model_response_completed"] = False
            return {**update, "streamed_tool_results": results}
        except BaseException as error:
            try:
                if isinstance(error, ModelError):
                    # Codex drains in-flight calls even after a failed model
                    # stream. Transport failure is not tool cancellation.
                    await live.finish(
                        on_result=lambda result: self._repository.append_items(
                            state["thread_id"], (result,)
                        )
                    )
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
        if self._code_mode is None or tool_snapshot.tool_mode == "direct":
            return None
        mcp_resources = runtime.context.tools.resources

        async def nested(call, spec):
            nested_runtime = Runtime(
                context=replace(
                    runtime.context,
                    events=CellEventSink(runtime.context.events, inactive),
                )
            )
            try:
                return await self._execute_one(
                    state,
                    nested_runtime,
                    call,
                    nested_spec=spec,
                    tool_snapshot=tool_snapshot,
                    mcp_resources=mcp_resources,
                )
            except BaseException:
                await self._execute_one(
                    state,
                    nested_runtime,
                    call,
                    interrupted=True,
                    nested_spec=spec,
                    tool_snapshot=tool_snapshot,
                    mcp_resources=mcp_resources,
                )
                raise

        async def notify(call_id, text):
            item = ToolResultItem(call_id, "exec", text, state["turn_id"], input_kind="freeform")
            await self._repository.append_items(state["thread_id"], (item,))
            await CellEventSink(runtime.context.events, inactive).emit(
                ToolOutputDelta(state["thread_id"], state["turn_id"], call_id, text[:4000])
            )

        propagation = self._code_mode.activate(
            state["turn_id"],
            nested,
            notify,
            failure,
            registry=tool_snapshot,
            admission_lease=mcp_resources.lease if mcp_resources is not None else None,
            readiness=StepToolReadiness(
                self._repository, state["thread_id"], state["turn_id"], tool_snapshot
            ),
        )
        # Capture the owning Turn's event lifetime, never a later Turn's sink.
        inactive = self._code_mode.inactive
        return propagation

    async def _retry_update(self, state, runtime, failure: ModelFailure):
        error = failure.error()
        unbounded = (
            failure.kind == ModelErrorKind.CONNECTION.value
            and self._settings.model_unbounded_connection_retries
        )
        if not error.retryable or (
            not unbounded and failure.retries_used >= self._settings.model_max_retries
        ):
            raise error
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
        tool_snapshot = self._resolve_tool_snapshot(state, runtime)
        specs = tool_snapshot.specs()
        items = active_history(await self._repository.load_items(state["thread_id"]))
        plan = self._tool_plan(specs, items, tool_mode=tool_snapshot.tool_mode)
        return {
            "model_retry_pending": False,
            "request_items": current_discovery_history(
                specs, items, mode=self._settings.tool_search_mode
            ),
            "request_tools": plan.advertised,
            "dispatch_tools": plan.dispatch,
            "tool_snapshot_id": state.get("tool_snapshot_id"),
            "tool_inventory_json": self._tool_inventory(tool_snapshot, plan),
        }

    async def _call_model_owned(
        self, state: CorkiState, runtime: Runtime[GraphRunContext], live: LiveTools
    ) -> dict[str, object]:
        # Checkpoints created before request-level tool plans were introduced
        # can still resume. New checkpoints always carry the exact frozen tuple.
        tool_snapshot = self._resolve_tool_snapshot(state, runtime)
        request_tools = state.get("request_tools", tool_snapshot.model_visible_specs())
        request_items = self._window_manager.model_history(state["request_items"])
        if self._media is not None:
            request_items = await self._media.prepare_items(request_items, for_model=True)
        request = ModelRequest(
            model=self._settings.model,
            thread_id=str(state["thread_id"]),
            content_item_kinds=self._settings.content_item_kinds,
            model_info=self._settings.model_context_info(self._settings.model),
            reasoning_effort=self._settings.reasoning_effort,
            reasoning_summary=self._settings.reasoning_summary,
            service_tier=self._settings.session_service_tier,
            fast_mode_enabled=self._settings.fast_mode,
            instructions=state["context_instructions"],
            context_items=(),
            items=request_items,
            tools=request_tools,
            tool_search_mode=self._settings.tool_search_mode,
            harness_managed_retries=True,
            tool_freeform_mode=self._settings.tool_freeform_mode,
            tool_namespace_mode=self._settings.tool_namespace_mode,
            client_metadata=await self._window_manager.client_metadata(
                state["thread_id"],
                state["turn_id"],
                tool_inventory_json=state.get("tool_inventory_json"),
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
            _validate_model_items(
                ModelCompleted(partial), state["turn_id"], allow_legacy_hosted=True
            )
            if any(has_external_context(item) for item in partial):
                await self._mark_memory_polluted(state["thread_id"])
            return {
                "last_model_items": partial,
                "model_end_turn": False,
                "model_response_completed": False,
                "step_count": state["step_count"] + 1,
                "sample_count": _sample_index(state) + 1,
                "model_interrupted": False,
            }
        partial_items: list[ConversationItem] = []
        output = ModelOutput(
            state["thread_id"],
            state["turn_id"],
            runtime.context.events.emit,
            plan_mode=self._turn_settings.collaboration_mode == "plan",
        )
        recorded_citation_items: set[str] = set()
        if completed is None:
            if runtime.context.legacy_base_unknown:
                raise ValueError("missing admitted Turn base instructions")
            realtime = runtime.context.realtime if state.get("realtime_active", False) else None
            async with aclosing(
                model_events(self._model, request, realtime, live.failure)
            ) as stream:
                async for event in stream:
                    if isinstance(event, RealtimeStop):
                        raise asyncio.CancelledError
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
                        elif isinstance(item, ReasoningItem):
                            await output.complete_reasoning(item)
                        if self._memory_repository is not None and item_citations:
                            with suppress(Exception):
                                await self._memory_repository.mark_memories_used(item_citations)
                            recorded_citation_items.add(item.id)
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
                        await output.reasoning_delta(event)
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
        completed, _, _ = _normalize_memory_citations(completed)
        if completed.items[: len(partial_items)] != tuple(partial_items):
            raise ModelError("completed response changed or omitted an already completed item")
        _validate_model_items(completed, state["turn_id"], allow_legacy_hosted=not sampled)
        # Deduplicate blocks within an item, not distinct completed messages.
        # An item.done already records its usage; a durable step replay is not
        # a new model completion and must not count the same citations again.
        cited_threads = tuple(
            thread
            for item in completed.items
            if isinstance(item, AssistantMessageItem)
            and item.memory_citation is not None
            and item.id not in recorded_citation_items
            for thread in item.memory_citation.thread_ids
        )
        if sampled and self._memory_repository is not None and cited_threads:
            # Usage ranking is helpful but never important enough to fail an
            # otherwise valid user turn when telemetry persistence is degraded.
            with suppress(Exception):
                await self._memory_repository.mark_memories_used(cited_threads)
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
                cache_write_tokens=completed.usage.cache_write_tokens,
                codex_rollout_budget_units=completed.usage.codex_rollout_budget_units,
            )
        )

        for item in completed.items:
            if isinstance(item, AssistantMessageItem):
                await output.complete(item)
            elif isinstance(item, ReasoningItem):
                await output.complete_reasoning(item)
        return {
            "last_model_items": completed.items,
            "model_end_turn": completed.end_turn,
            "model_response_completed": True,
            "step_count": state["step_count"] + 1,
            "sample_count": _sample_index(state) + 1,
            "model_retry_count": 0,
            "model_connection_retry_count": 0,
            "model_interrupted": False,
        }

    async def _accept_pending_realtime(
        self,
        state: CorkiState,
        runtime: Runtime[GraphRunContext],
        *,
        tool_snapshot: ToolRegistrySnapshot | None = None,
    ) -> dict[str, object]:
        if not state.get("realtime_active", False) or not runtime.context.realtime.active:
            return {}
        if runtime.context.realtime.stop_requested:
            raise asyncio.CancelledError
        latest: str | None = None
        blocked = False
        for command in runtime.context.realtime.take_pending():
            if isinstance(command, RealtimeStop):
                raise asyncio.CancelledError
            if await self._persist_realtime_input(
                state, runtime, command, tool_snapshot=tool_snapshot
            ):
                latest = command.text
            else:
                blocked = True
        if blocked and latest is None:
            return {"prompt_stopped": True}
        return {"user_input": latest} if latest is not None else {}

    async def _inspect_user_prompt(self, item, state, runtime):
        from corki.core.prompt_hooks import inspect

        return await inspect(
            self._stop_hooks,
            item,
            state=state,
            runtime=runtime.context,
            repository=self._repository,
            settings=self._turn_settings,
            shell=self._session_shell,
            mcp_manager=self._mcp_manager,
        )

    async def _persist_realtime_input(
        self,
        state: CorkiState,
        runtime: Runtime[GraphRunContext],
        command: RealtimeInput,
        *,
        tool_snapshot: ToolRegistrySnapshot | None = None,
    ) -> bool:
        if tool_snapshot is None:
            tool_snapshot = self._resolve_tool_snapshot(state, runtime)
        specs = tool_snapshot.specs()

        def tools_for_model(info, items):
            candidate = self._make_tool_snapshot(tool_snapshot, info)
            return self._tool_plan(
                candidate.specs(), items, tool_mode=candidate.tool_mode
            ).advertised

        def inventory_for_model(info, items):
            candidate = self._make_tool_snapshot(tool_snapshot, info)
            plan = self._tool_plan(candidate.specs(), items, tool_mode=candidate.tool_mode)
            return self._tool_inventory(candidate, plan, info)

        text = command.text
        item = command.item or UserMessageItem(text, state["turn_id"])
        if await self._inspect_user_prompt(item, state, runtime):
            runtime.context.realtime.acknowledge((item,))
            return False
        snapshot = await self._context_builder.build(
            defer_initial_context=True,
            collaboration_mode=self._settings.collaboration_mode,
            collaboration_instructions=self._settings.collaboration_instructions,
            cwd=Path(state["cwd"]),
            turn_id=state["turn_id"],
            # Codex's pending-input path does not repeat turn-start skill
            # injection. The model can still discover/read a mentioned skill.
            user_input=text,
            include_input_context=False,
            realtime_active=True,
            tool_specs=specs,
            tool_snapshot=tool_snapshot,
            timezone_name=state.get("timezone_name"),
        )
        await self._emit_context_warnings(state, runtime, snapshot.warnings)
        prepared = await self._window_manager.prepare(
            thread_id=state["thread_id"],
            turn_id=state["turn_id"],
            snapshot=snapshot,
            tools=tool_snapshot.model_visible_specs(),
            pending_items=await ordered_inputs(self._repository, state["thread_id"], (item,)),
            history_projector=lambda items, specs=specs: current_discovery_history(specs, items),
            tool_resolver=lambda items: (
                self._tool_plan(specs, items, tool_mode=tool_snapshot.tool_mode).advertised
            ),
            tool_inventory_for_model=inventory_for_model,
            tool_resolver_for_model=tools_for_model,
            on_retry=self._compaction_retry_callback(state, runtime),
            on_compact=self._compaction_hook_callback(state, runtime),
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
        return True

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
            tool_snapshot = self._resolve_tool_snapshot(state, runtime)
            self._activate_code_mode(state, runtime, tool_snapshot)
        try:
            result = await self._execute_tools_owned(state, runtime)
            if not keep_worker:
                await self._window_manager.record_budget_notices(
                    state["thread_id"], state["turn_id"], needs_follow_up=True
                )
                if state.get("model_response_completed", False):
                    await runtime.context.turn_diff.step_completed(
                        state["thread_id"], state["turn_id"], runtime.context.events
                    )
                    result["model_response_completed"] = False
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
        tool_snapshot = self._resolve_tool_snapshot(state, runtime)
        request_tools = state.get(
            "dispatch_tools", state.get("request_tools", tool_snapshot.model_visible_specs())
        )
        advertised = {spec.name: spec for spec in request_tools}
        streamed = {item.call_id: item for item in state.get("streamed_tool_results", ())}
        readiness = StepToolReadiness(
            self._repository, state["thread_id"], state["turn_id"], tool_snapshot
        )
        live = LiveTools(
            lambda item: self._execute_one(state, runtime, item.call, tool_snapshot=tool_snapshot),
            readiness=lambda item: readiness(item.call, advertised.get(item.call.name)),
        )
        try:
            for item in state["last_model_items"]:
                if isinstance(item, ToolCallItem) and item.call.id not in streamed:
                    spec = advertised.get(item.call.name)
                    live.submit(
                        item,
                        parallel=spec is not None and spec.concurrency is ToolConcurrency.PARALLEL,
                    )
            completed = {
                item.call_id: item
                for item in await live.finish(
                    on_result=lambda result: self._repository.append_items(
                        state["thread_id"], (result,)
                    )
                )
            }
            results = [streamed.get(call.id) or completed[call.id] for call in calls]
        finally:
            await live.aclose()

        for item in results:
            if item.state_update.plan is not None:
                plan = tuple(dict(value) for value in item.state_update.plan)
                latest = runtime.context.nested_plan_updates
                if (
                    isinstance(
                        tool_snapshot.get(item.tool_name), (CodeModeExecTool, CodeModeWaitTool)
                    )
                    and latest
                    and latest[-1].plan == plan
                    and latest[-1].explanation == item.state_update.plan_explanation
                ):
                    continue
                await runtime.context.events.emit(
                    PlanUpdated(
                        state["thread_id"],
                        state["turn_id"],
                        plan,
                        explanation=item.state_update.plan_explanation,
                        tool_call_id=item.call_id,
                    )
                )
                # A distinct outer/direct update supersedes any previous echo.
                runtime.context.nested_plan_updates.clear()
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
        mcp_resources=None,
    ) -> ToolResultItem | ToolResult:
        if tool_snapshot is None:
            tool_snapshot = self._resolve_tool_snapshot(state, runtime)
        if mcp_resources is None:
            mcp_resources = runtime.context.tools.resources
        with (
            mcp_resources.lease()
            if mcp_resources is not None and not interrupted
            else nullcontext()
        ):
            return await self._execute_bound_tool(
                state,
                runtime,
                call,
                interrupted=interrupted,
                nested_spec=nested_spec,
                tool_snapshot=tool_snapshot,
                mcp_resources=mcp_resources,
            )

    async def _execute_bound_tool(
        self, state, runtime, call, *, interrupted, nested_spec, tool_snapshot, mcp_resources
    ):
        from corki.core.pre_tool_hooks import bind

        post_snapshot = self._stop_hooks._snapshot.get("PostToolUse", ((), ()))
        before_tool = bind(
            self._stop_hooks._snapshot.get("PreToolUse", ((), ())),
            state=state,
            runtime=runtime.context,
            settings=self._turn_settings,
            repository=self._repository,
            shell=step_shell(state, self._session_shell),
            environment=self._stop_hooks.environment,
            async_owner=self._async_pre_hooks,
            mcp_manager=self._mcp_manager,
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
        model_authority = self._turn_settings.model_context_info(
            self._turn_settings.model
        ).activation_authority
        honor_allow_rules = not (model_authority is not None and model_authority.cyber)
        if cached is not None:
            # Durable identity/argument validation already ran in claim_tool_call.
            # A changed catalog cannot rewrite a completed or unknown outcome.
            result = cached
        elif interrupted:
            result = self._executor.error(
                call, "Tool execution cancelled before dispatch; not executed."
            )
        elif nested_spec is not None:
            try:
                result = await self._executor.execute(
                    call,
                    ToolContext(
                        before_tool=before_tool,
                        request_user_input=runtime.context.request_user_input,
                        is_non_root_agent=runtime.context.is_non_root_agent,
                        collaboration_mode=self._turn_settings.collaboration_mode,
                        cwd=Path(state["cwd"]),
                        mcp_resources=mcp_resources,
                        orchestrator_mcp_enabled=self._turn_settings.orchestrator_mcp_enabled,
                        on_patch_started=lambda: runtime.context.turn_diff.started(call.id),
                        execution_permissions=self._turn_settings.execution_permissions,
                        write_stdin_approval=self._turn_settings.write_stdin_approval,
                        honor_exec_policy_allow_rules=honor_allow_rules,
                        on_warning=lambda message: runtime.context.events.emit(
                            WarningEvent(state["thread_id"], state["turn_id"], message)
                        ),
                        shell=step_shell(state, self._session_shell),
                        remaining_context_tokens=lambda: self._turn_window.remaining_tokens(
                            state["thread_id"],
                            instructions=state["context_instructions"],
                            tools=state["request_tools"],
                        ),
                        on_external_context=lambda: self._mark_memory_polluted(state["thread_id"]),
                        model_output_policy=self._turn_settings.model_context_info(
                            self._turn_settings.model
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
            result = await self._executor.execute(
                call,
                ToolContext(
                    before_tool=before_tool,
                    request_user_input=runtime.context.request_user_input,
                    is_non_root_agent=runtime.context.is_non_root_agent,
                    collaboration_mode=self._turn_settings.collaboration_mode,
                    cwd=Path(state["cwd"]),
                    mcp_resources=mcp_resources,
                    orchestrator_mcp_enabled=self._turn_settings.orchestrator_mcp_enabled,
                    on_patch_started=lambda: runtime.context.turn_diff.started(call.id),
                    execution_permissions=self._turn_settings.execution_permissions,
                    write_stdin_approval=self._turn_settings.write_stdin_approval,
                    honor_exec_policy_allow_rules=honor_allow_rules,
                    on_warning=lambda message: runtime.context.events.emit(
                        WarningEvent(state["thread_id"], state["turn_id"], message)
                    ),
                    shell=step_shell(state, self._session_shell),
                    remaining_context_tokens=lambda: self._turn_window.remaining_tokens(
                        state["thread_id"],
                        instructions=state["context_instructions"],
                        tools=state["request_tools"],
                    ),
                    on_external_context=lambda: self._mark_memory_polluted(state["thread_id"]),
                    model_output_policy=self._turn_settings.model_context_info(
                        self._turn_settings.model
                    ).truncation_policy,
                    supports_image_input=self._settings.supports_image_input,
                    supports_audio_input=self._settings.supports_audio_input,
                    supports_image_detail_original=self._settings.supports_image_detail_original,
                ),
                spec=advertised[call.name],
                snapshot=tool_snapshot,
            )
        from corki.tools.builtin.patch import ApplyPatchTool

        is_patch = isinstance(tool_snapshot.get(call.name), ApplyPatchTool)
        if (
            cached is None
            and is_patch
            and self._turn_settings.execution_permissions is not None
            and result.dispatch_error
            and result.patch_delta_json is None
            and call.id not in runtime.context.turn_diff.pending
        ):
            # Host admission/schema rejection cannot have started a patch. Keep
            # the error channel, but persist proof that this call changed nothing.
            result = replace(result, patch_delta_json='{"version":1,"exact":true,"changes":[]}')
        from corki.core.post_tool_hooks import run as run_post_hooks

        post_arguments = dict(
            result=result,
            tool=tool_snapshot.get(call.name),
            state=state,
            runtime=runtime.context,
            settings=self._turn_settings,
            repository=self._repository,
            shell=step_shell(state, self._session_shell),
            environment=self._stop_hooks.environment,
            fresh=cached is None,
            nested=nested_spec is not None,
            async_owner=self._async_post_hooks,
            mcp_manager=self._mcp_manager,
        )
        if cached is None:
            batch = (
                await run_post_hooks(post_snapshot, **post_arguments, prepare_only=True)
                if not interrupted
                else None
            )
            if batch is None:
                await self._repository.complete_tool_call(
                    state["thread_id"], state["turn_id"], result
                )
            else:
                await self._repository.complete_tool_call(
                    state["thread_id"], state["turn_id"], result, batch
                )
        if result.contains_external_context:
            await self._mark_memory_polluted(state["thread_id"])
        projected_result = result
        if not interrupted and (cached is not None or batch is not None):
            projected_result = await run_post_hooks(post_snapshot, **post_arguments)
        if result.display_content and not interrupted:
            await runtime.context.events.emit(
                ToolOutputDelta(
                    state["thread_id"], state["turn_id"], call.id, result.display_content
                )
            )
        if not interrupted:
            if nested_spec is not None and result.state_update.plan is not None:
                update = PlanUpdated(
                    state["thread_id"],
                    state["turn_id"],
                    tuple(dict(value) for value in result.state_update.plan),
                    explanation=result.state_update.plan_explanation,
                    tool_call_id=call.id,
                )
                await runtime.context.events.emit(update)
                runtime.context.nested_plan_updates[:] = [update]
            await runtime.context.events.emit(
                ToolCallCompleted(
                    state["thread_id"],
                    state["turn_id"],
                    call.id,
                    call.name,
                    result.is_error,
                    mcp_result_json=result.mcp_result_json,
                    mcp_error=result.mcp_error,
                    patch_delta_json=result.patch_delta_json,
                )
            )
        permissions = self._turn_settings.execution_permissions
        await runtime.context.turn_diff.observe(
            result,
            is_patch=is_patch,
            compiler=permissions.compiler if permissions is not None else None,
            cwd=Path(state["cwd"]),
            thread=state["thread_id"],
            turn=state["turn_id"],
            events=runtime.context.events,
            publish=not interrupted,
        )

        # A nested call needs the authoritative output, not its lossy history
        # projection. Both paths already passed through the same durable
        # claim/execute/complete and event lifecycle above. Do not duplicate
        # structured data into the model-visible conversation.
        result = projected_result
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
        from corki.core.start_hooks import drain as drain_start

        await drain_start(
            self._repository, state["thread_id"], state["turn_id"], runtime.context.events
        )
        await drain_async_prompt_hooks(
            self._repository, state["thread_id"], state["turn_id"], runtime.context.events
        )
        await self._async_pre_hooks.drain(
            self._repository,
            runtime.context.events,
            active_thread=state["thread_id"],
            active_turn=state["turn_id"],
        )
        await self._async_post_hooks.drain(
            self._repository,
            runtime.context.events,
            active_thread=state["thread_id"],
            active_turn=state["turn_id"],
        )
        await self._stop_hooks.drain(state["thread_id"], state["turn_id"], runtime.context.events)
        stop_decision = await self._stop_hooks.run(
            state,
            runtime.context,
            settings=self._turn_settings,
            repository=self._repository,
            shell=self._session_shell,
            mcp_manager=self._mcp_manager,
        )
        if stop_decision is StopDecision.BLOCK:
            evaluation = evaluate_model_step(
                state["last_model_items"],
                step_count=state["step_count"],
                tool_call_count=state["tool_call_count"],
                max_steps=self._settings.max_steps,
                max_tool_calls=self._settings.max_tool_calls,
                end_turn=False,
            )
            return {"route": evaluation.decision.value, "error": evaluation.error}
        realtime = runtime.context.realtime
        live = state.get("realtime_active", False) and realtime.active
        if live and stop_decision is StopDecision.STOP:
            if realtime.stop_requested:
                raise asyncio.CancelledError
            # Stop ends this sampling loop, even if input arrived during hooks.
            # Runtime completion still persists accepted, unrecorded input.
            realtime.close_input()
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
        needs_follow_up = bool(pending) and stop_decision is not StopDecision.STOP
        seen_items = {item.id for item in state["request_items"]}
        async_pending = any(
            (
                (
                    getattr(item, "key", "").startswith(
                        ("post_tool_use:", "pre_tool_use:", "start_hook:")
                    )
                    and item.key.endswith(":async:additional_context")
                )
                or (
                    getattr(item, "key", "").startswith("user_prompt_submit:")
                    and item.key.endswith(":additional_context")
                )
            )
            and item.id not in seen_items
            for item in history
        )
        needs_follow_up |= async_pending and stop_decision is not StopDecision.STOP
        if live:
            if realtime.stop_requested:
                raise asyncio.CancelledError
            if not needs_follow_up and stop_decision is not StopDecision.STOP:
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


def _validate_model_items(
    completed: ModelCompleted, turn_id: TurnId, *, allow_legacy_hosted: bool = False
) -> None:
    """Protect durable history from a malformed provider adapter result."""

    from corki.protocol.items import HostedToolItem

    if not allow_legacy_hosted and any(
        isinstance(item, HostedToolItem) for item in completed.items
    ):
        raise ModelError(
            "provider adapter returned an unsupported hosted item; use ordinary function calling",
            kind=ModelErrorKind.PROTOCOL,
        )
    allowed = (AssistantMessageItem, ReasoningItem, ToolCallItem, HostedToolItem)
    if any(not isinstance(item, allowed) for item in completed.items):
        raise ModelError(
            "provider adapter returned a non-model conversation item",
            kind=ModelErrorKind.PROTOCOL,
        )
    if any(
        not _valid_model_wire_id(item.turn_id) or item.turn_id != turn_id
        for item in completed.items
    ):
        raise ModelError(
            "provider adapter returned an item for a different turn",
            kind=ModelErrorKind.PROTOCOL,
        )
    if any(not _valid_model_wire_id(item.step_id) for item in completed.items):
        raise ModelError(
            "provider adapter returned an invalid step id",
            kind=ModelErrorKind.PROTOCOL,
        )
    step_ids = {item.step_id for item in completed.items}
    if len(step_ids) > 1:
        raise ModelError(
            "provider adapter returned multiple step ids for one response",
            kind=ModelErrorKind.PROTOCOL,
        )
    item_ids = [item.id for item in completed.items]
    if any(not _valid_model_wire_id(item_id) for item_id in item_ids):
        raise ModelError(
            "provider adapter returned an invalid conversation item id",
            kind=ModelErrorKind.PROTOCOL,
        )
    if len(item_ids) != len(set(item_ids)):
        raise ModelError(
            "provider adapter returned duplicate conversation item ids",
            kind=ModelErrorKind.PROTOCOL,
        )
    calls = [item.call for item in completed.items if isinstance(item, ToolCallItem)]
    call_ids = [call.id for call in calls]
    if any(not _valid_model_wire_id(call_id) for call_id in call_ids):
        raise ModelError(
            "provider adapter returned an invalid tool call id",
            kind=ModelErrorKind.PROTOCOL,
        )
    if len(call_ids) != len(set(call_ids)):
        raise ModelError(
            "provider adapter returned duplicate tool call ids",
            kind=ModelErrorKind.PROTOCOL,
        )
    # Empty/whitespace strings are valid wire names. An unregistered name is a
    # tool Observation, not a malformed model step; do not trim or replace it.
    if any(not _valid_model_wire_id(call.name) for call in calls):
        raise ModelError(
            "provider adapter returned a tool call with an invalid name",
            kind=ModelErrorKind.PROTOCOL,
        )


def _valid_model_wire_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


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
        content, citation = parse_memory_citation(item.content)
        citation = item.memory_citation or citation
        if citation is not None:
            found = True
            thread_ids.extend(citation.thread_ids)
        body = item.response_body_json
        if content != item.content and body is None:
            # Chat/custom ports have no native body carrier. Preserve their raw
            # text as a typed compatibility body before changing the UI view.
            body = capture_response_body(
                {"type": "message", "content": [{"type": "output_text", "text": item.content}]}
            )
        normalized.append(
            replace(item, content=content, memory_citation=citation, response_body_json=body)
        )
    return (
        replace(completed, items=tuple(normalized)),
        tuple(dict.fromkeys(thread_ids)),
        found,
    )
