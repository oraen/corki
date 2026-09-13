"""Ephemeral consolidation using Corki's real Runtime, not a second tool loop."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path

from corki.config.settings import CorkiSettings
from corki.memory import git_baseline, workspace
from corki.memory.agent_artifacts import AgentArtifacts, SharedAgentArtifacts, collect, write_file
from corki.memory.agent_shutdown import ConsolidationShutdownError, ConsolidationShutdowns
from corki.memory.artifacts import (
    remove_memory_symlinks,
    validate_shared_artifacts,
    write_workspace_diff,
)
from corki.memory.consolidation_prompt import build_consolidation_prompt
from corki.memory.permissions import MemoryPermissionSnapshot
from corki.memory.workspace_lease import WorkspaceLeases
from corki.models.types import ModelCompleted, ModelItemCompleted
from corki.prompting import PromptStore
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_session_id
from corki.protocol.items import ToolCallItem
from corki.protocol.session_source import SessionSource

_SHUTDOWN_TIMEOUT_SECONDS = 10.0


class BorrowedModel:
    def __init__(self, model, effort, *, check_owner=None, admit_tools=None):
        self.model, self.effort = model, effort
        self.check_owner = check_owner
        self.admit_tools = admit_tools

    async def stream(self, request):
        from contextlib import aclosing

        if self.check_owner is not None:
            await self.check_owner()
        async with aclosing(
            self.model.stream(replace(request, reasoning_effort=self.effort))
        ) as stream:
            async for event in stream:
                items = (
                    event.items
                    if isinstance(event, ModelCompleted)
                    else (event.item,)
                    if isinstance(event, ModelItemCompleted)
                    else ()
                )
                if self.admit_tools is not None and any(isinstance(i, ToolCallItem) for i in items):
                    await self.admit_tools()
                if self.check_owner is not None and isinstance(
                    event, (ModelCompleted, ModelItemCompleted)
                ):
                    # A late tool call can mutate the shared workspace before
                    # final publication. Reject revoked owners before admission.
                    await self.check_owner()
                yield event

    async def aclose(self):
        pass  # The parent owns the shared transport, including stage-one requests.


async def _blocking(function, *args):
    """Keep filesystem work owned through cancellation before deleting its copy."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
    if cancelled is not None:
        try:
            task.result()
        except Exception as exc:
            raise cancelled from exc
        raise cancelled
    return task.result()


def _prepare(root: Path, sampled: workspace.Snapshot, workspace_diff: str) -> None:
    if workspace.validate(sampled) is None:
        raise ValueError("invalid consolidation input snapshot")
    root.mkdir(mode=0o700, exist_ok=True)
    for name, entry in sampled.items():
        write_file(root / name, entry)
    (root / "phase2_workspace_diff.md").write_text(workspace_diff, encoding="utf-8")


@dataclass(slots=True)
class PreparedAgent:
    """One owned workspace and admitted child configuration for one phase-two pass."""

    temporary: Path
    settings: CorkiSettings
    permissions: MemoryPermissionSnapshot
    shared: bool = False
    retained: bool = False
    started: bool = False
    check_owner: Callable[[], Awaitable[None]] | None = None
    execution_lease: WorkspaceLeases | None = None

    @property
    def root(self) -> Path:
        return self.settings.working_directory


