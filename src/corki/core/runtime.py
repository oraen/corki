"""Durable streaming facade around the checkpointed LangGraph harness."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, Protocol

from langgraph import errors as graph_errors
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from corki.code_mode.service import CodeModeService
from corki.code_mode.tools import CodeModeExecTool, CodeModeWaitTool
from corki.config import CorkiSettings, MCPServerSettings
from corki.context import ContextBuilder, ContextWindowManager
from corki.context.interruptions import interrupted_turn_item
from corki.context.project import discover_project_root
from corki.core.checkpoint import checkpoint_serializer
from corki.core.checkpoint_lifecycle import close_checkpoint
from corki.core.graph import CorkiGraph, EventSink, GraphRunContext
from corki.core.state import CorkiState
from corki.core.turn_run import TurnRun
from corki.history_notes import HistoryNotesService, history_notes_enabled, history_notes_requested
from corki.history_notes.local import LocalHistoryNotesBackend
from corki.mcp import MCPManager, MCPServerMetadata
from corki.mcp.context import MCPContextContributor
from corki.media.images import ImagePolicy
from corki.media.preparation import MediaPreparation
from corki.memory import (
    LocalMemoryBackend,
    LongTermMemoryService,
    MemoryContextContributor,
    SQLiteMemoryRepository,
    memory_tools,
)
from corki.memory.pollution import mark_polluted
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
from corki.protocol.items import HostedToolItem, TurnAbortedItem, UserMessageItem
from corki.realtime import RealtimeController
from corki.realtime.context import RealtimeContextContributor
from corki.sessions import TurnRecord, TurnStatus
from corki.sessions.repository import SessionRepository
from corki.skills import SkillListTool, SkillReadTool, SkillService
from corki.skills.context import SkillContextContributor
from corki.storage import SQLiteSessionRepository
from corki.tools import FatalToolError, ToolExecutor, ToolRegistry
from corki.tools.builtin import (
    ApplyPatchTool,
    ExecCommandTool,
    ProcessManager,
    UpdatePlanTool,
    ViewImageTool,
    WriteStdinTool,
)
from corki.tools.search import ToolSearchTool
from corki.tools.search_cache import ToolSearchHandlerCache

_LOG = logging.getLogger(__name__)


class AgentRuntime(Protocol):
    async def stream(
        self, message: str, *, realtime: bool = False
    ) -> AsyncIterator[RuntimeEvent]: ...

    async def resume_pending(self) -> AsyncIterator[RuntimeEvent]: ...

    async def compact(self) -> AsyncIterator[RuntimeEvent]: ...

    async def steer(self, message: str) -> None: ...

    async def cancel_active(
        self, *, reason: Literal["interrupted", "replaced"] = "interrupted"
    ) -> None: ...

    def request_mcp_refresh(
        self,
        servers: tuple[MCPServerSettings, ...] | None = None,
        *,
        server_metadata: Mapping[str, MCPServerMetadata] | None = None,
    ) -> None: ...

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
        history_notes_client=None,
        history_notes_path: Path | None = None,
    ) -> None:
        self._settings = settings
        self._model = model
        self._repository = repository
        self._registry = registry
        self._search_owner = registry.create_owner()
        self._code_mode_owner = registry.create_owner()
        self._search_cache = ToolSearchHandlerCache()
        self._process_manager = process_manager
        self._mcp_manager = mcp_manager
        self._plugin_manager = plugin_manager
        self._memory_service = memory_service
        self._memory_repository = memory_repository
        self._thread_id = thread_id or new_thread_id()
        self._history_notes = None
        if history_notes_requested(settings):
            backend = (
                None
                if history_notes_enabled(settings)
                else LocalHistoryNotesBackend(
                    repository,
                    history_notes_path or checkpoint_path.with_name("history-notes.db"),
                    self._thread_id,
                )
            )
            self._history_notes = HistoryNotesService(
                settings, self._thread_id, client=history_notes_client, backend=backend
            )
            registry.replace_owned(registry.create_owner(), self._history_notes.tools)
        self._thread_created = False
        self._thread_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._ready_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._active_run: TurnRun | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._startup_task: asyncio.Task[None] | None = None
        self._checkpoint_context: Any | None = None
        self._checkpoint_cleanup_error: BaseException | None = None
        self._checkpoint_path = checkpoint_path
        self._checkpointer: AsyncSqliteSaver | None = None
        self._compiled: Any = None
        self._closed = False
        self._realtime = RealtimeController(settings.max_realtime_inputs)
        self._code_mode = None
        if settings.tool_mode != "direct":
            self._code_mode = CodeModeService(registry, max_calls=settings.max_tool_calls)
        self._media = MediaPreparation(
            ImagePolicy(
                settings.supports_image_input,
                settings.supports_image_detail_original,
                settings.unified_image_budget,
            ),
            supports_audio=settings.supports_audio_input,
        )
        executor = ToolExecutor(
            registry,
            output_char_budget=settings.tool_output_char_budget,
            media_preparation=self._media,
        )
        window_manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name=settings.model,
            truncation_policy=settings.model_context_info(settings.model).truncation_policy,
            context_window_tokens=settings.main_context_limits.raw_tokens,
            auto_compact_tokens=settings.auto_compact_tokens,
            auto_compact_token_limit_scope=settings.auto_compact_token_limit_scope,
            token_budget_enabled=settings.token_budget_enabled,
            token_budget=settings.token_budget,
            effective_context_window_percent=settings.main_context_limits.effective_percent,
            media_preparation=self._media,
            max_retries=settings.model_max_retries,
            retry_base_seconds=settings.model_retry_base_seconds,
            compact_prompt=settings.compact_prompt,
            history_notes=self._history_notes,
        )
        if settings.token_budget_enabled:
            from corki.tools.builtin.context import GetContextRemainingTool, NewContextTool

            registry.register(NewContextTool())
            registry.register(GetContextRemainingTool(window_manager, self._thread_id))
        self._graph = CorkiGraph(
            settings=settings,
            model=model,
            repository=repository,
            registry=registry,
            executor=executor,
            context_builder=context_builder,
            window_manager=window_manager,
            memory_repository=memory_repository,
            code_mode=self._code_mode,
            media_preparation=self._media,
            refresh_tools=self._refresh_tools,
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
        history_notes_client=None,
        mcp_server_metadata: Mapping[str, MCPServerMetadata] | None = None,
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
                context_window_tokens=settings.main_context_limits.raw_tokens,
                max_context_tokens=settings.skills_max_context_tokens,
                rules=settings.skills_config,
            )
            actual_registry.register(SkillListTool(skill_service))
            actual_registry.register(SkillReadTool(skill_service))
            contributors.append(
                SkillContextContributor(
                    skill_service, include_instructions=settings.skills_include_instructions
                )
            )
        mcp_settings = tuple(
            replace(server, cwd=server.cwd or settings.working_directory)
            for server in (*settings.mcp_servers, *plugin_manager.mcp_servers)
        )
        mcp_manager = MCPManager(
            mcp_settings,
            actual_registry,
            defer_tools=settings.tool_search_mode != "disabled",
            server_metadata=mcp_server_metadata,
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
            capabilities,
            supports_native_tool_search=settings.tool_search_mode == "native",
            supports_native_freeform=settings.tool_freeform_mode == "native",
            supports_native_namespaces=settings.tool_namespace_mode == "native",
            supports_audio_input=settings.supports_audio_input,
            supports_encrypted_tool_output=(
                settings.supports_encrypted_tool_output or history_notes_enabled(settings)
            ),
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
            history_notes_client=history_notes_client,
            history_notes_path=database_path.with_name(database_path.stem + ".history-notes.db"),
        )

    @property
    def thread_id(self) -> ThreadId:
        return self._thread_id

    async def stream(self, message: str, *, realtime: bool = False) -> AsyncIterator[RuntimeEvent]:
        if not isinstance(message, str):
            raise TypeError("message must be a string; use compact() for manual compaction")
        async with aclosing(self._start_turn(message, realtime=realtime)) as events:
            async for event in events:
                yield event

    async def compact(self) -> AsyncIterator[RuntimeEvent]:
        """Replace active work with a standalone local compaction turn."""
        await self.cancel_active(reason="replaced")
        async with aclosing(self._start_turn(None)) as events:
            async for event in events:
                yield event

    async def _start_turn(
        self, message: str | None, *, realtime: bool = False
    ) -> AsyncIterator[RuntimeEvent]:
        operation = "compact" if message is None else "normal"
        async with self._turn_lock:
            # Serialize admission, not observation. The previous worker owns
            # persistence/cleanup; its event iterator may remain paused forever.
            if operation == "compact":
                # A queued predecessor may have started since compact() first
                # requested cancellation. Replace the actual current worker.
                await self.cancel_active(reason="replaced")
            if self._active_run is not None:
                await self._active_run.done.wait()
            async with self._lifecycle_lock:
                await self._ensure_ready()
                turn_id = new_turn_id()
                user_item = UserMessageItem(message, turn_id) if message is not None else None
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
                    message or "",
                    resumed=False,
                    realtime=realtime,
                    operation=operation,
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

    async def cancel_active(
        self, *, reason: Literal["interrupted", "replaced"] = "interrupted"
    ) -> None:
        """Request cooperative cancellation of active work, including startup."""

        if reason not in {"interrupted", "replaced"}:
            raise ValueError("cancel reason must be interrupted or replaced")
        startup = self._startup_task
        if startup is not None and not startup.done() and not startup.cancelling():
            startup.cancel()
        if self._active_run is not None:
            self._realtime.close_input()
            self._active_run.cancel(reason=reason)

    async def resume_pending(self) -> AsyncIterator[RuntimeEvent]:
        """Continue the latest checkpointed running turn, if one exists."""
        async with self._turn_lock:
            if self._active_run is not None:
                await self._active_run.done.wait()
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
                    if user_item is None and turn.operation != "compact":
                        user_item = UserMessageItem(turn.user_input, turn.id)
                    if turn.operation == "compact":
                        user_item = None
                    initial = _initial_state(self._thread_id, turn.id, self._settings, user_item)
                events = self._run_graph(
                    initial,
                    turn.id,
                    turn.user_input,
                    resumed=True,
                    realtime=False,
                    operation=turn.operation,
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
        operation: str = "normal",
    ) -> AsyncIterator[RuntimeEvent]:
        run = TurnRun(turn_id, self._settings.event_queue_size)
        run.start(
            lambda owned: self._execute_run(
                owned, initial, message, realtime=realtime, resumed=resumed, operation=operation
            )
        )
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
            prepared = await self._media.prepare_items(items)
            await self._repository.append_items(self._thread_id, prepared)
            self._realtime.acknowledge(items)

    async def _execute_run(
        self,
        run: TurnRun,
        initial: CorkiState | None,
        message: str,
        *,
        realtime: bool,
        resumed: bool,
        operation: str = "normal",
    ) -> RuntimeEvent:
        """Own work, cleanup, and the durable terminal independently of the UI."""
        turn_id = run.turn_id
        terminal: RuntimeEvent | None = None
        writing_start = not resumed
        try:
            # Own the very first durable write, not only graph execution. A
            # cancelled observer must never leave an unowned RUNNING record.
            if writing_start:
                await self._repository.save_turn(
                    TurnRecord(
                        turn_id, self._thread_id, TurnStatus.RUNNING, message, operation=operation
                    )
                )
            writing_start = False
            if run.cancel_requested:
                raise asyncio.CancelledError
            if resumed and any(
                isinstance(item, TurnAbortedItem) and item.turn_id == turn_id
                for item in await self._repository.load_items(self._thread_id)
            ):
                # The marker may commit before terminal persistence fails. Do
                # not replay a checkpoint after that durable cancellation intent.
                run.cancel_reason = "interrupted"
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
            elif writing_start and isinstance(exc, Exception):
                terminal = TurnFailed(
                    self._thread_id,
                    turn_id,
                    f"Initial Turn persistence failed: {exc}",
                    error_kind="storage",
                )
            elif isinstance(exc, ModelError):
                terminal = TurnFailed(
                    self._thread_id,
                    turn_id,
                    str(exc),
                    error_kind=exc.kind.value,
                    retryable=exc.retryable,
                )
            elif isinstance(exc, FatalToolError):
                terminal = TurnFailed(self._thread_id, turn_id, str(exc), error_kind="tool")
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

        code_mode_deactivated = False
        try:
            if isinstance(terminal, TurnCancelled):
                try:
                    await self._process_manager.terminate_all()
                except Exception as exc:
                    run.cleanup_error = run.cleanup_error or exc
                    _LOG.exception("Process cleanup failed during turn cancellation")
                if self._code_mode is not None:
                    try:
                        await self._code_mode.deactivate(interrupt=True)
                    except Exception as exc:
                        run.cleanup_error = run.cleanup_error or exc
                        _LOG.exception("Code Mode cleanup failed during turn cancellation")
                    code_mode_deactivated = True
            try:
                # Closing immediately after TurnStarted still preserves the input.
                if initial is not None and initial.get("pending_input_items"):
                    await self._repository.append_items(
                        self._thread_id,
                        await self._media.prepare_items(initial["pending_input_items"]),
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

            if (
                isinstance(terminal, TurnCancelled)
                and run.cancel_reason == "interrupted"
                and self._settings.agent_interrupt_message_enabled
            ):
                try:
                    await self._repository.append_items(
                        self._thread_id, (interrupted_turn_item(turn_id),)
                    )
                except Exception as exc:
                    run.cleanup_error = run.cleanup_error or exc
                    _LOG.exception("Could not persist interrupted-turn history marker")

            if isinstance(terminal, TurnCompleted):
                record = TurnRecord(
                    turn_id,
                    self._thread_id,
                    TurnStatus.COMPLETED,
                    message,
                    final_answer=terminal.final_answer,
                    operation=operation,
                )
            elif isinstance(terminal, TurnCancelled):
                record = TurnRecord(
                    turn_id, self._thread_id, TurnStatus.CANCELLED, message, operation=operation
                )
            else:
                assert isinstance(terminal, TurnFailed)
                record = TurnRecord(
                    turn_id,
                    self._thread_id,
                    TurnStatus.FAILED,
                    message,
                    error=terminal.error,
                    operation=operation,
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
            try:
                if self._code_mode is not None and not code_mode_deactivated:
                    await self._code_mode.deactivate(
                        interrupt=not isinstance(terminal, TurnCompleted)
                    )
            except Exception as exc:
                run.cleanup_error = run.cleanup_error or exc
                _LOG.exception("Code Mode cleanup failed after turn terminal selection")
            finally:
                if realtime:
                    self._realtime.deactivate()

    def _graph_config(self, turn_id: TurnId) -> dict[str, object]:
        return {
            "configurable": {"thread_id": f"{self._thread_id}:{turn_id}"},
            "recursion_limit": (
                2**63 - 1
                if self._settings.model_unbounded_connection_retries
                else self._settings.max_steps * (5 + 2 * self._settings.model_max_retries) + 10
            ),
        }

    async def _ensure_ready(self) -> None:
        if self._closed:
            raise RuntimeError("Corki runtime is closed")
        if self._compiled is not None:
            return
        async with self._ready_lock:
            if self._closed:
                raise RuntimeError("Corki runtime is closed")
            if self._compiled is not None:
                return
            startup = asyncio.create_task(self._initialize(), name="corki-initialize")
            self._startup_task = startup
            try:
                await asyncio.shield(startup)
            except asyncio.CancelledError:
                if not startup.done() and not startup.cancelling():
                    startup.cancel()
                # The caller and close may both cancel, including during
                # rollback. Neither may abandon or interrupt owned cleanup.
                while not startup.done():
                    try:
                        await asyncio.shield(startup)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not startup.cancelled() and startup.exception() is not None:
                    _LOG.warning(
                        "Initialization failed during cancellation", exc_info=startup.exception()
                    )
                raise
            finally:
                self._startup_task = None

    async def _initialize(self) -> None:
        initializing = asyncio.current_task()
        assert initializing is not None
        await self._ensure_thread()
        if self._settings.memories_enabled and self._settings.memories_disable_on_external_context:
            history = await self._repository.load_items(self._thread_id)
            if any(
                isinstance(item, HostedToolItem)
                and json.loads(item.payload_json)["type"] == "function_call_output"
                for item in history
            ):
                await mark_polluted(self._memory_repository, self._settings, self._thread_id)
        if self._closed or initializing.cancelling():
            raise asyncio.CancelledError
        if self._code_mode is not None and not CodeModeService.available():
            if (
                self._settings.tool_mode == "code_mode_only"
                or self._settings.code_mode_disable_fallback
            ):
                raise ValueError("Code Mode engine unavailable; install corki[code-mode]")
            _LOG.warning("Code Mode engine unavailable; falling back to direct tools")
            self._code_mode = None
            self._graph._code_mode = None
        await self._mcp_manager.start()
        if self._closed or initializing.cancelling():
            raise asyncio.CancelledError
        self._sync_tool_search()
        self._sync_code_mode_tools()

        # The context-manager returned by LangGraph is single use. Build a
        # fresh one for every attempt and publish no partially initialized
        # state, so a transient SQLite/setup failure can be retried safely.
        checkpoint_context = AsyncSqliteSaver.from_conn_string(str(self._checkpoint_path))
        checkpointer: AsyncSqliteSaver | None = None
        try:
            checkpointer = await checkpoint_context.__aenter__()
            checkpointer.serde = checkpoint_serializer()
            checkpointer.jsonplus_serde = checkpointer.serde
            await checkpointer.setup()
            if self._closed or initializing.cancelling():
                raise asyncio.CancelledError
            self._checkpoint_path.chmod(0o600)
            compiled = self._graph.compile(checkpointer=checkpointer)
        except BaseException as exc:
            if checkpointer is not None:
                try:
                    await close_checkpoint(checkpoint_context, checkpointer, exc)
                except BaseException as cleanup_error:
                    # Preserve setup failure/cancellation now, but do not lose
                    # the cleanup diagnostic if a later initialization succeeds.
                    self._checkpoint_cleanup_error = self._checkpoint_cleanup_error or cleanup_error
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

    def request_mcp_refresh(
        self,
        servers: tuple[MCPServerSettings, ...] | None = None,
        *,
        server_metadata: Mapping[str, MCPServerMetadata] | None = None,
    ) -> None:
        """Queue reconnect/config replacement for preparation or MCP call admission.

        Model schemas remain frozen; not-yet-admitted MCP calls resolve the current
        binding. Running calls are never replayed or rebound. Callers supply the
        complete desired server configuration when replacing settings.
        """
        if self._closed:
            raise RuntimeError("Corki runtime is closed")
        self._mcp_manager.request_refresh(servers, server_metadata=server_metadata)

    async def _refresh_tools(self) -> None:
        await self._mcp_manager.refresh_if_dirty()
        self._sync_tool_search()
        self._sync_code_mode_tools()

    def _sync_code_mode_tools(self) -> None:
        if self._code_mode is None:
            return
        owned = self._registry.owned_names(self._code_mode_owner)
        controls = []
        adopt = []
        for name, tool_type in (("exec", CodeModeExecTool), ("wait", CodeModeWaitTool)):
            existing = self._registry.get(name)
            if existing is not None and (
                not isinstance(existing, tool_type) or existing.service is not self._code_mode
            ):
                raise ValueError(f"{name} is reserved for Code Mode")
            if existing is not None and name not in owned:
                adopt.append(name)
            controls.append(tool_type(self._code_mode))
        if (
            not adopt
            and owned
            and all(tool.spec == self._registry.spec(tool.spec.name) for tool in controls)
        ):
            return
        for name in adopt:
            self._registry.unregister(name)
        self._registry.replace_owned(self._code_mode_owner, tuple(controls))

    def _sync_tool_search(self) -> None:
        enabled = self._settings.tool_search_mode != "disabled" and bool(
            self._registry.deferred_entries()
        )
        existing = self._registry.get("tool_search")
        owned = self._registry.owned_names(self._search_owner)
        if not enabled and not owned:
            return
        if existing is not None and "tool_search" not in owned:
            if not isinstance(existing, ToolSearchTool):
                if enabled:
                    raise ValueError("tool_search is reserved for harness tool discovery")
                return
            # Adopt a caller-supplied harness search tool during composition only.
            self._registry.unregister("tool_search")
        if enabled:
            candidate = self._search_cache.get_or_build(self._registry)
            if candidate is existing:
                return
            self._registry.replace_owned(self._search_owner, (candidate,))
        elif owned:
            self._registry.replace_owned(self._search_owner, ())

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._realtime.close_input()
            startup = self._startup_task
            if startup is not None and not startup.done() and not startup.cancelling():
                startup.cancel()
            self._close_task = asyncio.create_task(self._close_resources(), name="corki-close")
        # A cancelled waiter must not abandon or interrupt shared teardown.
        await asyncio.shield(self._close_task)

    async def _close_resources(self) -> None:
        async with self._lifecycle_lock, self._ready_lock:
            error = self._checkpoint_cleanup_error
            active = self._active_run
            if active is not None:
                active.cancel()
                await active.join()
                error = error or active.error or active.cleanup_error
            for close in (
                *((self._code_mode.aclose,) if self._code_mode is not None else ()),
                self._process_manager.terminate_all,
                *((self._memory_service.aclose,) if self._memory_service is not None else ()),
                self._mcp_manager.aclose,
                self._plugin_manager.aclose,
                *((self._history_notes.aclose,) if self._history_notes is not None else ()),
                self._model.aclose,
                self._repository.close,
            ):
                try:
                    await close()
                except BaseException as exc:
                    error = error or exc
            if self._checkpointer is not None and self._checkpoint_context is not None:
                try:
                    await close_checkpoint(self._checkpoint_context, self._checkpointer)
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
    user_item: UserMessageItem | None,
    *,
    realtime: bool = False,
) -> CorkiState:
    return {
        "thread_id": thread_id,
        "operation": "compact" if user_item is None else "normal",
        "turn_id": turn_id,
        "cwd": str(settings.working_directory),
        "user_input": user_item.content if user_item is not None else "",
        "request_items": (),
        "request_tools": (),
        "context_instructions": "",
        "pending_input_items": (user_item,) if user_item is not None else (),
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
    registry.register(
        ViewImageTool(
            supports_images=settings.supports_image_input,
            supports_original=settings.supports_image_detail_original,
            unified_budget=settings.unified_image_budget,
        )
    )
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
        "request_max_retries": settings.model_request_max_retries,
        "retry_base_seconds": settings.model_retry_base_seconds,
        "response_char_limit": settings.model_response_char_limit,
    }
    if settings.api_mode == "responses":
        return OpenAIResponsesModel(**common)
    return OpenAICompatibleModel(
        **common,
        thinking_enabled=settings.thinking_enabled,
    )
