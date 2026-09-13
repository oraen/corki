"""Two-phase, Codex-aligned long-term memory generation pipeline."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path

from corki.config.managed_mcp import MCPRequirementsSnapshot
from corki.config.settings import CorkiSettings
from corki.memory import git_baseline, workspace
from corki.memory.agent import ConsolidationShutdownError, PreparedAgent, prepare_agent, run_agent
from corki.memory.agent import _blocking as _owned_file_operation
from corki.memory.agent_artifacts import AgentArtifacts, SharedAgentArtifacts
from corki.memory.agent_artifacts import publish as publish_agent_artifacts
from corki.memory.agent_shutdown import ConsolidationShutdowns, RetainedWorker
from corki.memory.artifacts import (
    remove_workspace_diff,
    sync_stage_one_artifacts,
    validate_shared_artifacts,
    write_consolidated_artifacts,
)
from corki.memory.claim_handoff import handoff_claim
from corki.memory.consolidation_prompt import seed_extension_instructions
from corki.memory.inputs import extraction_token_budget, truncate_rollout
from corki.memory.models import (
    ConsolidatedMemory,
    ConsolidationClaim,
    MemoryExtractionClaim,
    MemorySkill,
    StageOneMemory,
)
from corki.memory.permissions import MemoryPermissionSnapshot, MemorySandboxPolicyError
from corki.memory.repository import MemoryRepository
from corki.memory.sanitizer import redact_secrets
from corki.memory.transcript import render_transcript
from corki.models import ModelCompleted, ModelRequest
from corki.models.base import ModelPort
from corki.prompting import PromptStore
from corki.protocol.ids import ThreadId, new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ConversationItem,
    UserMessageItem,
)

_EXTRACTION_CONCURRENCY = 8
_PRUNE_BATCH_SIZE = 200

_STAGE_ONE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "raw_memory": {"type": "string"},
        "rollout_summary": {"type": "string"},
        "rollout_slug": {"type": ["string", "null"]},
    },
    "required": ["raw_memory", "rollout_summary", "rollout_slug"],
    "additionalProperties": False,
}

_CONSOLIDATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "memory": {"type": "string"},
        "memory_summary": {"type": "string"},
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["name", "description", "content"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["memory", "memory_summary", "skills"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class MemoryRunReport:
    claimed: int = 0
    extracted: int = 0
    empty: int = 0
    failed: int = 0
    consolidated: bool = False
    consolidation_skipped: bool = False


class LongTermMemoryService:
    """Run bounded background extraction and singleton global consolidation."""

    def __init__(
        self,
        *,
        settings: CorkiSettings,
        repository: MemoryRepository,
        model: ModelPort,
        root: Path,
        close_model: bool = False,
        prompt_store: PromptStore | None = None,
        home_path: Path | None = None,
        compatibility_home: Path | None = None,
        configured_skill_roots: tuple[Path, ...] = (),
        thread_id: ThreadId | None = None,
        managed_requirements: MCPRequirementsSnapshot | None = None,
    ) -> None:
        self._settings = settings
        self._managed_requirements = managed_requirements
        self._request_thread_id = thread_id or new_thread_id()
        self._repository = repository
        self._model = model
        self._root = root
        self._close_model = close_model
        self._prompts = prompt_store or PromptStore()
        self._home_path = home_path or root.parent
        self._compatibility_home = compatibility_home
        self._configured_skill_roots = configured_skill_roots
        self._task: asyncio.Task[MemoryRunReport] | None = None
        self._tasks: set[asyncio.Task[MemoryRunReport]] = set()
        self._warnings: list[str] = []
        self._agent_shutdowns = ConsolidationShutdowns()
        self._close_task: asyncio.Task | None = None

    @property
    def warnings(self) -> tuple[str, ...]:
        return (*self._warnings, *self._agent_shutdowns.warnings)

    @property
    def retained_workers(self) -> tuple[RetainedWorker, ...]:
        """Unconfirmed children remain inspectable after their background job ends."""
        return self._agent_shutdowns.retained

    def start(
        self,
        current_thread_id: ThreadId,
        *,
        parent_permissions: MemoryPermissionSnapshot | None = None,
    ) -> None:
        """Own one pass per eligible new Turn; database claims arbitrate overlap."""

        if (
            not self._settings.memories_enabled
            or not self._settings.memories_background_enabled
            or self._close_task is not None
        ):
            return
        self._task = asyncio.create_task(
            self.run_once(
                current_thread_id,
                parent_permissions=parent_permissions
                or MemoryPermissionSnapshot(
                    self._settings.execution_permissions, self._managed_requirements
                ),
            ),
            name="corki-long-term-memory",
        )
        self._tasks.add(self._task)
        self._task.add_done_callback(self._capture_background_failure)

    async def wait(self) -> MemoryRunReport | None:
        """Observe accepted passes without owning cancellation; return the latest report."""
        latest = self._task
        if latest is None:
            return None
        # Include an older active pass even if the latest has already skipped a
        # busy claim. New Turns accepted after this snapshot are a later wait.
        tasks = self._tasks | {latest}
        await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
        return latest.result()

    async def run_once(
        self,
        current_thread_id: ThreadId,
        *,
        parent_permissions: MemoryPermissionSnapshot | None = None,
    ) -> MemoryRunReport:
        """Own an awaited pass independently of the embedding caller's task."""
        if self._close_task is not None:
            raise RuntimeError("memory service is closed")
        if asyncio.current_task() in self._tasks:
            # start() already registered this pass; retain its override and
            # background-result semantics without introducing another owner.
            return await self._run_once(current_thread_id, parent_permissions=parent_permissions)
        task = asyncio.create_task(
            self._run_once(current_thread_id, parent_permissions=parent_permissions),
            name="corki-memory-direct-pass",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done() and not task.cancelling():
                task.cancel()
            await _join_owned((task,))
            raise

    async def _run_once(
        self,
        current_thread_id: ThreadId,
        *,
        parent_permissions: MemoryPermissionSnapshot | None = None,
    ) -> MemoryRunReport:
        parent_permissions = parent_permissions or MemoryPermissionSnapshot(
            self._settings.execution_permissions, self._managed_requirements
        )
        if self._close_task is not None:
            raise RuntimeError("memory service is closed")
        try:
            await _owned_file_operation(git_baseline.ensure_layout, self._root)
        except (OSError, ValueError) as exc:
            self._warnings.append(f"memory layout could not be prepared: {exc}")
            return MemoryRunReport(failed=1)
        try:
            await _owned_file_operation(seed_extension_instructions, self._root)
        except (OSError, ValueError) as exc:
            self._warnings.append(f"memory extension instructions could not be seeded: {exc}")
        try:
            await self._repository.prune_stage_one_outputs(
                max_unused_days=self._settings.memories_max_unused_days,
                limit=_PRUNE_BATCH_SIZE,
            )
        except Exception as exc:  # noqa: BLE001 - maintenance failure must not stop generation
            self._warnings.append(f"memory retention pruning failed: {exc}")
        claims = await handoff_claim(
            self._repository.claim_extraction_jobs(
                current_thread_id=current_thread_id,
                max_age_days=self._settings.memories_max_thread_age_days,
                min_idle_hours=self._settings.memories_min_thread_idle_hours,
                limit=self._settings.memories_max_threads_per_startup,
                lease_seconds=self._settings.memories_lease_seconds,
            ),
            on_cancel=self._cancel_extraction_claims,
            warn=self._warnings.append,
        )
        semaphore = asyncio.Semaphore(_EXTRACTION_CONCURRENCY)

        async def extract(claim: MemoryExtractionClaim) -> str:
            async with semaphore:
                try:
                    memory = await self._extract(claim)
                    completed = await self._repository.complete_extraction(claim, memory)
                    if not completed:
                        return "failed"
                    return "extracted" if memory is not None else "empty"
                except Exception as exc:  # noqa: BLE001 - isolated background job boundary
                    await self._record_extraction_failure(claim, f"{type(exc).__name__}: {exc}")
                    self._warnings.append(f"phase-one memory extraction failed: {exc}")
                    return "failed"

        jobs = tuple(asyncio.create_task(extract(claim)) for claim in claims)
        try:
            try:
                outcomes = await asyncio.gather(*jobs)
            finally:
                for job in jobs:
                    if not job.done():
                        job.cancel()
                await _join_owned(jobs)
        except asyncio.CancelledError:
            # Includes jobs cancelled before first execution or while waiting
            # for a semaphore slot. Join every worker before releasing claims;
            # the running+owner fence preserves already committed successes.
            cleanup = asyncio.create_task(
                self._cancel_extraction_claims(claims, error="memory extraction cancelled")
            )
            await _join_owned((cleanup,))
            raise
        report = MemoryRunReport(
            claimed=len(claims),
            extracted=outcomes.count("extracted"),
            empty=outcomes.count("empty"),
            failed=outcomes.count("failed"),
        )
        claim = await handoff_claim(
            self._repository.claim_consolidation(
                lease_seconds=self._settings.memories_lease_seconds
            ),
            on_cancel=self._cancel_consolidation_claim,
            warn=self._warnings.append,
        )
        if claim is None:
            return report
        try:
            skipped = await self._run_owned_consolidation(claim, parent_permissions)
            return MemoryRunReport(
                claimed=report.claimed,
                extracted=report.extracted,
                empty=report.empty,
                failed=report.failed,
                consolidated=not skipped,
                consolidation_skipped=skipped,
            )
        except asyncio.CancelledError as exc:
            if isinstance(exc.__cause__, ConsolidationShutdownError):
                self._warnings.append(str(exc.__cause__))
                raise
            cleanup = asyncio.create_task(
                self._record_consolidation_failure(claim, "memory consolidation cancelled")
            )
            await _join_owned((cleanup,))
            raise
        except Exception as exc:  # noqa: BLE001 - isolate background consolidation failures
            if not isinstance(exc, ConsolidationShutdownError):
                await self._record_consolidation_failure(
                    claim,
                    "failed_sandbox_policy"
                    if isinstance(exc, MemorySandboxPolicyError)
                    else f"{type(exc).__name__}: {exc}",
                )
            self._warnings.append(f"phase-two memory consolidation failed: {exc}")
            return MemoryRunReport(
                claimed=report.claimed,
                extracted=report.extracted,
                empty=report.empty,
                failed=report.failed + 1,
            )

    async def _cancel_extraction_claims(
        self,
        claims: tuple[MemoryExtractionClaim, ...],
        *,
        error: str = "memory extraction cancelled before dispatch",
    ) -> None:
        for claim in claims:
            await self._record_extraction_failure(claim, error)

    async def _cancel_consolidation_claim(self, claim: ConsolidationClaim | None) -> None:
        if claim is not None:
            await self._record_consolidation_failure(
                claim, "memory consolidation cancelled before dispatch"
            )

    async def _record_extraction_failure(self, claim: MemoryExtractionClaim, error: str) -> None:
        try:
            await self._repository.fail_extraction(
                claim, error, retry_delay_seconds=self._settings.memories_retry_delay_seconds
            )
        except Exception as exc:  # noqa: BLE001 - failed error reporting must not orphan siblings
            self._warnings.append(
                f"could not persist extraction failure (lease will expire): {exc}"
            )

    async def _record_consolidation_failure(self, claim: ConsolidationClaim, error: str) -> None:
        try:
            await self._repository.fail_consolidation(
                claim, error, retry_delay_seconds=self._settings.memories_retry_delay_seconds
            )
        except Exception as exc:  # noqa: BLE001 - preserve failure/cancel control flow
            self._warnings.append(
                f"could not persist consolidation failure (lease will expire): {exc}"
            )

    async def _run_owned_consolidation(
        self,
        claim: ConsolidationClaim,
        parent_permissions: MemoryPermissionSnapshot,
    ) -> bool:
        work = asyncio.create_task(
            self._consolidation_work(claim, parent_permissions), name="memory-consolidation"
        )
        heartbeat = asyncio.create_task(self._heartbeat(claim), name="memory-heartbeat")
        failure: BaseException | None = None
        try:
            done, _ = await asyncio.wait((work, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                # A failed heartbeat must stop sampling before we release the lease.
                heartbeat.result()
                raise RuntimeError("memory heartbeat unexpectedly stopped")
            selected, digest, consolidated, sampled = work.result()
        except BaseException as exc:
            failure = exc
        finally:
            for task in (work, heartbeat):
                if not task.done():
                    task.cancel()
            try:
                await _join_owned((work, heartbeat))
            except asyncio.CancelledError as exc:
                failure = exc
            # Joining must not discard a failed shutdown just because heartbeat
            # failure or parent cancellation won the race. That lease cannot be
            # released while the child may still be running.
            try:
                work.result()
            except BaseException as exc:
                shutdown = exc if isinstance(exc, ConsolidationShutdownError) else exc.__cause__
                if isinstance(shutdown, ConsolidationShutdownError):
                    if isinstance(failure, asyncio.CancelledError):
                        failure.__cause__ = shutdown
                    else:
                        failure = shutdown
        if failure is not None:
            raise failure

        # Stop heartbeats before the final transaction clears its token: a
        # heartbeat after a successful commit must not report false ownership
        # loss. Publication itself is fenced by the repository write lock.
        def publish() -> None:
            if consolidated is not None:
                if isinstance(consolidated, SharedAgentArtifacts):
                    git_baseline.reset(self._root)
                    return
                if isinstance(consolidated, AgentArtifacts):
                    publish_agent_artifacts(self._root, consolidated)
                else:
                    write_consolidated_artifacts(self._root, consolidated)
                remove_workspace_diff(self._root)
                validate_shared_artifacts(self._root)
                git_baseline.reset(self._root)

        if not await self._repository.complete_consolidation(claim, selected, publish=publish):
            raise RuntimeError("memory consolidation lease was lost before publication")
        return consolidated is None

    async def _heartbeat(self, claim: ConsolidationClaim) -> None:
        interval = min(30.0, self._settings.memories_lease_seconds / 3)
        while True:
            if not await self._repository.heartbeat_consolidation(
                claim, lease_seconds=self._settings.memories_lease_seconds
            ):
                raise RuntimeError("memory consolidation ownership lost during heartbeat")
            await asyncio.sleep(interval)

    async def _consolidation_work(
        self,
        claim: ConsolidationClaim,
        parent_permissions: MemoryPermissionSnapshot,
    ) -> tuple[
        tuple[StageOneMemory, ...],
        str,
        ConsolidatedMemory | AgentArtifacts | SharedAgentArtifacts | None,
        workspace.Snapshot,
    ]:
        async def prepare_shared() -> None:
            if not await self._repository.write_consolidation_workspace(
                claim, lambda: git_baseline.prepare(self._root)
            ):
                raise RuntimeError(
                    "memory consolidation ownership lost before workspace preparation"
                )

        async with prepare_agent(
            self._settings, parent_permissions, root=self._root, prepare_shared=prepare_shared
        ) as prepared:

            async def check_owner() -> None:
                if not await self._repository.write_consolidation_workspace(claim, lambda: None):
                    raise ValueError("memory consolidation ownership lost before model admission")

            prepared.check_owner = check_owner
            selected = await self._repository.load_consolidation_inputs(
                limit=self._settings.memories_max_raw_for_consolidation,
                max_unused_days=self._settings.memories_max_unused_days,
            )
            if not await self._repository.write_consolidation_workspace(
                claim, lambda: sync_stage_one_artifacts(self._root, selected)
            ):
                raise RuntimeError("memory consolidation ownership lost before workspace sync")
            sampled = await _owned_file_operation(git_baseline.capture, self._root)
            digest = workspace.digest(sampled, outputs=False)
            skipped = await _owned_file_operation(git_baseline.matches, self._root, sampled)
            consolidated = None if skipped else await self._consolidate(sampled, prepared)

            return selected, digest, consolidated, sampled

    async def _extract(self, claim: MemoryExtractionClaim) -> StageOneMemory | None:
        transcript = _render_transcript(claim.items)
        transcript = truncate_rollout(transcript, extraction_token_budget(self._settings))
        input_text = self._prompts.render(
            "memory/stage_one_input",
            thread_id=str(claim.thread_id),
            cwd=str(claim.cwd),
            transcript=transcript,
        )
        payload = await self._sample_json(
            model=self._settings.resolved_memory_extraction_model,
            reasoning_effort="low",
            instructions=self._prompts.render("memory/stage_one_system"),
            content=input_text,
            required={"raw_memory", "rollout_summary"},
            optional=frozenset({"rollout_slug"}),
            output_schema=_STAGE_ONE_SCHEMA,
            output_schema_name="corki_memory_extraction",
        )
        raw = _redact_secrets(_required_string(payload, "raw_memory"))
        summary = _redact_secrets(_required_string(payload, "rollout_summary"))
        slug_value = payload.get("rollout_slug")
        if slug_value is not None and not isinstance(slug_value, str):
            raise ValueError("rollout_slug must be a string or null")
        if not raw or not summary:
            return None
        return StageOneMemory(
            thread_id=claim.thread_id,
            cwd=claim.cwd,
            source_updated_at=claim.source_updated_at,
            raw_memory=raw,
            rollout_summary=summary,
            rollout_slug=_redact_secrets(slug_value) if slug_value is not None else None,
        )

    async def _consolidate(
        self,
        sampled: workspace.Snapshot,
        prepared: PreparedAgent,
    ) -> ConsolidatedMemory | AgentArtifacts | SharedAgentArtifacts:
        diff = await _owned_file_operation(
            lambda: workspace.render_diff(git_baseline.read(self._root), sampled)
        )
        result = await run_agent(
            settings=self._settings,
            prepared=prepared,
            model=self._model,
            sampled=sampled,
            prompt_store=self._prompts,
            workspace_diff=diff,
            home_path=self._home_path,
            compatibility_home=self._compatibility_home,
            configured_skill_roots=self._configured_skill_roots,
            source_directory=self._root,
            shutdowns=self._agent_shutdowns,
        )
        if isinstance(result, (AgentArtifacts, SharedAgentArtifacts)):
            return result
        payload = _structured_object(result, required=set(_CONSOLIDATION_SCHEMA["required"]))
        memory = _redact_secrets(_required_string(payload, "memory").strip())
        summary = _redact_secrets(_required_string(payload, "memory_summary").strip())
        if not memory or not summary:
            raise ValueError("consolidated memory and summary must not be empty")
        raw_skills = payload["skills"]
        if not isinstance(raw_skills, list):
            raise ValueError("skills must be an array")
        skills: list[MemorySkill] = []
        seen: set[str] = set()
        for raw_skill in raw_skills:
            if not isinstance(raw_skill, dict) or set(raw_skill) != {
                "name",
                "description",
                "content",
            }:
                raise ValueError("each memory skill must contain name, description, and content")
            name = _required_string(raw_skill, "name").strip()
            description = _redact_secrets(_required_string(raw_skill, "description").strip())
            body = _redact_secrets(_required_string(raw_skill, "content").strip())
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
                raise ValueError(f"invalid memory skill name: {name}")
            if name in seen or not description or not body:
                raise ValueError("memory skills must be unique and non-empty")
            seen.add(name)
            skills.append(MemorySkill(name, description, body))
        return ConsolidatedMemory(memory, summary, tuple(skills))

    async def _sample_json(
        self,
        *,
        model: str,
        reasoning_effort: str,
        instructions: str,
        content: str,
        required: set[str],
        output_schema: dict[str, object],
        output_schema_name: str,
        optional: frozenset[str] = frozenset(),
    ) -> dict[str, object]:
        turn_id = new_turn_id()
        request = ModelRequest(
            model=model,
            thread_id=str(self._request_thread_id),
            content_item_kinds=self._settings.content_item_kinds,
            model_info=self._settings.model_context_info(model),
            reasoning_summary=self._settings.reasoning_summary,
            service_tier=self._settings.session_service_tier,
            fast_mode_enabled=self._settings.fast_mode,
            reasoning_effort=reasoning_effort,
            instructions=instructions,
            context_items=(),
            items=(UserMessageItem(content, turn_id),),
            tools=(),
            output_schema=output_schema,
            output_schema_name=output_schema_name,
        )
        completed: ModelCompleted | None = None
        async with aclosing(self._model.stream(request)) as stream:
            async for event in stream:
                if isinstance(event, ModelCompleted):
                    completed = event
                    break
        if completed is None:
            raise ValueError("memory model stream ended without completion")
        text = "\n".join(
            item.content for item in completed.items if isinstance(item, AssistantMessageItem)
        ).strip()
        return _structured_object(text, required=required, optional=optional)

    def _capture_background_failure(self, task: asyncio.Task[MemoryRunReport]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:  # noqa: BLE001 - never fail an interactive turn from background
            self._warnings.append(f"memory startup failed: {type(exc).__name__}: {exc}")

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._close_resources(), name="memory-service-close"
            )
        await asyncio.shield(self._close_task)

    async def _close_resources(self) -> None:
        error: BaseException | None = None
        try:
            tasks = tuple(self._tasks)
            for task in tasks:
                if not task.done() and not task.cancelling():
                    task.cancel()
            await _join_owned(tasks)
        except BaseException as exc:
            error = exc
        for close in (
            self._agent_shutdowns.settle,
            *((self._model.aclose,) if self._close_model else ()),
            self._repository.close,
        ):
            try:
                await close()
            except BaseException as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise error


async def _join_owned(tasks: tuple[asyncio.Task, ...]) -> None:
    """Finish owned children even if cancellation is repeated during cleanup."""
    group = asyncio.gather(*tasks, return_exceptions=True)
    cancelled = False
    while not group.done():
        try:
            await asyncio.shield(group)
        except asyncio.CancelledError:
            cancelled = True
    group.result()
    if cancelled:
        raise asyncio.CancelledError


def _render_transcript(items: tuple[ConversationItem, ...]) -> str:
    return render_transcript(items, redact=_redact_secrets)


def _structured_object(text: str, *, required: set[str], optional=frozenset()) -> dict[str, object]:
    try:
        value = json.loads(text, object_pairs_hook=_unique_json_object)
    except json.JSONDecodeError as exc:
        raise ValueError("memory model returned invalid JSON") from exc
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or not value.keys() <= required | optional
    ):
        raise ValueError(
            f"memory model output requires: {', '.join(sorted(required))}; "
            f"allowed optional fields: {', '.join(sorted(optional)) or 'none'}"
        )
    return value


def _required_string(value: dict[str, object], key: str) -> str:
    result = value[key]
    if not isinstance(result, str):
        raise ValueError(f"{key} must be a string")
    return result


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, part in pairs:
        if key in value:
            raise ValueError(f"memory model output repeats field: {key}")
        value[key] = part
    return value


def _redact_secrets(value: str) -> str:
    return redact_secrets(value)
