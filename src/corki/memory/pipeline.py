"""Two-phase, Codex-aligned long-term memory generation pipeline."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path

from corki.config.settings import CorkiSettings
from corki.context.tokens import estimate_text_tokens
from corki.memory.artifacts import (
    baseline_matches,
    ensure_memory_layout,
    read_optional,
    read_skill_artifacts,
    stage_one_digest,
    sync_stage_one_artifacts,
    write_baseline,
    write_consolidated_artifacts,
)
from corki.memory.inputs import extraction_token_budget, truncate_rollout
from corki.memory.models import (
    ConsolidatedMemory,
    ConsolidationClaim,
    MemoryExtractionClaim,
    MemorySkill,
    StageOneMemory,
)
from corki.memory.repository import MemoryRepository
from corki.memory.sanitizer import redact_secrets
from corki.memory.transcript import render_transcript
from corki.models import ModelCompleted, ModelRequest
from corki.models.base import ModelPort
from corki.prompting import PromptStore
from corki.protocol.ids import ThreadId, new_turn_id
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
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._model = model
        self._root = root
        self._close_model = close_model
        self._prompts = prompt_store or PromptStore()
        self._task: asyncio.Task[MemoryRunReport] | None = None
        self._warnings: list[str] = []
        ensure_memory_layout(root)

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self._warnings)

    def start(self, current_thread_id: ThreadId) -> None:
        """Start at most one background pass for this runtime."""

        if (
            not self._settings.memories_enabled
            or not self._settings.memories_generate
            or self._task is not None
        ):
            return
        self._task = asyncio.create_task(
            self.run_once(current_thread_id), name="corki-long-term-memory"
        )
        self._task.add_done_callback(self._capture_background_failure)

    async def wait(self) -> MemoryRunReport | None:
        return await self._task if self._task is not None else None

    async def run_once(self, current_thread_id: ThreadId) -> MemoryRunReport:
        try:
            await self._repository.prune_stage_one_outputs(
                max_unused_days=self._settings.memories_max_unused_days,
                limit=_PRUNE_BATCH_SIZE,
            )
        except Exception as exc:  # noqa: BLE001 - maintenance failure must not stop generation
            self._warnings.append(f"memory retention pruning failed: {exc}")
        claims = await self._repository.claim_extraction_jobs(
            current_thread_id=current_thread_id,
            max_age_days=self._settings.memories_max_thread_age_days,
            min_idle_hours=self._settings.memories_min_thread_idle_hours,
            limit=self._settings.memories_max_threads_per_startup,
            lease_seconds=self._settings.memories_lease_seconds,
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
                except asyncio.CancelledError:
                    cleanup = asyncio.create_task(
                        self._record_extraction_failure(claim, "memory extraction cancelled")
                    )
                    await _join_owned((cleanup,))
                    raise
                except Exception as exc:  # noqa: BLE001 - isolated background job boundary
                    await self._record_extraction_failure(claim, f"{type(exc).__name__}: {exc}")
                    self._warnings.append(f"phase-one memory extraction failed: {exc}")
                    return "failed"

        jobs = tuple(asyncio.create_task(extract(claim)) for claim in claims)
        try:
            outcomes = await asyncio.gather(*jobs)
        finally:
            for job in jobs:
                if not job.done():
                    job.cancel()
            await _join_owned(jobs)
        report = MemoryRunReport(
            claimed=len(claims),
            extracted=outcomes.count("extracted"),
            empty=outcomes.count("empty"),
            failed=outcomes.count("failed"),
        )
        claim = await self._repository.claim_consolidation(
            lease_seconds=self._settings.memories_lease_seconds
        )
        if claim is None:
            return report
        try:
            skipped = await self._run_owned_consolidation(claim)
            return MemoryRunReport(
                claimed=report.claimed,
                extracted=report.extracted,
                empty=report.empty,
                failed=report.failed,
                consolidated=not skipped,
                consolidation_skipped=skipped,
            )
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(
                self._record_consolidation_failure(claim, "memory consolidation cancelled")
            )
            await _join_owned((cleanup,))
            raise
        except Exception as exc:  # noqa: BLE001 - isolate background consolidation failures
            await self._record_consolidation_failure(claim, f"{type(exc).__name__}: {exc}")
            self._warnings.append(f"phase-two memory consolidation failed: {exc}")
            return MemoryRunReport(
                claimed=report.claimed,
                extracted=report.extracted,
                empty=report.empty,
                failed=report.failed + 1,
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

    async def _run_owned_consolidation(self, claim: ConsolidationClaim) -> bool:
        work = asyncio.create_task(self._consolidation_work(claim), name="memory-consolidation")
        heartbeat = asyncio.create_task(self._heartbeat(claim), name="memory-heartbeat")
        try:
            done, _ = await asyncio.wait((work, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                # A failed heartbeat must stop sampling before we release the lease.
                heartbeat.result()
                raise RuntimeError("memory heartbeat unexpectedly stopped")
            selected, digest, consolidated = work.result()
        finally:
            for task in (work, heartbeat):
                if not task.done():
                    task.cancel()
            await _join_owned((work, heartbeat))

        # Stop heartbeats before the final transaction clears its token: a
        # heartbeat after a successful commit must not report false ownership
        # loss. Publication itself is fenced by the repository write lock.
        def publish() -> None:
            if consolidated is not None:
                write_consolidated_artifacts(self._root, consolidated)
                write_baseline(self._root, digest)

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
        self, claim: ConsolidationClaim
    ) -> tuple[tuple[StageOneMemory, ...], str, ConsolidatedMemory | None]:
        selected = await self._repository.load_consolidation_inputs(
            limit=self._settings.memories_max_raw_for_consolidation,
            max_unused_days=self._settings.memories_max_unused_days,
        )
        if not await self._repository.write_consolidation_workspace(
            claim, lambda: sync_stage_one_artifacts(self._root, selected)
        ):
            raise RuntimeError("memory consolidation ownership lost before workspace sync")
        digest = await asyncio.to_thread(stage_one_digest, self._root)
        skipped = await asyncio.to_thread(baseline_matches, self._root, digest)
        consolidated = None if skipped else await self._consolidate()

        return selected, digest, consolidated

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

    async def _consolidate(self) -> ConsolidatedMemory:
        content = _bounded_json_payload(
            {
                "previous_memory": read_optional(self._root / "MEMORY.md"),
                "previous_summary": read_optional(self._root / "memory_summary.md"),
                "previous_skills": json.dumps(read_skill_artifacts(self._root), ensure_ascii=False),
                "raw_memories": read_optional(self._root / "raw_memories.md"),
                "ad_hoc_notes": _read_memory_notes(self._root),
            },
            token_limit=max(1_024, self._settings.context_window_tokens * 7 // 10),
        )
        payload = await self._sample_json(
            model=self._settings.resolved_memory_consolidation_model,
            reasoning_effort="medium",
            instructions=self._prompts.render("memory/consolidation"),
            content=content,
            required={"memory", "memory_summary", "skills"},
            output_schema=_CONSOLIDATION_SCHEMA,
            output_schema_name="corki_memory_consolidation",
        )
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

    def _capture_background_failure(self, task: asyncio.Task[MemoryRunReport]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:  # noqa: BLE001 - never fail an interactive turn from background
            self._warnings.append(f"memory startup failed: {type(exc).__name__}: {exc}")

    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._close_model:
            await self._model.aclose()
        await self._repository.close()


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


def _truncate_head_tail(text: str, token_limit: int) -> str:
    if estimate_text_tokens(text) <= token_limit:
        return text
    marker = "\n\n… middle of historical transcript omitted …\n\n"
    budget = max(1, token_limit - estimate_text_tokens(marker))
    target = budget // 2

    def prefix(max_tokens: int, value: str) -> str:
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_text_tokens(value[:middle]) <= max_tokens:
                low = middle
            else:
                high = middle - 1
        return value[:low]

    head = prefix(target, text)
    tail = prefix(budget - estimate_text_tokens(head), text[::-1])[::-1]
    return head.rstrip() + marker + tail.lstrip()


def _bounded_json_payload(values: dict[str, str], *, token_limit: int) -> str:
    """Bound large consolidation inputs without ever producing malformed JSON."""

    def render(per_value_limit: int) -> str:
        return json.dumps(
            {key: _truncate_head_tail(value, per_value_limit) for key, value in values.items()},
            ensure_ascii=False,
        )

    complete = json.dumps(values, ensure_ascii=False)
    if estimate_text_tokens(complete) <= token_limit:
        return complete
    low, high = 1, token_limit
    best = render(1)
    while low <= high:
        middle = (low + high) // 2
        candidate = render(middle)
        if estimate_text_tokens(candidate) <= token_limit:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def _required_string(value: dict[str, object], key: str) -> str:
    result = value[key]
    if not isinstance(result, str):
        raise ValueError(f"{key} must be a string")
    return result


def _read_memory_notes(root: Path) -> str:
    notes_root = root / "extensions"
    entries: list[str] = []
    for path in sorted(notes_root.rglob("*.md")):
        if path.is_symlink() or any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            entries.append(
                f"## {path.relative_to(root).as_posix()}\n{path.read_text(encoding='utf-8')}"
            )
        except (OSError, UnicodeError):
            continue
    return "\n\n".join(entries)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, part in pairs:
        if key in value:
            raise ValueError(f"memory model output repeats field: {key}")
        value[key] = part
    return value


def _redact_secrets(value: str) -> str:
    return redact_secrets(value)
