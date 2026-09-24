"""Durable streaming facade around the checkpointed LangGraph harness."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
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
from corki.config.exec_policy import ExecPolicySnapshot
from corki.config.execution_requirements import validate_effective_approval_requirements
from corki.config.layers import LocalConfigState
from corki.config.managed_mcp import MCPRequirementsSnapshot, load_mcp_requirements
from corki.config.mcp_requirements import MCPRequirements
from corki.config.plugins import LayerDisabledPlugins, LayerPluginDirectories, plugin_selection
from corki.context import ContextBuilder, ContextWindowManager, local_time
from corki.context.deferred_tools import DeferredToolsContextContributor
from corki.context.extensions import ContextContributor
from corki.context.instruction_manager import InstructionManager
from corki.context.interruptions import interrupted_turn_item
from corki.context.project import discover_project_root
from corki.context.user_instructions import (
    HomeUserInstructionsProvider,
    Instructions,
    UserInstructionsProvider,
)
from corki.core.checkpoint import checkpoint_serializer
from corki.core.checkpoint_lifecycle import close_checkpoint, setup_checkpoint
from corki.core.construction import create_owned, rollback_registry, synchronous_rollback
from corki.core.graph import CorkiGraph, EventSink, GraphRunContext
from corki.core.model_settings import (
    ThreadSettingsController,
    bind_model_settings,
    capture_model_settings,
)
from corki.core.state import CorkiState
from corki.core.step_settings import (
    ModelMetadataResolver,
    StepSettingsState,
    TurnSettingsController,
    resolve_model_metadata,
)
from corki.core.step_tools import StepToolState
from corki.core.turn_run import TurnRun
from corki.execution.backend import resolve_execution_permissions
from corki.execution.rules import ExecPolicyHandle
from corki.history_notes import HistoryNotesService, history_notes_requested
from corki.history_notes.local import LocalHistoryNotesBackend
from corki.mcp import MCPManager, MCPServerMetadata
from corki.mcp.approval_persistence import MCPApprovalPersistence
from corki.mcp.catalog import MCPCatalog, MCPRegistration
from corki.mcp.context import MCPContextContributor
from corki.mcp.discovery import MCPToolCatalogEntry
from corki.mcp.elicitation import ElicitationHandler
from corki.mcp.input_requirements import collect_input_requirements_async
from corki.mcp.prewarm import MCPPrewarm
from corki.mcp.runtime_environment import MCPRuntimeContext
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.media.images import ImagePolicy
from corki.media.preparation import MediaPreparation
from corki.memory import (
    LocalMemoryBackend,
    LongTermMemoryService,
    MemoryContextContributor,
    SQLiteMemoryRepository,
    memory_tools,
)
from corki.memory.permissions import MemoryPermissionSnapshot
from corki.memory.pollution import mark_polluted
from corki.memory.repository import MemoryRepository
from corki.memory.reset import MemoryResetter
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
from corki.plugins.manager import discover_manifests
from corki.plugins.store import PluginStoreSource
from corki.protocol.collaboration import CollaborationMode
from corki.protocol.events import (
    RuntimeEvent,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    WarningEvent,
)
from corki.protocol.execution_identity import ExecutionIdentity
from corki.protocol.ids import SessionId, ThreadId, TurnId, new_thread_id, new_turn_id
from corki.protocol.input_images import validate_image_positions
from corki.protocol.input_mentions import InputMention, validate_mentions
from corki.protocol.items import ConversationItem, HostedToolItem, TurnAbortedItem, UserMessageItem
from corki.protocol.memory import ThreadMemoryMode
from corki.protocol.session_source import DEFAULT_SESSION_SOURCE, SessionSource, SessionSourceKind
from corki.protocol.settings import (
    UNSET,
    ModelSettingsSnapshot,
    ThreadModelSettings,
    TurnSettingsUpdateResult,
    UnsetSetting,
)
from corki.protocol.terminals import BackgroundTerminalInfo
from corki.protocol.tool_exposure import ToolNamespacePolicy
from corki.protocol.tools import validate_image_attachments
from corki.realtime import RealtimeController
from corki.realtime.context import RealtimeContextContributor
from corki.sessions import TurnRecord, TurnStatus
from corki.sessions.archive import ThreadArchiveController, ThreadArchiveStore
from corki.sessions.display import (
    DisplayItemsCursor,
    DisplayItemsPage,
    DisplayTurnsCursor,
    DisplayTurnsPage,
)
from corki.sessions.memory_mode import ThreadMemoryController
from corki.sessions.models import DisplayHistory, ThreadRecord
from corki.sessions.repository import SessionRepository
from corki.skills import SkillListTool, SkillReadTool, SkillService
from corki.skills.context import SkillContextContributor
from corki.storage import SQLiteSessionRepository
from corki.storage.thread_archive import SQLiteThreadArchiveStore
from corki.storage.thread_writer import ThreadWriterLease
from corki.storage.volatile import VolatileSessionRepository
from corki.tools import FatalToolError, ToolExecutor, ToolRegistry
from corki.tools.builtin import (
    ApplyPatchTool,
    ExecCommandTool,
    ProcessManager,
    UpdatePlanTool,
    ViewImageTool,
    WriteStdinTool,
)
from corki.tools.builtin.user_input import RequestUserInputTool
from corki.tools.router import ToolRouterBuilder, search_enabled
from corki.tools.search import ToolSearchTool
from corki.tools.search_cache import ToolSearchHandlerCache

_LOG = logging.getLogger(__name__)


class AgentRuntime(Protocol):
    def respond_user_input(self, turn_id: str, call_id: str, response: object) -> bool: ...
    def cancel_user_input(self, turn_id: str, call_id: str) -> bool: ...

    @property
    def thread_settings(self) -> ModelSettingsSnapshot: ...

    @property
    def active_turn_settings(self) -> ModelSettingsSnapshot | None: ...

    async def update_turn_settings(
        self,
        turn_id: str,
        *,
        model: str | None = None,
        reasoning_effort: str | None | UnsetSetting = UNSET,
        reasoning_summary: str | None = None,
        service_tier: str | None | UnsetSetting = UNSET,
    ) -> TurnSettingsUpdateResult: ...

    async def update_thread_settings(
        self,
        *,
        model: str | None = None,
        reasoning_effort: str | None | UnsetSetting = UNSET,
        reasoning_summary: str | None = None,
        service_tier: str | None | UnsetSetting = UNSET,
        collaboration_mode: CollaborationMode | None = None,
        personality: str | None | UnsetSetting = UNSET,
    ) -> ModelSettingsSnapshot: ...

    def set_mcp_elicitation_handler(self, handler: ElicitationHandler | None) -> None: ...

    def respond_mcp_elicitation(
        self,
        server_name: str,
        request_id: str,
        action: str,
        *,
        content: Any = None,
        meta: Any = None,
    ) -> None: ...

    async def stream(
        self,
        message: str,
        *,
        realtime: bool = False,
        mentions: tuple[InputMention, ...] = (),
        attachments=(),
        image_positions=(),
    ) -> AsyncIterator[RuntimeEvent]: ...

    async def resume_pending(self) -> AsyncIterator[RuntimeEvent]: ...

    async def load_display_history(self) -> tuple[ConversationItem, ...]: ...

    async def load_display_snapshot(self) -> DisplayHistory: ...

    async def compact(self) -> AsyncIterator[RuntimeEvent]: ...

    async def steer(
        self,
        message: str,
        *,
        mentions: tuple[InputMention, ...] = (),
        attachments=(),
        image_positions=(),
    ) -> None: ...

    def take_unsubmitted_inputs(self) -> tuple[UserMessageItem, ...]: ...

    async def cancel_active(
        self, *, reason: Literal["interrupted", "replaced"] = "interrupted"
    ) -> None: ...

    def request_mcp_refresh(
        self,
        servers: tuple[MCPServerSettings, ...] | None = None,
        *,
        server_metadata: Mapping[str, MCPServerMetadata] | None = None,
    ) -> None: ...

    async def mcp_tool_catalog(self) -> tuple[MCPToolCatalogEntry, ...]: ...

    def request_mcp_reconcile(
        self,
        servers: tuple[MCPServerSettings, ...] | None = None,
        *,
        server_metadata: Mapping[str, MCPServerMetadata] | None = None,
    ) -> None: ...

    async def aclose(self) -> None: ...

    async def stream_close(self) -> AsyncIterator[RuntimeEvent]: ...

    async def list_background_terminals(self) -> tuple[BackgroundTerminalInfo, ...]: ...

    async def terminate_background_terminal(self, process_id: str) -> bool: ...

    async def clean_background_terminals(self) -> None: ...

    async def reset_memory(self) -> tuple[Path, ...]: ...

    async def set_thread_memory_mode(
        self, mode: ThreadMemoryMode | str, *, thread_id: str | None = None
    ) -> None: ...

    @property
    def memory_reset_targets(self) -> tuple[Path, ...]: ...


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
        writer_database_path: Path | None = None,
        archive_store: ThreadArchiveStore | None = None,
        include_archived: bool = False,
        ephemeral: bool = False,
        thread_id: ThreadId | None = None,
        session_id: SessionId | None = None,
        session_source: SessionSource = DEFAULT_SESSION_SOURCE,
        history_notes_path: Path | None = None,
        session_start_source: Literal["startup", "clear"] | None = None,
        fork_from_thread_id: ThreadId | None = None,
        fork_before_user_message: int | None = None,
        fork_source_repository: SessionRepository | None = None,
        memory_resetter: MemoryResetter | None = None,
        mcp_requirements_snapshot: MCPRequirementsSnapshot | None = None,
        model_metadata_resolver: ModelMetadataResolver = resolve_model_metadata,
        exec_policy_config_folders: tuple[Path, ...] = (),
        inherited_exec_policy: ExecPolicySnapshot | ExecPolicyHandle | None = None,
        skill_service: SkillService | None = None,
        skill_tool_owner: object | None = None,
        plugin_roots_base: tuple[Path | PluginStoreSource, ...] | None = None,
        plugin_catalog_base: MCPCatalog | None = None,
        plugin_discovery_enabled: bool = False,
    ) -> None:
        from corki.storage.forks import validate_fork

        validate_fork(
            fork_from_thread_id,
            fork_before_user_message,
            session_start_source,
            fork_source_repository,
        )
        if session_start_source not in (None, "startup", "clear"):
            raise ValueError("session_start_source must be startup, clear, or None")
        if not isinstance(session_source, SessionSource):
            raise ValueError("session source must be host-validated")
        if not isinstance(ephemeral, bool):
            raise ValueError("ephemeral must be a host-validated boolean")
        if ephemeral != isinstance(repository, VolatileSessionRepository):
            raise ValueError("ephemeral execution requires private volatile session storage")
        if ephemeral and (archive_store is not None or memory_repository is not None):
            raise ValueError("ephemeral sessions cannot bind persistent archive or memory state")
        self._ephemeral = ephemeral
        self._session_source = session_source
        self._instruction_manager = context_builder.instruction_manager
        self._settings = settings
        self._hooks_reload_default = (
            settings.hooks_enabled
            if settings._hooks_reload_default is None
            else settings._hooks_reload_default
        )
        if inherited_exec_policy is not None and (
            not session_source.is_non_root_agent
            or not isinstance(inherited_exec_policy, (ExecPolicySnapshot, ExecPolicyHandle))
            or settings.execution_permissions is None
        ):
            raise ValueError("only a configured non-root host may inherit an exec policy")
        self._inherited_exec_policy = (
            inherited_exec_policy if isinstance(inherited_exec_policy, ExecPolicyHandle) else None
        )
        inherited_snapshot = (
            inherited_exec_policy.snapshot
            if isinstance(inherited_exec_policy, ExecPolicyHandle)
            else inherited_exec_policy
        )
        self._execution_permissions_input = (
            replace(settings.execution_permissions, exec_policy_snapshot=inherited_snapshot)
            if settings.execution_permissions is not None
            else None
        )
        self._exec_policy_config_folders = exec_policy_config_folders
        self._service_tier_warning_pending = settings.service_tier_warning
        self._execution_warnings_pending: tuple[str, ...] = ()
        self._configuration_warnings_pending = (
            settings.configuration.warnings if settings.configuration is not None else ()
        )
        self._model = model
        self._repository = repository
        if not isinstance(include_archived, bool):
            raise ValueError("include_archived must be a host-validated boolean")
        self._include_archived = include_archived
        self._archive_store = archive_store or (
            SQLiteThreadArchiveStore(repository.path)
            if not ephemeral and isinstance(repository, SQLiteSessionRepository)
            else None
        )
        self._registry = registry
        registry.configure_namespace_policy(
            ToolNamespacePolicy(
                settings.code_mode_direct_only_tool_namespaces,
                settings.code_mode_excluded_tool_namespaces,
            )
        )
        self._search_owner = registry.create_owner()
        self._code_mode_owner = registry.create_owner()
        self._search_cache = ToolSearchHandlerCache()
        self._process_manager = process_manager
        self._mcp_manager = mcp_manager
        self._mcp_prewarm = MCPPrewarm(mcp_manager)
        self._mcp_requirements_snapshot = mcp_requirements_snapshot
        self._plugin_manager = plugin_manager
        self._skill_service = skill_service
        self._skill_tool_owner = skill_tool_owner
        self._pending_skill_service: SkillService | None = None
        self._pending_plugins = None
        self._plugin_roots_base = plugin_roots_base
        self._plugin_catalog_base = plugin_catalog_base
        self._plugin_discovery_enabled = plugin_discovery_enabled
        self._plugin_dirs_override = (
            None
            if isinstance(settings.plugin_dirs, LayerPluginDirectories)
            else settings.plugin_dirs
        )
        self._disabled_plugins_override = (
            None
            if isinstance(settings.disabled_plugins, LayerDisabledPlugins)
            else settings.disabled_plugins
        )
        self._memory_service = memory_service
        self._memory_repository = memory_repository
        self._memory_resetter = memory_resetter
        self._thread_id = thread_id or new_thread_id()
        self._thread_archive = (
            ThreadArchiveController(self._archive_store, self._thread_id, self.aclose)
            if self._archive_store is not None
            else None
        )
        self._writer = (
            None
            if ephemeral
            else ThreadWriterLease(
                writer_database_path
                or (
                    repository.path
                    if isinstance(repository, SQLiteSessionRepository)
                    else checkpoint_path
                ),
                self._thread_id,
            )
        )
        self._thread_settings = ThreadSettingsController(settings, repository, self._thread_id)
        self._initial_session_id = (
            session_id if session_id is not None else SessionId(str(self._thread_id))
        )
        self._execution_identity: ExecutionIdentity | None = None
        self._history_notes = None
        if history_notes_requested(settings):
            backend = LocalHistoryNotesBackend(
                repository,
                history_notes_path or checkpoint_path.with_name("history-notes.db"),
                self._thread_id,
            )
            self._history_notes = HistoryNotesService(settings, self._thread_id, backend=backend)
            registry.replace_owned(registry.create_owner(), self._history_notes.tools)
        self._thread_created = False
        self._start_source_resolved = False
        self._requested_start_source = session_start_source
        self._fork_source = fork_from_thread_id
        self._fork_before = fork_before_user_message
        self._fork_repository = fork_source_repository
        self._fork_snapshot = None
        self._thread_lock = asyncio.Lock()
        self._process_manager.set_identity_resolver(self._ensure_thread)
        self._thread_memory = ThreadMemoryController(
            repository, self._thread_id, self._ensure_thread
        )
        self._turn_lock = asyncio.Lock()
        self._ready_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._active_run: TurnRun | None = None
        self._close_task: asyncio.Task[None] | None = None
        from corki.core.session_end_hooks import ShutdownEvents

        self._shutdown_events = ShutdownEvents()
        self._close_storage_pending = False
        self._pending_terminals: dict[TurnId, TurnRecord] = {}
        self._startup_task: asyncio.Task[None] | None = None
        self._checkpoint_context: Any | None = None
        self._checkpoint_cleanup_error: BaseException | None = None
        self._checkpoint_path = checkpoint_path
        self._checkpointer: AsyncSqliteSaver | None = None
        self._compiled: Any = None
        self._closed = False
        self._turn_updates = TurnSettingsController(
            settings=lambda: self._thread_settings.settings,
            active=lambda: self._active_run,
            closed=lambda: self._closed,
            lifecycle_lock=self._lifecycle_lock,
            resolver=model_metadata_resolver,
        )
        self._realtime = RealtimeController(settings.max_realtime_inputs)
        self._unsubmitted_inputs: tuple[UserMessageItem, ...] = ()
        self._input_flush_pending = False
        # The session owner survives direct turns; engine processes remain lazy.
        self._code_mode = CodeModeService(
            registry, max_calls=settings.max_tool_calls, elicitations=mcp_manager.elicitations
        )
        self._tool_router = ToolRouterBuilder(
            registry,
            self._code_mode,
            self._search_cache,
            self._search_owner,
            self._code_mode_owner,
            namespace_tools=getattr(
                model,
                "capabilities",
                resolve_capabilities(
                    base_url=settings.api_base,
                    api_mode=settings.api_mode,
                    provider_name=settings.provider_name,
                ),
            ).namespace_tools,
        )
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
            remote_request_options={
                "thread_id": str(self._thread_id),
                "content_item_kinds": settings.content_item_kinds,
                "tool_search_mode": settings.tool_search_mode,
                "tool_namespace_mode": settings.tool_namespace_mode,
                "tool_freeform_mode": settings.tool_freeform_mode,
                "reasoning_effort": settings.reasoning_effort,
                "reasoning_summary": settings.reasoning_summary,
                "service_tier": settings.session_service_tier,
                "fast_mode_enabled": settings.fast_mode,
                "model_info": settings.model_context_info(settings.model),
            },
            model_context_lookup=settings.model_context_info,
        )
        if settings.token_budget_enabled:
            from corki.tools.builtin.context import GetContextRemainingTool, NewContextTool

            registry.register(NewContextTool())
            registry.register(GetContextRemainingTool(window_manager, self._thread_id))
        if settings.deferred_tool_world_state and settings.tool_search_mode != "disabled":
            context_builder = context_builder.with_step_contributor(
                DeferredToolsContextContributor()
            )
        context_builder = context_builder.with_step_contributor(MCPContextContributor(mcp_manager))
        context_builder = context_builder.with_execution_rules(process_manager.approvals.rules)
        context_builder = context_builder.with_managed_instructions(
            mcp_requirements_snapshot.developer_instructions
            if mcp_requirements_snapshot is not None
            else None,
            enabled=not session_source.is_basic_guardian,
        )
        self._graph = CorkiGraph(
            mcp_manager=mcp_manager,
            plugins=plugin_manager.plugins,
            session_shell=process_manager.shell,
            settings=settings,
            model=model,
            repository=repository,
            registry=registry,
            executor=executor,
            context_builder=context_builder,
            window_manager=window_manager,
            memory_repository=memory_repository,
            code_mode=self._code_mode,
            tool_router=self._tool_router,
            media_preparation=self._media,
            refresh_tools=self._refresh_tools,
            refresh_input_tools=self._refresh_input_tools,
        )
        self._graph._stop_hooks.start_source = "resume" if thread_id is not None else "startup"
        from corki.skills.mcp_dependencies import SkillMCPDependencies

        self._skill_mcp_dependencies = SkillMCPDependencies()
        persistence = mcp_manager._approval_persistence
        if persistence is not None:
            self._prepare_mcp_configuration = persistence.prepare_reload
            persistence.prepare_reload = self._prepare_configuration_reload

    async def _prepare_configuration_reload(self, configuration, document):
        """Stage complete plugin inputs before publishing any active consumer."""
        if self._closed:
            raise RuntimeError("Corki runtime is closed")
        plan = None
        if self._plugin_discovery_enabled and self._plugin_roots_base is not None:
            directories, disabled = plugin_selection(configuration)
            roots = (
                *(
                    replace(root, configuration=configuration)
                    if isinstance(root, PluginStoreSource)
                    else root
                    for root in self._plugin_roots_base
                ),
                *(
                    path if path.is_absolute() else self._settings.working_directory / path
                    for path in (
                        tuple(map(Path, directories))
                        if self._plugin_dirs_override is None
                        else self._plugin_dirs_override
                    )
                ),
            )
            if self._disabled_plugins_override is not None:
                disabled = self._disabled_plugins_override
            manifests = await asyncio.to_thread(discover_manifests, roots, disabled)
            # A slow scan may finish after Runtime close or a host catalog replacement.
            if self._closed:
                raise RuntimeError("Corki runtime is closed")
            plan = self._plugin_manager.prepare_reload(*manifests)
        try:
            service = (
                self._skill_service.with_configuration(configuration)
                if self._skill_service is not None and self._skill_tool_owner is not None
                else None
            )
            if service is not None and plan is not None:
                service = service.with_plugin_roots(plan.view.skill_roots)
            hooks_enabled = document.get("features", {}).get("hooks", self._hooks_reload_default)
            settings = replace(
                self._settings, configuration=configuration, hooks_enabled=hooks_enabled
            )
            thread_settings = replace(
                self._thread_settings.settings,
                configuration=configuration,
                hooks_enabled=hooks_enabled,
            )
            catalog = (
                self._plugin_catalog_base.extend(*plan.view.mcp_registrations).with_default_cwd(
                    self._settings.working_directory
                )
                if self._plugin_catalog_base is not None and plan is not None
                else None
            )
            publish_mcp = (
                self._prepare_mcp_configuration(configuration, document, catalog=catalog)
                if catalog is not None
                else self._prepare_mcp_configuration(configuration, document)
            )
            hook_snapshot = self._graph._stop_hooks.prepare(
                configuration,
                plan.view.plugins if plan is not None else self._plugin_manager.plugins,
                managed_policy=self._settings.managed_hook_policy,
                enabled=settings.hooks_enabled,
            )
            if plan is not None:
                plan.validate()
        except BaseException:
            if plan is not None:
                try:
                    await plan.abort()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    _LOG.warning(
                        "Failed to close unpublished plugin modules: %s", type(error).__name__
                    )
            raise

        def publish():
            if plan is not None:
                plan.publish()
            publish_mcp()
            self._graph._stop_hooks.publish(hook_snapshot)
            self._settings = settings
            self._thread_settings.settings = thread_settings
            self._pending_skill_service = service
            self._pending_plugins = plan.view if plan is not None else None
            self._mcp_prewarm.request()

        return publish

    def _admit_skill_configuration(self) -> None:
        """A running/resumed Turn keeps both its catalog and skill tool handlers."""
        service = self._pending_skill_service
        plugins = self._pending_plugins
        if service is None and plugins is None:
            return
        if service is not None:
            service = service.with_context_window(
                self._graph._turn_settings.main_context_limits.raw_tokens
            )
        builder = self._graph._context_builder.map_contributors(
            lambda contributor: (
                contributor.with_service(service)
                if isinstance(contributor, SkillContextContributor) and service is not None
                else contributor.with_plugins(plugins.plugins, plugins.warnings)
                if isinstance(contributor, PluginContextContributor) and plugins is not None
                else contributor
            )
        )
        graph = self._graph.with_context_builder(builder)
        assert self._checkpointer is not None
        compiled = graph.compile(checkpointer=self._checkpointer)
        # Prepare/compile before this owner-scoped atomic registry publication.
        # No old Turn is executing when a new Turn is admitted.
        if service is not None:
            self._registry.replace_owned(
                self._skill_tool_owner, (SkillListTool(service), SkillReadTool(service))
            )
        self._graph, self._compiled = graph, compiled
        self._skill_service = service
        self._pending_skill_service = None
        self._pending_plugins = None

    def set_mcp_elicitation_handler(self, handler: ElicitationHandler | None) -> None:
        """Install trusted host delivery before startup; None declines new requests."""
        self._mcp_manager.elicitations.handler = handler

    def set_execution_approval_handler(self, handler: ElicitationHandler | None) -> None:
        """Bind trusted execution/patch review independently of MCP and model history."""
        self._process_manager.approvals.router.handler = handler

    def respond_execution_approval(
        self,
        request_id: str,
        action: str,
        *,
        remember: bool = False,
        execpolicy_amendment: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        """Resolve local execution review; valid host cancel interrupts the active Turn."""
        if type(remember) is not bool:
            raise ValueError("execution approval remember must be a boolean")
        if execpolicy_amendment is not None:
            if not isinstance(execpolicy_amendment, (list, tuple)) or any(
                not isinstance(token, str) for token in execpolicy_amendment
            ):
                raise ValueError("execpolicy amendment must be a string array")
            if action != "accept" or remember:
                raise ValueError("rule persistence requires its own acceptance decision")
        self._process_manager.approvals.router.respond(
            "local-shell",
            request_id,
            action,
            content={"remember": remember, "execpolicy_amendment": execpolicy_amendment},
        )
        # Validate the owned pending token before affecting the active Turn.
        # This is the native ExecApproval(Abort) host-operation boundary, not an
        # internal approval receiver returning Abort as a command failure.
        if action == "cancel" and self._active_run is not None:
            self._realtime.close_input()
            self._active_run.cancel(reason="interrupted")

    def respond_mcp_elicitation(
        self,
        server_name: str,
        request_id: str,
        action: str,
        *,
        content: Any = None,
        meta: Any = None,
    ) -> None:
        """Resolve only the host token/server pair, never a model tool call or wire ID."""
        self._mcp_manager.elicitations.respond(
            server_name,
            request_id,
            action,
            content=content,
            meta=meta,
        )

    @classmethod
    async def acreate(cls, **kwargs) -> LangGraphRuntime:
        """Construct with awaited rollback of resources allocated by this call."""
        return await create_owned(cls.create, kwargs)

    @classmethod
    @synchronous_rollback
    @rollback_registry
    def create(
        cls,
        *,
        settings: CorkiSettings,
        database_path: Path,
        model: ModelPort | None = None,
        thread_id: ThreadId | None = None,
        session_id: SessionId | None = None,
        session_source: SessionSource = DEFAULT_SESSION_SOURCE,
        repository: SessionRepository | None = None,
        archive_store: ThreadArchiveStore | None = None,
        include_archived: bool = False,
        ephemeral: bool = False,
        registry: ToolRegistry | None = None,
        home_path: Path | None = None,
        compatibility_home: Path | None = None,
        memory_model: ModelPort | None = None,
        memory_repository: MemoryRepository | None = None,
        memory_root: Path | None = None,
        mcp_server_metadata: Mapping[str, MCPServerMetadata] | None = None,
        mcp_requirements: MCPRequirementsSnapshot
        | MCPRequirements
        | Mapping[str, object]
        | None = None,
        mcp_catalog: MCPCatalog | None = None,
        mcp_runtime_context: MCPRuntimeContext | None = None,
        mcp_tool_catalog_cache: MCPToolCatalogCache | None = None,
        context_contributors: tuple[ContextContributor, ...] = (),
        configured_skill_roots: tuple[Path, ...] = (),
        context_source_directory: Path | None = None,
        user_instructions_provider: UserInstructionsProvider | None = None,
        inherited_user_instructions: Instructions | None = None,
        inherited_exec_policy: ExecPolicySnapshot | ExecPolicyHandle | None = None,
        load_plugins: bool = True,
        model_metadata_resolver: ModelMetadataResolver = resolve_model_metadata,
        _construction: list | None = None,
        session_start_source: Literal["startup", "clear"] | None = None,
        fork_from_thread_id: ThreadId | None = None,
        fork_before_user_message: int | None = None,
        fork_source_repository: SessionRepository | None = None,
    ) -> LangGraphRuntime:
        """Build a runtime; explicit startup/clear requires a new, empty thread.

        Omit session_start_source to automatically create or resume. Clear starts
        a separate thread; it never deletes history or resets long-term memory.
        A fork copies a source snapshot into a new identity, without executing
        source work. An optional zero-based boundary cuts before a user message.
        An explicit source repository is borrowed, never closed by this runtime;
        its history can seed an independent persistent or private ephemeral target.
        """
        from corki.storage.forks import validate_fork

        validate_fork(
            fork_from_thread_id,
            fork_before_user_message,
            session_start_source,
            fork_source_repository,
        )
        if session_start_source not in (None, "startup", "clear"):
            raise ValueError("session_start_source must be startup, clear, or None")
        construction = _construction if _construction is not None else []
        if type(load_plugins) is not bool:
            raise ValueError("load_plugins must be a host boolean")
        if not isinstance(session_source, SessionSource):
            raise ValueError("session source must be host-validated")
        if inherited_exec_policy is not None and (
            not session_source.is_non_root_agent
            or not isinstance(inherited_exec_policy, (ExecPolicySnapshot, ExecPolicyHandle))
            or settings.execution_permissions is None
        ):
            raise ValueError("only a configured non-root host may inherit an exec policy")
        if inherited_user_instructions is not None and (
            not session_source.is_non_root_agent
            or not isinstance(inherited_user_instructions, Instructions)
        ):
            raise ValueError("only a non-root host may supply inherited instruction snapshots")
        if not isinstance(include_archived, bool):
            raise ValueError("include_archived must be a host-validated boolean")
        if not isinstance(ephemeral, bool):
            raise ValueError("ephemeral must be a host-validated boolean")
        if ephemeral and any(
            value is not None for value in (repository, archive_store, memory_repository)
        ):
            raise ValueError(
                "ephemeral sessions own private state; persistence overrides are unsupported"
            )
        if (
            settings.memories_enabled
            and repository is not None
            and not isinstance(repository, SQLiteSessionRepository)
            and memory_repository is None
        ):
            raise ValueError(
                "memories_enabled with a custom session repository requires an explicit "
                "memory_repository"
            )
        # Explicit policies are embedding-host authority, never editable settings.
        # The default path and CLI must load system policy before allocating owners.
        if mcp_requirements is None:
            requirements_snapshot = load_mcp_requirements()
        elif isinstance(mcp_requirements, MCPRequirementsSnapshot):
            requirements_snapshot = mcp_requirements
        else:
            requirements_snapshot = MCPRequirementsSnapshot(
                MCPRequirements.coerce(mcp_requirements)
            )
        mcp_requirements = requirements_snapshot.policy
        if requirements_snapshot.execution:
            if settings.execution_permissions is None:
                raise ValueError(
                    "managed execution requirements need a configured sandbox compiler/profile"
                )
            settings = replace(
                settings,
                execution_permissions=replace(
                    settings.execution_permissions,
                    requirements=tuple(
                        dict.fromkeys(
                            (
                                *settings.execution_permissions.requirements,
                                *requirements_snapshot.execution,
                            )
                        )
                    ),
                ),
            )
        if settings.execution_permissions is not None:
            validate_effective_approval_requirements(settings.execution_permissions.requirements)
        if mcp_catalog is not None and not isinstance(mcp_catalog, MCPCatalog):
            raise ValueError("mcp_catalog must be a host-owned catalog")
        mcp_runtime_context = MCPRuntimeContext.coerce(mcp_runtime_context)
        plugin_directories, disabled_plugins = settings.plugin_dirs, settings.disabled_plugins
        if load_plugins and settings.plugins_enabled and settings.configuration is not None:
            configured_directories, configured_disabled = plugin_selection(settings.configuration)
            if isinstance(plugin_directories, LayerPluginDirectories):
                plugin_directories = tuple(map(Path, configured_directories))
            if isinstance(disabled_plugins, LayerDisabledPlugins):
                disabled_plugins = configured_disabled
        start_source = "resume" if thread_id is not None else "startup"
        thread_id = thread_id or new_thread_id()
        process_manager = ProcessManager(environment_policy=settings.shell_environment_policy)
        construction.append(process_manager.terminate_all)
        actual_registry = registry or _default_registry(settings, process_manager)
        # Create the canonical session schema before the memory adapter extends
        # the shared database with memory-specific tables.
        actual_repository = repository or (
            None if ephemeral else SQLiteSessionRepository(database_path)
        )
        if repository is None and actual_repository is not None:
            construction.append(actual_repository.close)
        capability_home = home_path or _capability_home(database_path)
        process_manager.approvals.rules.path = (
            capability_home.absolute() / "rules" / "default.rules"
        )
        project_root = discover_project_root(context_source_directory or settings.working_directory)
        # Only host/user-selected roots may introduce executable plugin code.
        # Project instruction trust never authorizes implicit Python imports.
        plugin_roots_base = (
            capability_home / "plugins",
            PluginStoreSource(
                capability_home.absolute(), settings.configuration or LocalConfigState()
            ),
        )
        plugin_roots = (
            *plugin_roots_base,
            *tuple(
                path if path.is_absolute() else settings.working_directory / path
                for path in plugin_directories
            ),
        )
        plugin_manager = PluginManager.discover_and_load(
            roots=plugin_roots if load_plugins and settings.plugins_enabled else (),
            disabled=disabled_plugins,
            registry=actual_registry,
            on_created=lambda manager: construction.append(manager.aclose),
        )
        memories_root = memory_root or capability_home / "memories"
        actual_memory_repository = memory_repository
        if settings.memories_enabled and not ephemeral and actual_memory_repository is None:
            actual_memory_repository = SQLiteMemoryRepository(database_path)
            construction.append(actual_memory_repository.close)
        contributors = [
            MemoryContextContributor(
                memories_root,
                enabled=settings.memories_enabled and settings.memories_use,
                token_limit=settings.memories_summary_token_limit,
                history_database=actual_repository.path
                if not ephemeral and isinstance(actual_repository, SQLiteSessionRepository)
                else None,
            ),
            RealtimeContextContributor(),
        ]
        if (
            settings.memories_enabled
            and settings.memories_use
            and settings.memories_dedicated_tools
        ):
            backend = LocalMemoryBackend(memories_root, actual_memory_repository)
            for memory_tool in memory_tools(backend):
                actual_registry.register(memory_tool)
        skill_service = None
        skill_tool_owner = None
        if settings.skills_enabled:
            skill_service = SkillService(
                home=capability_home,
                project_root=project_root,
                compatibility_home=compatibility_home,
                plugin_roots=plugin_manager.skill_roots,
                context_window_tokens=settings.main_context_limits.raw_tokens,
                max_context_tokens=settings.skills_max_context_tokens,
                rules=settings.skills_config,
                configured_project_roots=configured_skill_roots,
                discovery_cwd=context_source_directory,
            )
            skill_tool_owner = actual_registry.create_owner()
            actual_registry.replace_owned(
                skill_tool_owner, (SkillListTool(skill_service), SkillReadTool(skill_service))
            )
            contributors.append(
                SkillContextContributor(
                    skill_service,
                    include_instructions=settings.skills_include_instructions,
                    allow_input_mentions=not session_source.is_basic_guardian,
                )
            )
        plugin_catalog_base = None
        if mcp_catalog is None:
            plugin_catalog_base = MCPCatalog(
                tuple(MCPRegistration(server) for server in settings.mcp_servers)
            )
            registrations = (
                *plugin_manager.mcp_registrations,
                *(MCPRegistration(server) for server in settings.mcp_servers),
            )
            mcp_catalog = MCPCatalog(registrations)
        mcp_catalog = mcp_catalog.with_default_cwd(settings.working_directory)
        mcp_manager = MCPManager(
            (),
            actual_registry,
            defer_tools=search_enabled(
                settings,
                namespace_tools=getattr(
                    model,
                    "capabilities",
                    resolve_capabilities(
                        base_url=settings.api_base,
                        api_mode=settings.api_mode,
                        provider_name=settings.provider_name,
                    ),
                ).namespace_tools,
            ),
            code_mode_only=(
                settings.model_context_info(settings.model).tool_mode or settings.tool_mode
            )
            == "code_mode_only",
            prefix_tool_names=(
                not settings.non_prefixed_mcp_tool_names
                or settings.non_prefixed_mcp_tool_servers is not None
            ),
            non_prefixed_servers=(
                settings.non_prefixed_mcp_tool_servers or ()
                if settings.non_prefixed_mcp_tool_names
                else ()
            ),
            server_metadata=mcp_server_metadata,
            approval_policy=settings.mcp_approval_policy,
            plugins_enabled=settings.plugins_enabled,
            approval_persistence=MCPApprovalPersistence(
                settings.configuration or LocalConfigState(), capability_home
            ),
            tool_call_elicitation=settings.mcp_tool_call_elicitation,
            requirements=mcp_requirements,
            catalog=mcp_catalog,
            runtime_context=mcp_runtime_context,
            oauth_file_home=(
                capability_home if settings.mcp_oauth_credentials_store == "file" else None
            ),
            oauth_home=(
                capability_home if settings.mcp_oauth_credentials_store != "file" else None
            ),
            oauth_store_mode=settings.mcp_oauth_credentials_store,
            tool_catalog_cache=mcp_tool_catalog_cache,
            lazy_when_cached=session_source.kind == SessionSourceKind.SUBAGENT,
        )
        construction.append(mcp_manager.aclose)
        contributors.append(
            PluginContextContributor(
                plugin_manager.plugins,
                plugin_manager.warnings,
                allow_input_mentions=not session_source.is_basic_guardian,
            )
        )
        context_builder = ContextBuilder(
            contributors=(*contributors, *context_contributors),
            source_directory=context_source_directory,
            include_environment_context=settings.include_environment_context,
            shell_name=process_manager.shell.name,
            instruction_manager=InstructionManager(
                None
                if session_source.is_non_root_agent
                else (
                    user_instructions_provider
                    if user_instructions_provider is not None
                    else HomeUserInstructionsProvider(capability_home)
                ),
                inherited=None if session_source.is_basic_guardian else inherited_user_instructions,
            ),
        ).with_instruction_settings(settings)
        capabilities = resolve_capabilities(
            base_url=settings.api_base,
            api_mode=settings.api_mode,
            provider_name=settings.provider_name,
        )
        capabilities = replace(
            capabilities,
            supports_native_tool_search=settings.tool_search_mode == "native",
            supports_native_namespaces=settings.tool_namespace_mode == "native",
            supports_audio_input=settings.supports_audio_input,
            supports_service_tier=(
                capabilities.supports_service_tier
                if settings.supports_service_tier is None
                else settings.supports_service_tier
            ),
        )
        actual_model = model or _create_model(settings, capabilities)
        if model is None:
            construction.append(actual_model.aclose)
        memory_service: LongTermMemoryService | None = None
        if settings.memories_enabled and not ephemeral:
            assert actual_memory_repository is not None
            actual_memory_model = memory_model
            close_memory_model = False
            if actual_memory_model is None:
                if model is not None:
                    actual_memory_model = actual_model
                else:
                    actual_memory_model = _create_model(settings, capabilities)
                    construction.append(actual_memory_model.aclose)
                    close_memory_model = True
            memory_service = LongTermMemoryService(
                thread_id=thread_id,
                settings=settings,
                repository=actual_memory_repository,
                model=actual_memory_model,
                root=memories_root,
                close_model=close_memory_model,
                home_path=capability_home,
                compatibility_home=compatibility_home,
                configured_skill_roots=(
                    skill_service.configured_project_roots(settings.working_directory)
                    if skill_service is not None
                    else ()
                ),
                managed_requirements=requirements_snapshot,
            )
        # Delay the live RAM connection until fallible extension composition is
        # finished. If Runtime construction fails, no caller can close this owner.
        if ephemeral:
            actual_repository = VolatileSessionRepository()
        assert actual_repository is not None
        try:
            runtime = cls(
                model_metadata_resolver=model_metadata_resolver,
                exec_policy_config_folders=(
                    settings.configuration.rule_folders(capability_home.absolute())
                    if settings.configuration is not None
                    else (capability_home.absolute(),)
                ),
                inherited_exec_policy=inherited_exec_policy,
                settings=settings,
                model=actual_model,
                repository=actual_repository,
                registry=actual_registry,
                process_manager=process_manager,
                mcp_manager=mcp_manager,
                mcp_requirements_snapshot=requirements_snapshot,
                plugin_manager=plugin_manager,
                skill_service=skill_service,
                skill_tool_owner=skill_tool_owner,
                plugin_roots_base=plugin_roots_base,
                plugin_catalog_base=plugin_catalog_base,
                plugin_discovery_enabled=load_plugins and settings.plugins_enabled,
                memory_service=memory_service,
                memory_repository=actual_memory_repository,
                context_builder=context_builder,
                checkpoint_path=database_path.with_name("checkpoints.db"),
                writer_database_path=(
                    actual_repository.path
                    if not ephemeral and isinstance(actual_repository, SQLiteSessionRepository)
                    else database_path
                ),
                thread_id=thread_id,
                session_id=session_id,
                session_source=session_source,
                session_start_source=session_start_source,
                fork_from_thread_id=fork_from_thread_id,
                fork_before_user_message=fork_before_user_message,
                fork_source_repository=fork_source_repository,
                archive_store=archive_store,
                include_archived=include_archived,
                ephemeral=ephemeral,
                history_notes_path=database_path.with_name(
                    database_path.stem + ".history-notes.db"
                ),
                memory_resetter=None
                if ephemeral
                else MemoryResetter(
                    roots=(memories_root, capability_home / "memories_extensions"),
                    protected_paths=(settings.working_directory, capability_home, database_path),
                    repository=actual_memory_repository,
                    database_path=(
                        database_path
                        if isinstance(actual_repository, SQLiteSessionRepository)
                        else None
                    ),
                ),
            )
            runtime._graph._stop_hooks.start_source = start_source
            return runtime
        except BaseException:
            if isinstance(actual_repository, VolatileSessionRepository):
                actual_repository._close()
            raise

    @property
    def thread_id(self) -> ThreadId:
        return self._thread_id

    @property
    def session_id(self) -> SessionId:
        """Authoritative host identity, available after Runtime initialization."""
        if self._execution_identity is None:
            raise RuntimeError("session identity is not resolved before initialization")
        return self._execution_identity.session_id

    @property
    def execution_policy_handle(self) -> ExecPolicyHandle:
        """Capture live policy for explicit host-created non-root runtimes on this loop.

        Unlike the immutable settings snapshot, this keeps subsequent rule updates
        shared. It does not share consent, process ownership, or thread history.
        """
        if self._closed or self._compiled is None:
            raise RuntimeError("execution policy is available only on a ready, open runtime")
        permissions = self._settings.execution_permissions
        if permissions is None or permissions.exec_policy_snapshot is None:
            raise ValueError("live exec policy requires configured execution permissions")
        return self._process_manager.approvals.rules.capture(permissions.exec_policy_snapshot)

    async def stream(
        self,
        message: str,
        *,
        realtime: bool = False,
        mentions: tuple[InputMention, ...] = (),
        attachments=(),
        image_positions=(),
    ) -> AsyncIterator[RuntimeEvent]:
        if not isinstance(message, str):
            raise TypeError("message must be a string; use compact() for manual compaction")
        mentions = validate_mentions(mentions)
        attachments = validate_image_attachments(attachments)
        image_positions = validate_image_positions(message, image_positions, len(attachments))
        async with aclosing(
            self._start_turn(
                message,
                realtime=realtime,
                mentions=mentions,
                attachments=attachments,
                image_positions=image_positions,
            )
        ) as events:
            async for event in events:
                yield event

    @property
    def thread_settings(self) -> ModelSettingsSnapshot:
        """Current defaults for future Turns, not the active Turn's model selection."""
        return self._thread_settings.snapshot

    def respond_user_input(self, turn_id: str, call_id: str, response: object) -> bool:
        """Answer only the exact active request; late/duplicate replies are harmless."""
        run = self._active_run
        if (
            self._closed
            or run is None
            or run.turn_id != turn_id
            or run.done.is_set()
            or run.cancel_requested
            or run.finishing
        ):
            return False
        return run.user_input.respond(call_id, response)

    def cancel_user_input(self, turn_id: str, call_id: str) -> bool:
        """An old question panel must never interrupt a successor Turn."""
        run = self._active_run
        if (
            self._closed
            or run is None
            or run.turn_id != turn_id
            or run.done.is_set()
            or run.finishing
            or run.cancel_requested
            or not run.user_input.is_pending(call_id)
        ):
            return False
        self._realtime.close_input()
        run.cancel()
        return True

    async def update_thread_settings(
        self,
        *,
        model: str | None = None,
        reasoning_effort: str | None | UnsetSetting = UNSET,
        reasoning_summary: str | None = None,
        service_tier: str | None | UnsetSetting = UNSET,
        collaboration_mode: CollaborationMode | None = None,
        personality: str | None | UnsetSetting = UNSET,
    ) -> ModelSettingsSnapshot:
        """Commit sparse future defaults without waiting for or changing an active Turn.

        Explicit effort None clears selection; tier None requests the default tier.
        The returned snapshot acknowledges committed settings, not a model request.
        """
        async with self._lifecycle_lock:
            await self._ensure_ready()
            if self._closed:
                raise RuntimeError("Corki runtime is closed")
            return await self._thread_settings.update(
                model=model,
                reasoning_effort=reasoning_effort,
                reasoning_summary=reasoning_summary,
                service_tier=service_tier,
                collaboration_mode=collaboration_mode,
                personality=personality,
            )

    def _admit_model_settings(self, snapshot: ModelSettingsSnapshot) -> None:
        if capture_model_settings(self._graph._settings) == snapshot:
            return
        settings = bind_model_settings(self._thread_settings.settings, snapshot)
        graph = self._graph.with_model_settings(settings)
        assert self._checkpointer is not None
        compiled = graph.compile(checkpointer=self._checkpointer)
        self._graph, self._compiled = graph, compiled
        self._service_tier_warning_pending = settings.service_tier_warning

    @property
    def active_turn_settings(self) -> ModelSettingsSnapshot | None:
        """Current published settings; an already captured Step may still use older ones."""
        run = self._active_run
        if run is None or run.models is None or run.done.is_set() or run.cancel_requested:
            return None
        return run.models.current

    async def update_turn_settings(
        self,
        turn_id: str,
        *,
        model: str | None = None,
        reasoning_effort: str | None | UnsetSetting = UNSET,
        reasoning_summary: str | None = None,
        service_tier: str | None | UnsetSetting = UNSET,
    ) -> TurnSettingsUpdateResult:
        """Publish a sparse patch for later Steps of the exact named active task."""
        return await self._turn_updates.update(
            turn_id,
            model=model,
            reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary,
            service_tier=service_tier,
        )

    async def compact(self) -> AsyncIterator[RuntimeEvent]:
        """Replace active work with a standalone local compaction turn."""
        await self.cancel_active(reason="replaced")
        async with aclosing(self._start_turn(None)) as events:
            async for event in events:
                yield event

    async def _start_turn(
        self,
        message: str | None,
        *,
        realtime: bool = False,
        mentions=(),
        attachments=(),
        image_positions=(),
    ) -> AsyncIterator[RuntimeEvent]:
        operation = "compact" if message is None else "normal"
        pending_warning = None
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
                try:
                    await self._flush_pending_terminals()
                except Exception as exc:
                    if not self._retryable_terminal_storage_error(exc):
                        raise
                    # The old task has finished; its exact result remains owned.
                    # Do not gate new work on an unavailable terminal write, or
                    # confuse this with permission to skip the new task's commits.
                    pending_warning = (
                        "A previous task result remains pending; Corki will continue "
                        f"retrying its storage write. Error: {exc}"
                    )
                if self._input_flush_pending:
                    await self._flush_realtime_inputs()
                self._admit_skill_configuration()
                self._admit_model_settings(self.thread_settings)
                turn_id = new_turn_id()
                user_item = (
                    UserMessageItem(
                        message,
                        turn_id,
                        mentions=mentions,
                        attachments=attachments,
                        image_positions=image_positions,
                    )
                    if message is not None
                    else None
                )
                initial = _initial_state(
                    self._thread_id,
                    turn_id,
                    self._graph._settings,
                    user_item,
                    realtime=realtime,
                )
                initial["turn_base_instructions"] = self._graph._context_builder.base_instructions()
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
            if pending_warning is not None:
                yield WarningEvent(self._thread_id, turn_id, pending_warning)
            async for event in events:
                yield event

    async def steer(
        self,
        message: str,
        *,
        mentions: tuple[InputMention, ...] = (),
        attachments=(),
        image_positions=(),
    ) -> None:
        """Queue an additional user message for the currently streaming turn."""

        await self._realtime.steer(
            message, mentions=mentions, attachments=attachments, image_positions=image_positions
        )

    def take_unsubmitted_inputs(self) -> tuple[UserMessageItem, ...]:
        """Transfer uncommitted input even when the host missed the terminal event.

        Call after the turn has joined. This is a process-local handoff, not conversation history.
        Hosts persisting drafts must retain these items themselves; reading consumes this handoff.
        """
        items, self._unsubmitted_inputs = self._unsubmitted_inputs, ()
        return items

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
        else:
            self._mcp_manager.cancel_startup()

    async def load_display_history(self) -> tuple[ConversationItem, ...]:
        """Read canonical local history without resuming a Turn or executing a tool."""
        return await self._load_display(self._repository.load_items)

    async def load_display_snapshot(self) -> DisplayHistory:
        """Include terminal metadata without projecting it into model conversation items."""
        return await self._load_display(self._repository.load_display_snapshot)

    async def load_display_items_page(
        self, *, cursor: DisplayItemsCursor | None = None, limit: int = 100
    ) -> DisplayItemsPage:
        """Read a bounded raw history page without sampling or resuming a Turn."""
        return await self._load_display(
            lambda thread_id: self._repository.load_display_items_page(
                thread_id, cursor=cursor, limit=limit
            )
        )

    async def load_display_turns_page(
        self, *, cursor: DisplayTurnsCursor | None = None, limit: int = 100
    ) -> DisplayTurnsPage:
        """Read bounded Turn facts, including empty failed or cancelled Turns."""
        return await self._load_display(
            lambda thread_id: self._repository.load_display_turns_page(
                thread_id, cursor=cursor, limit=limit
            )
        )

    async def contains_display_item(self, *, kind, identity, through_sequence):
        """Check a historical display identity without hydrating its payload."""
        return await self._load_display(
            lambda thread_id: self._repository.contains_display_item(
                thread_id, kind=kind, identity=identity, through_sequence=through_sequence
            )
        )

    async def _load_display(self, read):
        # Readers need resource lifetime protection, not Turn admission. A
        # queued Turn may hold _turn_lock while waiting for an active worker.
        async with self._lifecycle_lock:
            await self._ensure_ready()
            reading = asyncio.create_task(read(self._thread_id))
            cancelled = False
            while not reading.done():
                try:
                    await asyncio.shield(reading)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                if not reading.cancelled():
                    reading.exception()
                raise asyncio.CancelledError
            return reading.result()

    async def resume_pending(self) -> AsyncIterator[RuntimeEvent]:
        """Continue the latest checkpointed running turn, if one exists."""
        async with self._turn_lock:
            if self._active_run is not None:
                await self._active_run.done.wait()
            async with self._lifecycle_lock:
                await self._ensure_ready()
                await self._flush_pending_terminals()
                turn = await self._repository.latest_running_turn(self._thread_id)
                if turn is None:
                    return
                if self._input_flush_pending:
                    await self._flush_realtime_inputs()
                config = self._graph_config(turn.id)
                assert self._checkpointer is not None
                checkpoint = await self._checkpointer.aget_tuple(config)
                checkpoint_channels = (
                    checkpoint.checkpoint.get("channel_values", {})
                    if checkpoint is not None
                    else {}
                )
                checkpoint_settings = checkpoint_channels.get("turn_model_settings")
                bases = [] if turn.base_instructions is None else [turn.base_instructions]
                for key in ("turn_base_instructions", "context_instructions"):
                    if key not in checkpoint_channels:
                        continue
                    if key == "context_instructions" and not checkpoint_channels.get(
                        "request_items"
                    ):
                        # The initial state has an empty placeholder, not a
                        # prepared prefix. Empty prepared prefixes remain valid.
                        continue
                    value = checkpoint_channels[key]
                    if not isinstance(value, str):
                        raise ValueError("invalid base instructions in checkpoint")
                    bases.append(value)
                if len(set(bases)) > 1:
                    raise ValueError("Turn base instructions conflict with checkpoint")
                # Legacy RUNNING turns may have already committed their model or
                # summary result. Defer the missing-base guard until a fresh
                # sample is actually required, so replay/cancellation still work.
                legacy_base_unknown = not bases and self._settings.base_instructions is not None
                step_settings = checkpoint_channels.get("step_model_settings")
                if "step_model_settings" in checkpoint_channels:
                    if not isinstance(step_settings, ModelSettingsSnapshot):
                        raise ValueError("invalid checkpoint Step model settings")
                    bind_model_settings(self._thread_settings.settings, step_settings)
                if "turn_model_settings" in checkpoint_channels and not isinstance(
                    checkpoint_settings, ModelSettingsSnapshot
                ):
                    raise ValueError("invalid checkpoint model settings")
                if (
                    turn.model_settings is not None
                    and checkpoint_settings is not None
                    and turn.model_settings != checkpoint_settings
                ):
                    raise ValueError("Turn model settings conflict with checkpoint")
                self._admit_model_settings(
                    turn.model_settings or checkpoint_settings or self.thread_settings
                )
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
                    initial = _initial_state(
                        self._thread_id, turn.id, self._graph._settings, user_item
                    )
                    if turn.base_instructions is not None:
                        initial["turn_base_instructions"] = turn.base_instructions
                events = self._run_graph(
                    initial,
                    turn.id,
                    turn.user_input,
                    resumed=True,
                    realtime=False,
                    operation=turn.operation,
                    resumed_step_settings=step_settings,
                    legacy_base_unknown=legacy_base_unknown,
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
        resumed_step_settings: ModelSettingsSnapshot | None = None,
        legacy_base_unknown: bool = False,
    ) -> AsyncIterator[RuntimeEvent]:
        run = TurnRun(turn_id, self._settings.event_queue_size)
        admitted = capture_model_settings(self._graph._settings)
        run.models = StepSettingsState(admitted, resumed_step_settings or admitted)
        compiled = self._compiled
        memory_permissions = MemoryPermissionSnapshot(
            self._graph._settings.execution_permissions, self._mcp_requirements_snapshot
        )
        admitted_info = self._graph._settings.model_context_info(self._graph._settings.model)
        warning_message = self._service_tier_warning_pending
        self._service_tier_warning_pending = None
        execution_warnings = (
            *self._configuration_warnings_pending,
            *self._execution_warnings_pending,
        )
        self._configuration_warnings_pending = ()
        self._execution_warnings_pending = ()
        run.start(
            lambda owned: self._execute_run(
                owned,
                initial,
                message,
                realtime=realtime,
                resumed=resumed,
                operation=operation,
                compiled=compiled,
                memory_permissions=memory_permissions,
                legacy_base_unknown=legacy_base_unknown,
            )
        )
        self._active_run = run
        if self._closed:
            run.cancel()
        terminal_sent = False
        try:
            yield TurnStarted(self._thread_id, turn_id, resumed=resumed)
            if admitted_info.used_fallback_model_metadata:
                yield WarningEvent(
                    self._thread_id,
                    turn_id,
                    f"Model metadata for `{admitted_info.model}` not found. "
                    "Defaulting to fallback metadata; "
                    "this can degrade performance and cause issues.",
                )
            if warning_message is not None:
                yield WarningEvent(self._thread_id, turn_id, warning_message)
            for warning in execution_warnings:
                yield WarningEvent(self._thread_id, turn_id, warning)
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
            while run.final_events:
                yield run.final_events.pop(0)
            terminal_sent = True
            yield terminal
            if isinstance(terminal, TurnCancelled):
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            run.cancel()
            await run.join()
            if not terminal_sent:
                while run.final_events:
                    yield run.final_events.pop(0)
                terminal_sent = True
                yield run.result()
            raise
        finally:
            run.cancel()
            await run.join()
            if self._active_run is run:
                self._active_run = None

    async def _flush_realtime_inputs(self, context: GraphRunContext | None = None) -> None:
        from corki.core.prompt_hooks import inspect, ordered_inputs, receipt_key, rejected

        items = self._realtime.unrecorded_items
        if items:
            # Only normal terminal persistence calls this path. Keep the retry
            # obligation distinct from cancelled inputs awaiting host recovery.
            self._input_flush_pending = True
            accepted = []
            for item in items:
                if context is not None:
                    stopped = await inspect(
                        self._graph._stop_hooks,
                        item,
                        state=_initial_state(
                            self._thread_id, item.turn_id, self._graph._turn_settings, item
                        ),
                        runtime=context,
                        repository=self._repository,
                        settings=self._graph._turn_settings,
                        shell=self._graph._session_shell,
                        mcp_manager=self._mcp_manager,
                    )
                else:
                    # Admission retries have no old Turn event owner. They may
                    # finish committed decisions, never bypass an unfinished hook.
                    saved = await self._repository.load_hook_batch(
                        self._thread_id, item.turn_id, receipt_key(item)
                    )
                    if saved is None:
                        raise RuntimeError("Pending input inspection incomplete; not admitted")
                    stopped = await rejected(self._repository, self._thread_id, item)
                if stopped:
                    self._realtime.acknowledge((item,))
                else:
                    accepted.append(item)
            prepared = await self._media.prepare_items(
                await ordered_inputs(self._repository, self._thread_id, accepted)
            )
            await self._repository.append_items(self._thread_id, prepared)
            self._realtime.acknowledge(items)
        self._input_flush_pending = False

    async def _execute_run(
        self,
        run: TurnRun,
        initial: CorkiState | None,
        message: str,
        *,
        realtime: bool,
        resumed: bool,
        operation: str = "normal",
        compiled: Any = None,
        memory_permissions: MemoryPermissionSnapshot | None = None,
        legacy_base_unknown: bool = False,
    ) -> RuntimeEvent:
        """Own work, cleanup, and the durable terminal independently of the UI."""
        turn_id = run.turn_id
        terminal: RuntimeEvent | None = None
        graph_context = GraphRunContext(
            events=_QueueEventSink(run.queue),
            request_user_input=lambda call_id, questions, blocking: run.user_input.request(
                self._thread_id,
                run.turn_id,
                call_id,
                questions,
                blocking,
                _QueueEventSink(run.queue).emit,
            ),
            is_non_root_agent=self._session_source.is_non_root_agent,
            session_source=self._session_source,
            legacy_base_unknown=legacy_base_unknown,
            realtime=self._realtime,
            terminal_pending_turns=frozenset(self._pending_terminals),
            models=run.models,
            tools=StepToolState(capture_resources=self._mcp_manager.capture_resource_binding),
        )
        writing_start = not resumed
        try:
            # Own the very first durable write, not only graph execution. A
            # cancelled observer must never leave an unowned RUNNING record.
            if writing_start:
                await self._repository.save_turn(
                    TurnRecord(
                        turn_id,
                        self._thread_id,
                        TurnStatus.RUNNING,
                        message,
                        operation=operation,
                        model_settings=initial.get("turn_model_settings") if initial else None,
                        base_instructions=initial.get("turn_base_instructions")
                        if initial
                        else None,
                    )
                )
            writing_start = False
            if run.cancel_requested:
                raise asyncio.CancelledError
            if (
                not resumed
                and operation == "normal"
                and self._memory_service is not None
                and not self._session_source.is_non_root_agent
            ):
                self._memory_service.start(self._thread_id, parent_permissions=memory_permissions)
            if resumed:
                cancellation = await self._repository.load_hook_batch(
                    self._thread_id, turn_id, f"turn_cancellation:{turn_id}"
                )
                if cancellation is not None:
                    intent, executions = cancellation
                    if (
                        executions
                        or set(intent) != {"version", "reason"}
                        or type(intent.get("version")) is not int
                        or intent["version"] != 1
                        or intent.get("reason") not in (None, "interrupted", "replaced")
                    ):
                        raise ValueError("Invalid durable Turn cancellation intent")
                    run.cancel_reason = intent["reason"]
                    raise asyncio.CancelledError
                from corki.core.interrupt_hooks import has_durable_interrupt

                if await has_durable_interrupt(self._repository, self._thread_id, turn_id):
                    # Legacy Interrupt plans were written only in cancelled-Turn
                    # cleanup, even when the optional visible marker was disabled.
                    run.cancel_reason = "interrupted"
                    raise asyncio.CancelledError
            if resumed and any(
                isinstance(item, TurnAbortedItem) and item.turn_id == turn_id
                for item in await self._repository.load_items(self._thread_id)
            ):
                # The marker may commit before terminal persistence fails. Do
                # not replay a checkpoint after that durable cancellation intent.
                run.cancel_reason = "interrupted"
                raise asyncio.CancelledError
            compiled = self._compiled if compiled is None else compiled
            assert compiled is not None
            if resumed:
                from corki.core.async_prompt_hooks import project_checkpoint as project_prompt
                from corki.core.post_hook_recovery import recover_feedback as recover_post_feedback
                from corki.core.pre_hook_recovery import recover_feedback

                await recover_feedback(
                    self._repository, self._thread_id, turn_id, graph_context.events
                )
                checkpoint = await compiled.aget_state(self._graph_config(turn_id))
                pre_feedback = ()
                prompt_feedback = ()
                if any(node in {"call_model", "retry_model"} for node in checkpoint.next):
                    prompt_feedback = await project_prompt(
                        self._repository,
                        self._thread_id,
                        turn_id,
                        graph_context.events,
                        checkpoint_id=checkpoint.config.get("configurable", {}).get("checkpoint_id")
                        if checkpoint.config
                        else None,
                    )
                    pre_feedback = await self._graph._async_pre_hooks.project_checkpoint(
                        self._repository,
                        graph_context.events,
                        thread=self._thread_id,
                        turn=turn_id,
                        checkpoint_id=checkpoint.config.get("configurable", {}).get("checkpoint_id")
                        if checkpoint.config
                        else None,
                    )
                post_feedback = await recover_post_feedback(
                    self._repository,
                    self._thread_id,
                    turn_id,
                    checkpoint_id=checkpoint.config.get("configurable", {}).get("checkpoint_id")
                    if checkpoint.config
                    else None,
                )
                if (prompt_feedback or pre_feedback or post_feedback) and any(
                    node in {"call_model", "retry_model"} for node in checkpoint.next
                ):
                    await compiled.aupdate_state(
                        self._graph_config(turn_id),
                        {
                            "request_items": (
                                *checkpoint.values.get("request_items", ()),
                                *prompt_feedback,
                                *pre_feedback,
                                *post_feedback,
                            )
                        },
                    )
                    checkpoint = await compiled.aget_state(self._graph_config(turn_id))
                if checkpoint.next and any(
                    node in {"call_model", "retry_model", "evaluate", "execute_tools"}
                    for node in checkpoint.next
                ):
                    servers = checkpoint.values.get("tool_snapshot_mcp_servers")
                    if servers is None:
                        # Legacy checkpoints predate per-Step server identity.
                        # Preserve their former complete-directory rebind contract.
                        await self._mcp_manager.refresh_if_dirty()
                    else:
                        if not isinstance(servers, (list, tuple)) or not all(
                            isinstance(server, str) for server in servers
                        ):
                            raise ValueError("invalid saved MCP server identities")
                        for server in servers:
                            await self._mcp_manager.prepare_server(server)
                    self._sync_tool_search()
                    self._sync_code_mode_tools()
                permissions = self._graph._settings.execution_permissions
                for outcome in await self._repository.load_turn_tool_outcomes(
                    self._thread_id, turn_id
                ):
                    await graph_context.turn_diff.observe(
                        outcome,
                        is_patch=outcome.tool_name == "apply_patch",
                        compiler=permissions.compiler if permissions is not None else None,
                        cwd=self._settings.working_directory,
                        thread=self._thread_id,
                        turn=turn_id,
                        events=graph_context.events,
                    )
            if resumed and any(node in {"call_model", "retry_model"} for node in checkpoint.next):
                await compiled.aupdate_state(
                    self._graph_config(turn_id), {"refresh_recovered_context": True}
                )
            result = await compiled.ainvoke(
                initial,
                # Step capture must reach the checkpoint before prepare starts
                # asynchronous model/tool/context effects, not race a background put.
                durability="sync",
                context=graph_context,
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
            from corki.core.compact_start_hooks import StartHookStopped

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
            elif isinstance(exc, StartHookStopped):
                terminal = TurnCompleted(self._thread_id, turn_id, "")
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
            # A Turn owns observations/cells, not already-published terminal
            # sessions. Explicit host cleanup or Runtime close owns those.
            if isinstance(terminal, TurnCancelled) and self._code_mode is not None:
                try:
                    await self._code_mode.deactivate(interrupt=True)
                except Exception as exc:
                    run.cleanup_error = run.cleanup_error or exc
                    _LOG.exception("Code Mode cleanup failed during turn cancellation")
                code_mode_deactivated = True
            try:
                # Closing immediately after TurnStarted still preserves the input.
                if initial is not None and initial.get("pending_input_items"):
                    from corki.core.compact_start_hooks import allows_input as compact_allows_input
                    from corki.core.prompt_hooks import ordered_inputs, rejected
                    from corki.core.start_hooks import allows_input

                    start_allows_input = await allows_input(
                        self._repository, self._thread_id, turn_id
                    )
                    start_allows_input = start_allows_input and await compact_allows_input(
                        self._repository, self._thread_id, turn_id
                    )
                    pending = [
                        item
                        for item in initial["pending_input_items"]
                        if start_allows_input
                        and not await rejected(self._repository, self._thread_id, item)
                    ]
                    await self._repository.append_items(
                        self._thread_id,
                        await self._media.prepare_items(
                            await ordered_inputs(self._repository, self._thread_id, pending)
                        ),
                    )
                if realtime:
                    if isinstance(terminal, TurnCancelled):
                        pending = self._realtime.unrecorded_items
                        if pending:
                            # An append can commit before its caller observes cancellation.
                            # Preserve those identities, returning only genuinely uncommitted input.
                            committed = {
                                item.id
                                for item in await self._repository.load_items(self._thread_id)
                            }
                            terminal = replace(
                                terminal,
                                unsubmitted_inputs=tuple(
                                    i for i in pending if i.id not in committed
                                ),
                            )
                            self._unsubmitted_inputs += terminal.unsubmitted_inputs
                            self._realtime.acknowledge(pending)
                    else:
                        try:
                            await self._flush_realtime_inputs(graph_context)
                        except asyncio.CancelledError:
                            # Input hooks still execute in this closing phase.
                            # Their cancellation must reach a durable terminal,
                            # not escape the worker without returning ownership.
                            pending = self._realtime.unrecorded_items
                            committed = {
                                item.id
                                for item in await self._repository.load_items(self._thread_id)
                            }
                            unsubmitted = tuple(
                                item for item in pending if item.id not in committed
                            )
                            terminal = TurnCancelled(self._thread_id, turn_id, unsubmitted)
                            self._unsubmitted_inputs += unsubmitted
                            self._realtime.acknowledge(pending)
                            self._input_flush_pending = False
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

            if isinstance(terminal, TurnCancelled):
                try:
                    # Control state is not the optional model-visible interrupt
                    # message. Preserve cancellation even if save_turn fails.
                    await self._repository.save_hook_batch(
                        self._thread_id,
                        turn_id,
                        f"turn_cancellation:{turn_id}",
                        {"version": 1, "reason": run.cancel_reason},
                    )
                except Exception as exc:
                    run.cleanup_error = run.cleanup_error or exc
                    _LOG.exception("Could not persist Turn cancellation intent")

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
                _LOG.exception("Could not persist turn terminal")
                confirmed = False
                deferred = False
                confirmation_unavailable = False
                confirm = getattr(self._repository, "confirm_turn_terminal", None)
                if confirm is not None:
                    try:
                        confirmed = await confirm(record) is True
                    except Exception:
                        confirmation_unavailable = True
                        _LOG.exception("Could not confirm turn terminal after write failure")
                if confirmed:
                    run.final_events.append(
                        WarningEvent(
                            self._thread_id,
                            turn_id,
                            "Terminal write reported an error, "
                            f"but the stored result was verified: {exc}",
                        )
                    )
                else:
                    run.cleanup_error = run.cleanup_error or exc
                    # Keep an admitted result through ambiguous reads. Retention is
                    # not permission to overwrite: the retry transaction rechecks
                    # both admission identity and any existing terminal payload.
                    if callable(getattr(self._repository, "retry_turn_terminal", None)):
                        try:
                            status = await self._repository.load_turn_status(
                                self._thread_id, turn_id
                            )
                            if status is TurnStatus.RUNNING or (
                                not writing_start
                                and confirmation_unavailable
                                and status
                                in {TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED}
                            ):
                                self._pending_terminals[turn_id] = record
                        except Exception:
                            _LOG.exception("Could not establish pending terminal ownership")
                            if not writing_start:
                                # Successful admission (or validated resume) already
                                # established ownership before this read outage.
                                self._pending_terminals[turn_id] = record
                if (
                    not confirmed
                    and not writing_start
                    and turn_id in self._pending_terminals
                    and self._retryable_terminal_storage_error(exc)
                ):
                    try:
                        await self._flush_pending_terminals()
                    except Exception as retry_error:
                        deferred = self._retryable_terminal_storage_error(retry_error)
                        _LOG.exception("Could not flush terminal before reporting task outcome")
                    else:
                        confirmed = True
                    if confirmed or deferred:
                        if run.cleanup_error is exc:
                            run.cleanup_error = None
                        detail = (
                            "The stored result was verified after retry."
                            if confirmed
                            else "The result remains pending; Corki will continue retrying."
                        )
                        run.final_events.append(
                            WarningEvent(
                                self._thread_id,
                                turn_id,
                                f"Terminal write reported an error. {detail} Error: {exc}",
                            )
                        )
                if not confirmed and not deferred and not isinstance(terminal, TurnCancelled):
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
                try:
                    await graph_context.tools.aclose(
                        wait_for_calls=not isinstance(terminal, TurnCompleted)
                    )
                except Exception as exc:
                    run.cleanup_error = run.cleanup_error or exc
                    _LOG.exception("Step resource cleanup failed after turn terminal selection")
                if isinstance(terminal, TurnCancelled) and run.cancel_reason == "interrupted":
                    from corki.core.interrupt_hooks import FinalEvents
                    from corki.core.interrupt_hooks import run as run_interrupt

                    try:
                        await run_interrupt(
                            self._graph._stop_hooks,
                            repository=self._repository,
                            thread=self._thread_id,
                            turn=turn_id,
                            session_source=self._session_source,
                            settings=self._graph._turn_settings,
                            shell=self._graph._session_shell,
                            mcp_manager=self._mcp_manager,
                            events=FinalEvents(run.final_events),
                        )
                    except BaseException as exc:
                        run.cleanup_error = run.cleanup_error or exc
                        _LOG.exception("Interrupt hook cleanup failed")
                if realtime:
                    self._realtime.deactivate()
                clear = graph_context.turn_diff.final_clear(
                    self._thread_id, turn_id, aborted=not isinstance(terminal, TurnCompleted)
                )
                if clear is not None:
                    run.final_events.append(clear)
                pending_copy = getattr(self._repository, "transcript_publication_pending", None)
                if callable(pending_copy):
                    try:
                        if await pending_copy(self._thread_id):
                            run.final_events.append(
                                WarningEvent(
                                    self._thread_id,
                                    turn_id,
                                    "The local transcript copy is not up to date. "
                                    "Committed session history is retained; publication will "
                                    "be retried on a later session write.",
                                )
                            )
                    except Exception:
                        _LOG.warning(
                            "Could not inspect transcript publication state", exc_info=True
                        )

    def _graph_config(self, turn_id: TurnId) -> dict[str, object]:
        return {
            "configurable": {"thread_id": f"{self._thread_id}:{turn_id}"},
            "recursion_limit": (
                2**63 - 1
                if self._settings.model_unbounded_connection_retries
                or self._settings.max_steps is None
                else self._settings.max_steps * (6 + 2 * self._settings.model_max_retries) + 10
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
        try:
            await self._initialize_owned()
        except BaseException:
            await self._mcp_manager.rollback_startup()
            # No Runtime was published. A retried creation must load a fresh
            # provider/project snapshot, not preserve this provisional attempt.
            self._instruction_manager.discard_startup_snapshot()
            self._execution_warnings_pending = ()
            if self._writer is not None:
                await self._writer.aclose()
            raise

    async def _initialize_owned(self) -> None:
        initializing = asyncio.current_task()
        assert initializing is not None
        permissions = self._execution_permissions_input
        if permissions is not None:
            self._process_manager.approvals.rules.bind_loop()
            folders = self._exec_policy_config_folders
            if not self._session_source.is_non_root_agent:
                permissions = replace(permissions, exec_policy_snapshot=None)
            if self._session_source.is_basic_guardian:
                # Native basic guardians use only managed exec policy, never
                # user/project rules or the inherited parent's user snapshot.
                folders = ()
                permissions = replace(
                    permissions,
                    exec_policy_sources=(),
                    exec_policy_snapshot=None,
                    approval_policy_json='"never"',
                    requested_approval_policy_json='"never"',
                    approval_policy_explicit=True,
                    approval_policy_constraint="guardian",
                )
            resolved, warnings = await resolve_execution_permissions(
                permissions, exec_policy_config_folders=folders
            )
            if self._closed or initializing.cancelling():
                raise asyncio.CancelledError
            inherited = self._inherited_exec_policy
            if (
                inherited is not None
                and not self._session_source.is_basic_guardian
                and resolved.exec_policy_snapshot == inherited.snapshot
            ):
                # Native resolution has checked the managed policy identity;
                # the snapshot also includes config folders and declared sources.
                # Keep this session's router, consent cache, and write path intact.
                self._process_manager.approvals.rules.inherit(inherited)
            # Publish one immutable effective policy before any Turn can be admitted.
            # Model setting updates must retain it instead of restoring the raw profile.
            self._settings = replace(self._settings, execution_permissions=resolved)
            self._graph._settings = replace(self._graph._settings, execution_permissions=resolved)
            self._graph._turn_settings = replace(
                self._graph._turn_settings, execution_permissions=resolved
            )
            self._thread_settings.settings = replace(
                self._thread_settings.settings, execution_permissions=resolved
            )
            self._execution_warnings_pending = warnings
        self._graph._context_builder = self._graph._context_builder.with_instruction_settings(
            self._settings
        )
        await self._graph._context_builder.initialize_instructions(self._settings.working_directory)
        await self._ensure_thread()
        self._graph._context_builder = await self._graph._context_builder.with_session_base(
            self._repository, self._thread_id, self._settings.model
        )
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
        await self._mcp_manager.start_session()
        if self._closed or initializing.cancelling():
            raise asyncio.CancelledError
        self._sync_tool_search()
        self._sync_code_mode_tools()

        # The context-manager returned by LangGraph is single use. Build a
        # fresh one for every attempt and publish no partially initialized
        # state, so a transient SQLite/setup failure can be retried safely.
        checkpoint_context = AsyncSqliteSaver.from_conn_string(
            ":memory:" if self._ephemeral else str(self._checkpoint_path)
        )
        checkpointer: AsyncSqliteSaver | None = None
        try:
            checkpointer = await checkpoint_context.__aenter__()
            if self._ephemeral:
                await checkpointer.conn.execute("PRAGMA temp_store=MEMORY")
            checkpointer.serde = checkpoint_serializer()
            checkpointer.jsonplus_serde = checkpointer.serde
            await setup_checkpoint(checkpointer)
            if self._closed or initializing.cancelling():
                raise asyncio.CancelledError
            if not self._ephemeral:
                self._checkpoint_path.chmod(0o600)
            compiled = self._graph.compile(checkpointer=checkpointer)
            await self._repository.save_thread_model_settings(
                self._thread_id,
                ThreadModelSettings(
                    model=self._settings.model,
                    provider=self._settings.provider_id
                    or self._settings.provider_name
                    or "default",
                    reasoning_effort=self._settings.reasoning_effort,
                    collaboration_mode=self._settings.collaboration_mode,
                    collaboration_instructions=self._settings.collaboration_instructions,
                    personality=self._settings.personality,
                ),
            )
            if self._closed or initializing.cancelling():
                raise asyncio.CancelledError
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
        self._mcp_prewarm.start()

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
        if servers is not None:
            self._plugin_catalog_base = None
        self._mcp_prewarm.request()

    @property
    def mcp_requirements_snapshot(self) -> MCPRequirementsSnapshot | None:
        """Expose captured startup authority and its sources without rereading disk."""
        return self._mcp_requirements_snapshot

    async def instruction_sources(self) -> tuple[Path, ...]:
        """Report current instruction provenance without rereading its files."""
        await self._ensure_ready()
        return self._instruction_manager.sources

    async def user_instructions(self) -> Instructions | None:
        """Return the immutable host snapshot available for non-root inheritance."""
        await self._ensure_ready()
        return self._instruction_manager.user_instructions

    @property
    def mcp_catalog(self) -> MCPCatalog | None:
        """Published source identities and disabled winners, detached from internal state."""
        return self._mcp_manager.catalog

    def request_mcp_catalog(self, catalog: MCPCatalog) -> None:
        """Publish new trusted declarations at the next preparation/call admission boundary."""
        if self._closed:
            raise RuntimeError("Corki runtime is closed")
        if not isinstance(catalog, MCPCatalog):
            raise ValueError("mcp_catalog must be a host-owned catalog")
        self._mcp_manager.request_catalog(
            catalog.with_default_cwd(self._settings.working_directory)
        )
        self._plugin_catalog_base = None
        self._mcp_prewarm.request()

    def request_mcp_runtime_context(self, context: MCPRuntimeContext) -> None:
        """Replace host-selected environment handles at the next tool admission boundary."""
        if self._closed:
            raise RuntimeError("Corki runtime is closed")
        self._mcp_manager.request_runtime_context(context)
        self._mcp_prewarm.request()

    async def mcp_tool_catalog(self) -> tuple[MCPToolCatalogEntry, ...]:
        """Host-only discovery metadata, distinct from model-visible callable tools."""
        await self._ensure_ready()
        return await self._mcp_manager.list_tool_catalog()

    def request_mcp_reconcile(
        self,
        servers: tuple[MCPServerSettings, ...] | None = None,
        *,
        server_metadata: Mapping[str, MCPServerMetadata] | None = None,
    ) -> None:
        """Queue configuration reconciliation through normal prepare/call admission.

        Healthy transport sessions survive policy-only changes; already admitted
        calls keep their original view. Explicit request_mcp_refresh still reconnects.
        """
        if self._closed:
            raise RuntimeError("Corki runtime is closed")
        self._mcp_manager.request_reconcile(servers, server_metadata=server_metadata)
        if servers is not None:
            self._plugin_catalog_base = None
        self._mcp_prewarm.request()

    async def _refresh_tools(self) -> None:
        await self._mcp_manager.capture_tools(
            optional_startup_grace_ms=self._settings.mcp_optional_startup_grace_ms
        )
        self._sync_tool_search()
        self._sync_code_mode_tools()

    async def input_reference_catalog(self):
        """Local composer metadata; never fetch a provider/official service catalog."""
        from corki.skills.io import run_skill_io

        service = self._skill_service
        if service is not None:
            snapshot = await run_skill_io(service.snapshot, self._settings.working_directory)
            skills = tuple(s for s in snapshot.skills if snapshot.is_enabled(s))
        else:
            skills = ()
        plugins = tuple(p.manifest for p in self._plugin_manager.plugins)
        return skills, plugins

    async def _refresh_input_tools(self, state) -> dict:
        # Local skill resolution must not depend on the official Apps catalog.
        items = (
            *await self._repository.load_items(self._thread_id),
            *state.get("pending_input_items", ()),
        )
        requirements = await collect_input_requirements_async(
            state,
            items,
            cwd=Path(state["cwd"]),
            skills=self._skill_service,
            plugins=self._plugin_manager,
            disabled=self._session_source.is_basic_guardian,
        )
        await self._mcp_manager.capture_tools(
            optional_startup_grace_ms=self._settings.mcp_optional_startup_grace_ms,
            required_servers=requirements["mcp_required_servers"],
            required_plugins=requirements["mcp_required_plugins"],
        )
        await self._skill_mcp_dependencies.install(self, state, items)
        self._sync_tool_search()
        self._sync_code_mode_tools()
        return requirements

    def _sync_code_mode_tools(self) -> None:
        owned = self._registry.owned_names(self._code_mode_owner)
        if self._tool_router.mode(self._settings) == "direct":
            if owned:
                self._registry.replace_owned(self._code_mode_owner, ())
            return
        controls = []
        adopt = []
        for name, tool_type in (("exec", CodeModeExecTool), ("wait", CodeModeWaitTool)):
            existing = self._registry.get(name)
            if existing is not None and (
                not isinstance(existing, tool_type) or existing.service is not self._code_mode
            ):
                # The selected Turn finalizer owns precedence and strict errors.
                # Keep host sources intact; do not publish a partial control pair.
                if owned:
                    self._registry.replace_owned(self._code_mode_owner, ())
                return
            if existing is not None and name not in owned:
                adopt.append(name)
            controls.append(tool_type(self._code_mode))
        if (
            not adopt
            and owned
            and all(
                self._registry.namespace_policy.project(tool.spec)
                == self._registry.spec(tool.spec.name)
                for tool in controls
            )
        ):
            return
        for name in adopt:
            self._registry.unregister(name)
        self._registry.replace_owned(self._code_mode_owner, tuple(controls))

    def _sync_tool_search(self) -> None:
        enabled = self._tool_router.search_enabled(self._settings) and bool(
            self._registry.deferred_entries()
        )
        existing = self._registry.get("tool_search")
        owned = self._registry.owned_names(self._search_owner)
        if not enabled and not owned:
            return
        if existing is not None and "tool_search" not in owned:
            if not isinstance(existing, ToolSearchTool):
                # Planning may shadow this source, but global publication cannot
                # take its ownership or fail before a Turn has been admitted.
                return
            # Adopt a caller-supplied harness search tool during composition only.
            self._registry.unregister("tool_search")
        if enabled:
            candidate = self._search_cache.get_or_build(
                self._registry, include_sources=not self._settings.deferred_tool_world_state
            )
            if candidate is existing:
                return
            self._registry.replace_owned(self._search_owner, (candidate,))
        elif owned:
            self._registry.replace_owned(self._search_owner, ())

    async def list_background_terminals(self) -> tuple[BackgroundTerminalInfo, ...]:
        return self._process_manager.list_background_terminals()

    async def terminate_background_terminal(self, process_id: str) -> bool:
        return await self._process_manager.terminate_background_terminal(process_id)

    async def clean_background_terminals(self) -> None:
        await self._process_manager.terminate_all()

    async def reset_memory(self) -> tuple[Path, ...]:
        """Explicitly clear generated memory, preserving conversations and memory settings."""
        if self._closed:
            raise RuntimeError("runtime is closed")
        if self._memory_resetter is None:
            raise RuntimeError("memory reset was not configured for this runtime")
        return await self._memory_resetter.reset()

    async def set_thread_memory_mode(
        self, mode: ThreadMemoryMode | str, *, thread_id: str | None = None
    ) -> None:
        """Persist source eligibility, not recall/use settings or a forgetting request."""
        if self._closed:
            raise RuntimeError("runtime is closed")
        if self._ephemeral:
            raise RuntimeError("ephemeral sessions do not support persistent thread memory mode")
        await self._thread_memory.set_mode(mode, thread_id=thread_id)

    @property
    def memory_reset_targets(self) -> tuple[Path, ...]:
        """Expose actual reset roots so host confirmation cannot guess a different home."""
        return self._memory_resetter.targets if self._memory_resetter is not None else ()

    async def stream_close(self) -> AsyncIterator[RuntimeEvent]:
        """Observe shutdown hooks live without lending cancellation ownership of teardown."""
        waiter = asyncio.create_task(self.aclose())
        events = self._shutdown_events
        waiter.add_done_callback(lambda _: events.changed.set())
        cursor = 0
        try:
            while True:
                while cursor < len(events.items):
                    event = events.items[cursor]
                    cursor += 1
                    yield event
                if waiter.done():
                    await waiter
                    return
                events.changed.clear()
                await events.changed.wait()
        finally:
            if not waiter.done():
                waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            # The active Turn still owns its Interrupt dispatch. Its shared
            # hook owner closes in _close_resources after that Turn has joined.
            # New Turns/inputs are already rejected by the Runtime boundary.
            self._graph._async_post_hooks.owner.close_admission()
            self._graph._async_pre_hooks.owner.close_admission()
            self._thread_memory.close_admission()
            self._realtime.close_input()
            startup = self._startup_task
            if startup is not None and not startup.done() and not startup.cancelling():
                startup.cancel()
            self._close_task = asyncio.create_task(self._close_resources(), name="corki-close")
        elif self._close_task.done() and self._close_storage_pending:
            # Execution services are already closed. Retry only the retained
            # storage barrier; admission remains permanently closed.
            self._close_task = asyncio.create_task(
                self._close_storage_resources(), name="corki-close-storage"
            )
        # A cancelled waiter must not abandon or interrupt shared teardown.
        await asyncio.shield(self._close_task)

    async def archive(self) -> ThreadRecord:
        """Close this Runtime and archive its durable Thread without deleting history."""
        if self._ephemeral:
            raise RuntimeError("ephemeral sessions do not support archive")
        if self._thread_archive is None:
            raise RuntimeError("thread archive storage was not configured")
        return await self._thread_archive.archive()

    async def unarchive(self) -> ThreadRecord:
        """Restore this Thread's collection; a closed Runtime stays closed."""
        if self._ephemeral:
            raise RuntimeError("ephemeral sessions do not support unarchive")
        if self._thread_archive is None:
            raise RuntimeError("thread archive storage was not configured")
        return await self._thread_archive.unarchive()

    async def _close_resources(self) -> None:
        # Stop execution before waiting on potentially slow catalog cleanup.
        # Closed admission prevents a successor from starting in this interval.
        active = self._active_run
        if active is not None:
            active.cancel()
        error = self._checkpoint_cleanup_error
        try:
            await self._turn_updates.aclose()
        except BaseException as exc:
            error = error or exc
        async with self._lifecycle_lock, self._ready_lock:
            error = error or self._checkpoint_cleanup_error
            active = self._active_run
            if active is not None:
                active.cancel()
                await active.join()
                error = error or active.error or active.cleanup_error
            for close in (
                self._graph._stop_hooks._async.aclose,
                self._graph._async_post_hooks.owner.aclose,
                self._graph._async_pre_hooks.owner.aclose,
                self._mcp_prewarm.aclose,
                self._thread_memory.aclose,
                *((self._memory_resetter.aclose,) if self._memory_resetter is not None else ()),
                *((self._code_mode.aclose,) if self._code_mode is not None else ()),
                self._process_manager.terminate_all,
                *((self._memory_service.aclose,) if self._memory_service is not None else ()),
                self._mcp_manager.aclose,
                self._plugin_manager.aclose,
                *((self._history_notes.aclose,) if self._history_notes is not None else ()),
                self._model.aclose,
            ):
                try:
                    await close()
                except BaseException as exc:
                    error = error or exc
            try:
                await self._close_storage_resources()
            except BaseException as exc:
                error = error or exc
            if error is not None:
                raise error

    @staticmethod
    def _retryable_terminal_storage_error(error: Exception) -> bool:
        if isinstance(error, sqlite3.OperationalError):
            code = getattr(error, "sqlite_errorcode", None)
            return isinstance(code, int) and code & 0xFF in {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
                sqlite3.SQLITE_IOERR,
                sqlite3.SQLITE_FULL,
                sqlite3.SQLITE_CANTOPEN,
            }
        return isinstance(error, OSError)

    async def _flush_pending_terminals(self) -> None:
        """Drain owned terminal facts before admission, recovery or storage close."""
        for turn_id, record in tuple(self._pending_terminals.items()):
            # Failure retains both the unwritten suffix and storage/Thread lease.
            # The joined transaction cannot overwrite a newer conflicting terminal.
            try:
                await self._repository.retry_turn_terminal(record)
            except (OSError, sqlite3.OperationalError) as exc:
                if not self._retryable_terminal_storage_error(exc):
                    raise
                _LOG.warning("Pending terminal write failed; retrying once: %s", exc)
                # A new transaction rechecks identity and any ambiguous prior commit.
                # Never retry the model, tools, integrity failures or cancellation.
                await self._repository.retry_turn_terminal(record)
            del self._pending_terminals[turn_id]

    async def _close_storage_resources(self) -> None:
        self._close_storage_pending = True
        await self._flush_pending_terminals()
        if self._input_flush_pending:
            # On failure, keep the database and Thread writer lease alive so a
            # later aclose can drain the same item identities without replaying work.
            await self._flush_realtime_inputs()
        self._close_storage_pending = False
        error = None
        # SessionEnd observes the final admitted history. Only the write barriers
        # above may be retried: once we start shutdown effects, storage cleanup
        # must run even on failure and a later aclose must not replay those effects.
        if self._execution_identity is not None:
            from corki.core.session_end_hooks import run as run_session_end

            try:
                await run_session_end(
                    self._graph._stop_hooks,
                    repository=self._repository,
                    thread=self._thread_id,
                    session_source=self._session_source,
                    settings=self._settings,
                    shell=self._process_manager.shell,
                    events=self._shutdown_events,
                )
            except BaseException as exc:
                error = exc
        try:
            await self._repository.close()
        except BaseException as exc:
            error = error or exc
        if self._checkpointer is not None and self._checkpoint_context is not None:
            try:
                await close_checkpoint(self._checkpoint_context, self._checkpointer)
            except BaseException as exc:
                error = error or exc
            self._checkpointer = None
            self._checkpoint_context = None
        try:
            if self._writer is not None:
                await self._writer.aclose()
        except BaseException as exc:
            error = error or exc
        if error is not None:
            raise error

    async def _ensure_thread(self) -> None:
        if self._closed:
            raise RuntimeError("Corki runtime is closed")
        if self._thread_created and (self._writer is None or self._writer.held):
            return
        async with self._thread_lock:
            if self._writer is not None:
                await self._writer.acquire()
            try:
                if self._closed:
                    raise RuntimeError("Corki runtime is closed")
                if self._archive_store is not None and not self._include_archived:
                    try:
                        record = await self._archive_store.read(self._thread_id)
                    except LookupError:
                        record = None
                    if record is not None and record.archived_at is not None:
                        raise ValueError(
                            f"thread {self._thread_id} is archived; unarchive it before resuming"
                        )
                if not self._thread_created:
                    if not self._start_source_resolved:
                        existing = await self._repository.thread_exists(self._thread_id)
                        if existing and (
                            self._requested_start_source is not None
                            or self._fork_source is not None
                        ):
                            raise ValueError("explicit session start requires a new thread")
                        self._graph._stop_hooks.start_source = (
                            "resume" if existing else self._requested_start_source or "startup"
                        )
                        self._start_source_resolved = True
                    create_thread = (
                        self._repository.create_thread
                        if self._fork_source is None
                        else self._repository.fork_thread
                    )
                    if self._fork_repository is not None and self._fork_snapshot is None:
                        self._fork_snapshot = await self._fork_repository.load_fork_snapshot(
                            self._fork_source
                        )
                    if (
                        self._fork_source is not None
                        and self._settings.base_instructions is not None
                    ):
                        if self._fork_snapshot is None:
                            self._fork_snapshot = await self._repository.load_fork_snapshot(
                                self._fork_source
                            )
                        self._fork_snapshot = replace(
                            self._fork_snapshot,
                            base_instructions=(
                                self._settings.model,
                                self._settings.base_instructions,
                            ),
                            base_instructions_provenance="custom",
                        )
                    await create_thread(
                        self._thread_id,
                        self._settings.working_directory,
                        memory_mode=(
                            ThreadMemoryMode.ENABLED
                            if self._settings.memories_generate
                            else ThreadMemoryMode.DISABLED
                        ),
                        session_id=self._initial_session_id,
                        session_source=self._session_source,
                        **(
                            {
                                "source_thread_id": self._fork_source,
                                "before_user_message": self._fork_before,
                                **(
                                    {"snapshot": self._fork_snapshot}
                                    if self._fork_snapshot is not None
                                    else {}
                                ),
                            }
                            if self._fork_source is not None
                            else {}
                        ),
                    )
                    identity = ExecutionIdentity(
                        self._thread_id,
                        await self._repository.load_thread_session_id(self._thread_id),
                    )
                    self._process_manager.bind_identity(identity)
                    if self._history_notes is not None:
                        self._history_notes.bind_identity(identity)
                    self._execution_identity = identity
                    self._thread_created = True
                    self._fork_snapshot = None
                    self._fork_repository = None
            except BaseException:
                if self._writer is not None:
                    await self._writer.aclose()
                raise


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
        "turn_model_settings": capture_model_settings(settings),
        "cwd": str(settings.working_directory),
        "timezone_name": local_time.timezone_name(),
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
            allow_login_shell=settings.allow_login_shell,
        )
    )
    registry.register(
        WriteStdinTool(processes, max_yield_time_ms=settings.background_terminal_max_timeout)
    )
    registry.register(ApplyPatchTool(processes.approvals))
    registry.register(UpdatePlanTool())
    if settings.request_user_input_enabled:
        registry.register(
            RequestUserInputTool(
                default_mode_enabled=settings.default_mode_request_user_input,
            )
        )
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
