"""Durable streaming facade around the checkpointed LangGraph harness."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from langgraph import errors as graph_errors
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from corki.config import CorkiSettings
from corki.context import ContextBuilder, ContextWindowManager
from corki.context.project import discover_project_root
from corki.core.graph import CorkiGraph, EventSink, GraphRunContext
from corki.core.state import CorkiState
from corki.core.turn_run import TurnRun
from corki.mcp import MCPManager
from corki.mcp.context import MCPContextContributor
from corki.memory import (
    LocalMemoryBackend,
    LongTermMemoryService,
    MemoryContextContributor,
    SQLiteMemoryRepository,
    memory_tools,
)
from corki.memory.repository import MemoryRepository
from corki.models import (
    ModelError,
    ModelPort,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    ProviderCapabilities,
    resolve_capabilities,
)
from corki.plugins import PluginManager
from corki.plugins.context import PluginContextContributor
from corki.protocol.events import (
    RuntimeEvent,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from corki.protocol.ids import ThreadId, TurnId, new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.realtime import RealtimeController
from corki.realtime.context import RealtimeContextContributor
from corki.sessions import TurnRecord, TurnStatus
from corki.sessions.repository import SessionRepository
from corki.skills import SkillListTool, SkillReadTool, SkillService
from corki.skills.context import SkillContextContributor
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolExecutor, ToolRegistry
from corki.tools.builtin import (
    ApplyPatchTool,
    ExecCommandTool,
    ProcessManager,
    UpdatePlanTool,
    ViewImageTool,
    WriteStdinTool,
)
from corki.tools.search import ToolSearchTool

_LOG = logging.getLogger(__name__)


class AgentRuntime(Protocol):
    async def stream(
        self, message: str, *, realtime: bool = False
    ) -> AsyncIterator[RuntimeEvent]: ...

    async def resume_pending(self) -> AsyncIterator[RuntimeEvent]: ...

    async def steer(self, message: str) -> None: ...

    async def cancel_active(self) -> None: ...

    async def aclose(self) -> None: ...


class _QueueEventSink(EventSink):
    def __init__(self, queue: asyncio.Queue[RuntimeEvent]) -> None:
        self._queue = queue

    async def emit(self, event: RuntimeEvent) -> None:
        await self._queue.put(event)


class LangGraphRuntime:
    """Own one thread, one active turn, and durable graph checkpoints."""

    def __init__(
        self,
        *,
        settings: CorkiSettings,
        model: ModelPort,
        repository: SessionRepository,
        registry: ToolRegistry,
        process_manager: ProcessManager,
        mcp_manager: MCPManager,
        plugin_manager: PluginManager,
        memory_service: LongTermMemoryService | None,
        memory_repository: MemoryRepository | None,
        context_builder: ContextBuilder,
        checkpoint_path: Path,
        thread_id: ThreadId | None = None,
    ) -> None:
        self._settings = settings
        self._model = model
        self._repository = repository
        self._registry = registry
        self._process_manager = process_manager
        self._mcp_manager = mcp_manager
        self._plugin_manager = plugin_manager
        self._memory_service = memory_service
        self._memory_repository = memory_repository
        self._thread_id = thread_id or new_thread_id()
        self._thread_created = False
        self._thread_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._ready_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._active_run: TurnRun | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._checkpoint_context: Any | None = None
        self._checkpoint_path = checkpoint_path
        self._checkpointer: AsyncSqliteSaver | None = None
        self._compiled: Any = None
        self._closed = False
        self._realtime = RealtimeController(settings.max_realtime_inputs)
        executor = ToolExecutor(
            registry,
            output_char_budget=settings.tool_output_char_budget,
        )
        window_manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name=settings.model,
            context_window_tokens=settings.context_window_tokens,
            auto_compact_tokens=settings.auto_compact_tokens,
        )
        self._graph = CorkiGraph(
            settings=settings,
            model=model,
            repository=repository,
            registry=registry,
            executor=executor,
            context_builder=context_builder,
            window_manager=window_manager,
            memory_repository=memory_repository,
        )

    @classmethod
    def create(
        cls,
        *,
        settings: CorkiSettings,
        database_path: Path,
        model: ModelPort | None = None,
        thread_id: ThreadId | None = None,
        repository: SessionRepository | None = None,
        registry: ToolRegistry | None = None,
        home_path: Path | None = None,
        compatibility_home: Path | None = None,
        memory_model: ModelPort | None = None,
        memory_repository: MemoryRepository | None = None,
        memory_root: Path | None = None,
    ) -> LangGraphRuntime:
        process_manager = ProcessManager()
        actual_registry = registry or _default_registry(settings, process_manager)
        # Create the canonical session schema before the memory adapter extends
        # the shared database with memory-specific tables.
        actual_repository = repository or SQLiteSessionRepository(database_path)
        capability_home = home_path or _capability_home(database_path)
        project_root = discover_project_root(settings.working_directory)
        plugin_roots = (
            project_root / ".corki" / "plugins",
            capability_home / "plugins",
            *tuple(
                path if path.is_absolute() else settings.working_directory / path
                for path in settings.plugin_dirs
            ),
        )
        plugin_manager = PluginManager.discover_and_load(
            roots=plugin_roots,
            disabled=settings.disabled_plugins,
            registry=actual_registry,
        )
        memories_root = memory_root or capability_home / "memories"
        actual_memory_repository = memory_repository
        if settings.memories_enabled and actual_memory_repository is None:
            if not isinstance(actual_repository, SQLiteSessionRepository):
                raise ValueError(
                    "memories_enabled with a custom session repository requires an explicit "
                    "memory_repository"
                )
            actual_memory_repository = SQLiteMemoryRepository(database_path)
        contributors = [
            MemoryContextContributor(
                memories_root,
                enabled=settings.memories_enabled and settings.memories_use,
                token_limit=settings.memories_summary_token_limit,
            ),
            RealtimeContextContributor(),
        ]
        if settings.memories_enabled and settings.memories_dedicated_tools:
            assert actual_memory_repository is not None
            backend = LocalMemoryBackend(memories_root, actual_memory_repository)
            for memory_tool in memory_tools(backend):
                actual_registry.register(memory_tool)
        if settings.skills_enabled:
            skill_service = SkillService(
                home=capability_home,
                project_root=project_root,
                compatibility_home=compatibility_home,
                plugin_roots=plugin_manager.skill_roots,
            )
            actual_registry.register(SkillListTool(skill_service))
            actual_registry.register(SkillReadTool(skill_service))
            contributors.append(SkillContextContributor(skill_service))
        mcp_settings = tuple(
            replace(server, cwd=server.cwd or settings.working_directory)
            for server in (*settings.mcp_servers, *plugin_manager.mcp_servers)
        )
        mcp_manager = MCPManager(
            mcp_settings, actual_registry, defer_tools=settings.tool_search_mode != "disabled"
        )
        contributors.insert(0, MCPContextContributor(mcp_manager))
        contributors.append(
            PluginContextContributor(plugin_manager.plugins, plugin_manager.warnings)
        )
        context_builder = ContextBuilder(contributors=tuple(contributors))
        capabilities = resolve_capabilities(
            base_url=settings.api_base,
            api_mode=settings.api_mode,
            provider_name=settings.provider_name,
        )
        capabilities = replace(
            capabilities, supports_native_tool_search=settings.tool_search_mode == "native"
        )
        actual_model = model or _create_model(settings, capabilities)
        memory_service: LongTermMemoryService | None = None
        if settings.memories_enabled:
            assert actual_memory_repository is not None
            actual_memory_model = memory_model
            close_memory_model = False
            if actual_memory_model is None:
                if model is not None:
                    actual_memory_model = actual_model
                else:
                    actual_memory_model = _create_model(settings, capabilities)
                    close_memory_model = True
            memory_service = LongTermMemoryService(
                settings=settings,
                repository=actual_memory_repository,
                model=actual_memory_model,
                root=memories_root,
                close_model=close_memory_model,
            )
        return cls(
            settings=settings,
            model=actual_model,
            repository=actual_repository,
            registry=actual_registry,
            process_manager=process_manager,
            mcp_manager=mcp_manager,
            plugin_manager=plugin_manager,
            memory_service=memory_service,
            memory_repository=actual_memory_repository,
            context_builder=context_builder,
            checkpoint_path=database_path.with_name("checkpoints.db"),
            thread_id=thread_id,
        )

    @property
    def thread_id(self) -> ThreadId:
        return self._thread_id

    async def stream(self, message: str, *, realtime: bool = False) -> AsyncIterator[RuntimeEvent]:
        async with self._turn_lock:
            async with self._lifecycle_lock:
                await self._ensure_ready()
                turn_id = new_turn_id()
                user_item = UserMessageItem(message, turn_id)
                await self._repository.save_turn(
                    TurnRecord(turn_id, self._thread_id, TurnStatus.RUNNING, message)
                )
                initial = _initial_state(
                    self._thread_id,
                    turn_id,
                    self._settings,
                    user_item,
                    realtime=realtime,
                )
                if realtime:
                    self._realtime.activate(turn_id)
                events = self._run_graph(
                    initial,
                    turn_id,
                    message,
                    resumed=False,
                    realtime=realtime,
                )
                # First advancement only registers owned work and yields Started.
                # Close cannot pass the lifecycle lock before registration.
                first = await anext(events)
            async with aclosing(events):
                yield first
                async for event in events:
                    yield event

    async def steer(self, message: str) -> None:
        """Queue an additional user message for the currently streaming turn."""

        await self._realtime.steer(message)

    async def cancel_active(self) -> None:
        """Request cooperative cancellation of the currently streaming turn."""

        if self._active_run is not None:
            self._realtime.close_input()
            self._active_run.cancel()

    async def resume_pending(self) -> AsyncIterator[RuntimeEvent]:
        """Continue the latest checkpointed running turn, if one exists."""
        async with self._turn_lock:
            async with self._lifecycle_lock:
                await self._ensure_ready()
                turn = await self._repository.latest_running_turn(self._thread_id)
                if turn is None:
                    return
                config = self._graph_config(turn.id)
                assert self._checkpointer is not None
                checkpoint = await self._checkpointer.aget_tuple(config)
                initial: CorkiState | None = None
                if checkpoint is None:
                    existing = await self._repository.load_items(self._thread_id)
                    user_item = next(
                        (
                            item
                            for item in existing
                            if isinstance(item, UserMessageItem) and item.turn_id == turn.id
                        ),
                        None,
                    )
                    if user_item is None:
                        user_item = UserMessageItem(turn.user_input, turn.id)
                    initial = _initial_state(self._thread_id, turn.id, self._settings, user_item)
                events = self._run_graph(
                    initial,
                    turn.id,
                    turn.user_input,
                    resumed=True,
                    realtime=False,
                )
                first = await anext(events)
            async with aclosing(events):
                yield first
                async for event in events:
                    yield event

    async def _run_graph(
        self,
        initial: CorkiState | None,
        turn_id: TurnId,
        message: str,
        *,
        resumed: bool,
        realtime: bool,
    ) -> AsyncIterator[RuntimeEvent]:
        run = TurnRun(turn_id, self._settings.event_queue_size)
        run.start(lambda owned: self._execute_run(owned, initial, message, realtime=realtime))
        self._active_run = run
        if self._closed:
            run.cancel()
        terminal_sent = False
        try:
            yield TurnStarted(self._thread_id, turn_id, resumed=resumed)
            while not run.done.is_set() or not run.queue.empty():
                if not run.queue.empty():
                    yield run.queue.get_nowait()
                    continue
                event_waiter = asyncio.create_task(run.queue.get())
                done_waiter = asyncio.create_task(run.done.wait())
                waiters = (event_waiter, done_waiter)
                finished: set[asyncio.Task[Any]] = set()
                try:
                    finished, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for waiter in waiters:
                        if not waiter.done():
                            waiter.cancel()
                    await asyncio.gather(*waiters, return_exceptions=True)
                if event_waiter in finished:
                    yield event_waiter.result()
            terminal = run.result()
            terminal_sent = True
            yield terminal
            if isinstance(terminal, TurnCancelled):
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            run.cancel()
            await run.join()
            if not terminal_sent:
                terminal_sent = True
                yield run.result()
            raise
        finally:
            run.cancel()
            await run.join()
            if self._active_run is run:
                self._active_run = None

    async def _flush_realtime_inputs(self) -> None:
        items = self._realtime.unrecorded_items
        if items:
            await self._repository.append_items(self._thread_id, items)
            self._realtime.acknowledge(items)

    async def _execute_run(
        self, run: TurnRun, initial: CorkiState | None, message: str, *, realtime: bool
    ) -> RuntimeEvent:
        """Own work, cleanup, and the durable terminal independently of the UI."""
        turn_id = run.turn_id
        terminal: RuntimeEvent
        try:
            if run.cancel_requested:
                raise asyncio.CancelledError
            assert self._compiled is not None
            result = await self._compiled.ainvoke(
                initial,
                context=GraphRunContext(events=_QueueEventSink(run.queue), realtime=self._realtime),
                config=self._graph_config(turn_id),
            )
            if result["status"] == TurnStatus.COMPLETED.value:
                terminal = TurnCompleted(self._thread_id, turn_id, result["final_answer"] or "")
            else:
                terminal = TurnFailed(
                    self._thread_id,
                    turn_id,
                    result["error"] or "turn failed",
                    error_kind="evaluation",
                )
        except BaseException as exc:
            if isinstance(
                exc,
                (
                    asyncio.CancelledError,
                    getattr(graph_errors, "NodeCancelledError", asyncio.CancelledError),
                ),
            ):
                terminal = TurnCancelled(self._thread_id, turn_id)
            elif isinstance(exc, ModelError):
                terminal = TurnFailed(
                    self._thread_id,
                    turn_id,
                    str(exc),
                    error_kind=exc.kind.value,
                    retryable=exc.retryable,
                )
            elif isinstance(exc, Exception):
                terminal = TurnFailed(
                    self._thread_id,
                    turn_id,
                    f"{type(exc).__name__}: {exc}",
                    error_kind="internal",
                )
            else:
                raise
        finally:
            # Once selected, a terminal cannot be changed by a late cancel/close.
            # In particular, repeated cancellation must not interrupt persistence.
            run.finishing = True
            if realtime:
                self._realtime.close_input()

        try:
            if isinstance(terminal, TurnCancelled):
                try:
                    await self._process_manager.terminate_all()
                except Exception as exc:
                    run.cleanup_error = run.cleanup_error or exc
                    _LOG.exception("Process cleanup failed during turn cancellation")
            try:
                # Closing immediately after TurnStarted still preserves the input.
                if initial is not None and initial.get("pending_input_items"):
                    await self._repository.append_items(
                        self._thread_id, initial["pending_input_items"]
                    )
                if realtime:
                    await self._flush_realtime_inputs()
            except Exception as exc:
                run.cleanup_error = run.cleanup_error or exc
                if isinstance(terminal, TurnCancelled):
                    _LOG.exception("Input persistence failed during turn cancellation")
                else:
                    terminal = TurnFailed(
                        self._thread_id,
                        turn_id,
                        f"Input persistence failed: {exc}",
                        error_kind="storage",
                    )

            if isinstance(terminal, TurnCompleted):
                record = TurnRecord(
                    turn_id,
                    self._thread_id,
                    TurnStatus.COMPLETED,
                    message,
                    final_answer=terminal.final_answer,
                )
            elif isinstance(terminal, TurnCancelled):
                record = TurnRecord(turn_id, self._thread_id, TurnStatus.CANCELLED, message)
            else:
                assert isinstance(terminal, TurnFailed)
                record = TurnRecord(
                    turn_id,
                    self._thread_id,
                    TurnStatus.FAILED,
                    message,
                    error=terminal.error,
                )
            try:
                await self._repository.save_turn(record)
            except Exception as exc:
                run.cleanup_error = run.cleanup_error or exc
                _LOG.exception("Could not persist turn terminal")
                if not isinstance(terminal, TurnCancelled):
                    terminal = TurnFailed(
                        self._thread_id,
                        turn_id,
                        f"Terminal persistence failed: {exc}",
                        error_kind="storage",
                    )
            return terminal
        finally:
            if realtime:
                self._realtime.deactivate()

    def _graph_config(self, turn_id: TurnId) -> dict[str, object]:
        return {
            "configurable": {"thread_id": f"{self._thread_id}:{turn_id}"},
            "recursion_limit": self._settings.max_steps * 5 + 10,
        }

    async def _ensure_ready(self) -> None:
        if self._closed:
            raise RuntimeError("Corki runtime is closed")
        await self._ensure_thread()
        if self._compiled is not None:
            return
        async with self._ready_lock:
            if self._closed:
                raise RuntimeError("Corki runtime is closed")
            if self._compiled is not None:
                return
            await self._mcp_manager.start()
            if self._settings.tool_search_mode != "disabled" and any(
                spec.exposure.is_deferred for spec in self._registry.specs()
            ):
                existing = self._registry.get("tool_search")
                if existing is None:
                    self._registry.register(ToolSearchTool(self._registry))
                elif not isinstance(existing, ToolSearchTool):
                    raise ValueError("tool_search is reserved for harness tool discovery")

            # The context-manager returned by LangGraph is single use. Build a
            # fresh one for every attempt and publish no partially initialized
            # state, so a transient SQLite/setup failure can be retried safely.
            checkpoint_context = AsyncSqliteSaver.from_conn_string(str(self._checkpoint_path))
            checkpointer: AsyncSqliteSaver | None = None
            try:
                checkpointer = await checkpoint_context.__aenter__()
                await checkpointer.setup()
                self._checkpoint_path.chmod(0o600)
                compiled = self._graph.compile(checkpointer=checkpointer)
            except BaseException as exc:
                if checkpointer is not None:
                    with suppress(BaseException):
                        await checkpoint_context.__aexit__(type(exc), exc, exc.__traceback__)
                raise

            # Model-visible schemas and executable handlers must change as one
            # immutable unit. Seal only after every fallible startup step has
            # succeeded; failed initialization remains retryable.
            self._registry.seal()
            self._checkpoint_context = checkpoint_context
            self._checkpointer = checkpointer
            self._compiled = compiled
            if self._memory_service is not None:
                self._memory_service.start(self._thread_id)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._realtime.close_input()
            self._close_task = asyncio.create_task(self._close_resources(), name="corki-close")
        # A cancelled waiter must not abandon or interrupt shared teardown.
        await asyncio.shield(self._close_task)

    async def _close_resources(self) -> None:
        async with self._lifecycle_lock, self._ready_lock:
            error: BaseException | None = None
            active = self._active_run
            if active is not None:
                active.cancel()
                await active.join()
                error = active.error or active.cleanup_error
            for close in (
                self._process_manager.terminate_all,
                *((self._memory_service.aclose,) if self._memory_service is not None else ()),
                self._mcp_manager.aclose,
                self._plugin_manager.aclose,
                self._model.aclose,
                self._repository.close,
            ):
                try:
                    await close()
                except BaseException as exc:
                    error = error or exc
            if self._checkpointer is not None and self._checkpoint_context is not None:
                try:
                    await self._checkpoint_context.__aexit__(None, None, None)
                except BaseException as exc:
                    error = error or exc
                self._checkpointer = None
                self._checkpoint_context = None
            if error is not None:
                raise error

    async def _ensure_thread(self) -> None:
        if self._thread_created:
            return
        async with self._thread_lock:
            if not self._thread_created:
                await self._repository.create_thread(
                    self._thread_id, self._settings.working_directory
                )
                self._thread_created = True


def _initial_state(
    thread_id: ThreadId,
    turn_id: TurnId,
    settings: CorkiSettings,
    user_item: UserMessageItem,
    *,
    realtime: bool = False,
) -> CorkiState:
    return {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "cwd": str(settings.working_directory),
        "user_input": user_item.content,
        "request_items": (),
        "request_tools": (),
        "context_instructions": "",
        "pending_input_items": (user_item,),
        "last_model_items": (),
        "model_end_turn": None,
        "plan": (),
        "step_count": 0,
        "tool_call_count": 0,
        "route": "",
        "status": TurnStatus.CREATED.value,
        "final_answer": None,
        "error": None,
        "realtime_active": realtime,
        "model_interrupted": False,
    }


def _default_registry(settings: CorkiSettings, processes: ProcessManager) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ExecCommandTool(
            processes,
            default_yield_seconds=settings.command_yield_seconds,
            timeout_seconds=settings.command_timeout_seconds,
        )
    )
    registry.register(
        WriteStdinTool(processes, default_yield_seconds=settings.command_yield_seconds)
    )
    registry.register(ApplyPatchTool())
    registry.register(UpdatePlanTool())
    registry.register(ViewImageTool())
    return registry


def _capability_home(database_path: Path) -> Path:
    """Infer the Corki home for embedders that do not pass ``home_path``."""

    parent = database_path.expanduser().resolve().parent
    return parent.parent if parent.name == "sessions" else parent


def _create_model(settings: CorkiSettings, capabilities: ProviderCapabilities) -> ModelPort:
    common = {
        "api_key": settings.api_key,
        "base_url": settings.api_base,
        "capabilities": capabilities,
        "reasoning_effort": settings.reasoning_effort,
        "max_retries": settings.model_max_retries,
        "retry_base_seconds": settings.model_retry_base_seconds,
        "response_char_limit": settings.model_response_char_limit,
    }
    if settings.api_mode == "responses":
        return OpenAIResponsesModel(**common)
    return OpenAICompatibleModel(
        **common,
        thinking_enabled=settings.thinking_enabled,
    )