@asynccontextmanager
async def prepare_agent(
    settings: CorkiSettings,
    parent_permissions: MemoryPermissionSnapshot | None = None,
    *,
    root: Path | None = None,
    prepare_shared: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[PreparedAgent]:
    """Establish the worker config before DB selection, sync, or no-change success."""
    temporary = Path(tempfile.mkdtemp(prefix="corki-memory-agent-"))
    shared = root is not None
    root = root if shared else temporary / "workspace"
    prepared = None
    try:
        if shared:
            if not root.is_absolute() or root.is_symlink() or not root.is_dir():
                raise ValueError("shared memory root must be an existing absolute directory")
            if prepare_shared is None:
                await _blocking(git_baseline.prepare, root)
            else:
                # The pipeline owns the claim and supplies its database write fence.
                # Do not prepare twice: preparation can remove another pass's diff.
                await prepare_shared()
        else:
            await _blocking(root.mkdir, 0o700)
        permissions = parent_permissions or MemoryPermissionSnapshot(settings.execution_permissions)
        worker_permissions = await permissions.for_worker(root)
        model_info = settings.model_context_info(settings.resolved_memory_consolidation_model)
        child_settings = replace(
            settings,
            working_directory=root,
            execution_permissions=worker_permissions,
            model=settings.resolved_memory_consolidation_model,
            reasoning_effort="medium",
            context_window_tokens=(
                model_info.resolved_context_window or settings.context_window_tokens
            ),
            effective_context_window_percent=model_info.effective_context_window_percent,
            memories_enabled=False,
            memories_generate=False,
            memories_use=False,
            memories_dedicated_tools=False,
            mcp_servers=(),
            mcp_approval_policy="never",
            plugin_dirs=(),
            realtime_enabled=False,
        )
        prepared = PreparedAgent(temporary, child_settings, permissions, shared=shared)
        yield prepared
    finally:
        # An unconfirmed Runtime close transfers cleanup to ConsolidationShutdowns.
        # All earlier failures and no-op passes still belong to this context.
        if prepared is None or not prepared.retained:
            if prepared is not None and prepared.execution_lease is not None:
                prepared.execution_lease.close()
            await _blocking(shutil.rmtree, temporary)


async def run_agent(
    *,
    settings,
    model,
    sampled: workspace.Snapshot,
    workspace_diff: str,
    instructions: str | None = None,
    prompt_store: PromptStore | None = None,
    home_path: Path | None = None,
    compatibility_home: Path | None = None,
    configured_skill_roots: tuple[Path, ...] = (),
    source_directory: Path | None = None,
    shutdowns: ConsolidationShutdowns | None = None,
    parent_permissions: MemoryPermissionSnapshot | None = None,
    prepared: PreparedAgent | None = None,
) -> str | AgentArtifacts | SharedAgentArtifacts:
    """Run a single worker, borrowing an admitted pass or owning a fresh one."""
    manager = (
        prepare_agent(settings, parent_permissions) if prepared is None else nullcontext(prepared)
    )
    async with manager as prepared:
        return await _run_prepared_agent(
            prepared,
            model=model,
            sampled=sampled,
            workspace_diff=workspace_diff,
            instructions=instructions,
            prompt_store=prompt_store,
            home_path=home_path,
            compatibility_home=compatibility_home,
            configured_skill_roots=configured_skill_roots,
            source_directory=source_directory,
            shutdowns=shutdowns,
        )


async def _run_prepared_agent(
    prepared: PreparedAgent,
    *,
    model,
    sampled: workspace.Snapshot,
    workspace_diff: str,
    instructions: str | None,
    prompt_store: PromptStore | None,
    home_path: Path | None,
    compatibility_home: Path | None,
    configured_skill_roots: tuple[Path, ...],
    source_directory: Path | None,
    shutdowns: ConsolidationShutdowns | None,
) -> str | AgentArtifacts | SharedAgentArtifacts:
    # Lazy import avoids a module cycle; execution still uses the normal Runtime.
    from corki.core.runtime import LangGraphRuntime

    if prepared.started:
        raise ValueError("prepared consolidation agent has already been used")
    prepared.started = True
    temporary, root = prepared.temporary, prepared.root
    runtime, error, result = None, None, None
    cancelled = False
    shutdowns = shutdowns or ConsolidationShutdowns()

    async def admit_tools():
        if prepared.shared and prepared.execution_lease is None:

            def acquire():
                # Store ownership before returning from the joined worker: a
                # cancelled await must not discard an acquired descriptor.
                prepared.execution_lease = WorkspaceLeases((root,))

            await _blocking(acquire)
        # BorrowedModel checks the claim after acquisition, closing the gap
        # between the earlier request admission and the reset's workspace lock.

    async def retained_cleanup():
        if prepared.execution_lease is not None:
            prepared.execution_lease.close()
        await _blocking(shutil.rmtree, temporary)

    try:
        if prepared.shared:
            await _blocking(write_workspace_diff, root, workspace_diff)
        else:
            await _blocking(_prepare, root, sampled, workspace_diff)
        if instructions is None:
            instructions = await _blocking(
                build_consolidation_prompt, prompt_store or PromptStore(), root, prepared.shared
            )
        runtime = await LangGraphRuntime.acreate(
            settings=prepared.settings,
            # Codex starts consolidation through a fresh internal AgentControl,
            # not the parent-linked spawn_internal_session entry point.
            session_id=new_session_id(),
            session_source=SessionSource.internal("memory_consolidation"),
            ephemeral=True,
            database_path=temporary / "state" / "thread.db",
            model=BorrowedModel(
                model, "medium", check_owner=prepared.check_owner, admit_tools=admit_tools
            ),
            home_path=home_path or temporary / "home",
            compatibility_home=compatibility_home,
            load_plugins=False,
            configured_skill_roots=configured_skill_roots,
            context_source_directory=source_directory,
            mcp_requirements=prepared.permissions.managed,
        )
        terminal = None
        async for event in runtime.stream(instructions):
            terminal = event  # Drain background events without notifying the parent UI.
        if not isinstance(terminal, TurnCompleted):
            raise ValueError(f"consolidation agent did not complete: {terminal}")
        result = terminal.final_answer
    except BaseException as exc:
        error = exc
    finally:
        if runtime is not None:
            try:
                cancelled = await shutdowns.close(
                    runtime,
                    temporary,
                    retained_cleanup,
                    timeout=_SHUTDOWN_TIMEOUT_SECONDS,
                )
            except BaseException as exc:
                prepared.retained = True
                failure = (
                    exc
                    if isinstance(exc, ConsolidationShutdownError)
                    else (
                        ConsolidationShutdownError(
                            f"consolidation shutdown failed; retained {temporary}: {exc}"
                        )
                    )
                )
                if failure.cancelled and not isinstance(error, asyncio.CancelledError):
                    error = asyncio.CancelledError()
                if isinstance(error, asyncio.CancelledError):
                    error.__cause__ = failure
                else:
                    error = failure
    if cancelled and error is None:
        error = asyncio.CancelledError()
    if error is not None:
        if prepared.shared and runtime is not None and not prepared.retained:
            try:
                await _blocking(remove_memory_symlinks, root)
            except Exception:
                logging.getLogger(__name__).warning(
                    "failed removing memory workspace symbolic links", exc_info=True
                )
        raise error
    # Existing complete JSON outputs remain a compatibility path; file-editing
    # agents can simply finish after leaving valid artifacts.
    if result and result.lstrip().startswith("{"):
        return result
    if prepared.shared:
        await _blocking(validate_shared_artifacts, root)
        return SharedAgentArtifacts()
    return await _blocking(collect, root, sampled)
