"""Two-phase, Codex-aligned long-term memory generation pipeline."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from corki.config.settings import CorkiSettings
from corki.context.tokens import estimate_text_tokens
from corki.memory.artifacts import (
    baseline_digest,
    ensure_memory_layout,
    read_optional,
    stage_one_digest,
    sync_stage_one_artifacts,
    write_baseline,
    write_consolidated_artifacts,
)
from corki.memory.models import (
    ConsolidatedMemory,
    MemoryExtractionClaim,
    MemorySkill,
    StageOneMemory,
)
from corki.memory.repository import MemoryRepository
from corki.models import ModelCompleted, ModelRequest
from corki.models.base import ModelPort
from corki.prompting import PromptStore
from corki.protocol.ids import ThreadId, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ConversationItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
)

_EXTRACTION_CONCURRENCY = 8
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)\b"
        r"(\s*[:=]\s*)['\"]?[^\s'\",;]{6,}"
    ),
)

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
                    await asyncio.shield(
                        self._repository.fail_extraction(
                            claim,
                            "memory extraction cancelled",
                            retry_delay_seconds=self._settings.memories_retry_delay_seconds,
                        )
                    )
                    raise
                except Exception as exc:  # noqa: BLE001 - isolated background job boundary
                    await self._repository.fail_extraction(
                        claim,
                        f"{type(exc).__name__}: {exc}",
                        retry_delay_seconds=self._settings.memories_retry_delay_seconds,
                    )
                    self._warnings.append(f"phase-one memory extraction failed: {exc}")
                    return "failed"

        outcomes = await asyncio.gather(*(extract(claim) for claim in claims))
        report = MemoryRunReport(
            claimed=len(claims),
            extracted=outcomes.count("extracted"),
            empty=outcomes.count("empty"),
            failed=outcomes.count("failed"),
        )
        has_extension_inputs = any(
            path.is_file() and not path.is_symlink()
            for path in (self._root / "extensions").rglob("*.md")
        )
        await self._repository.enqueue_consolidation(force=has_extension_inputs)
        claim = await self._repository.claim_consolidation(
            lease_seconds=self._settings.memories_lease_seconds
        )
        if claim is None:
            return report
        try:
            selected = await self._repository.load_consolidation_inputs(
                limit=self._settings.memories_max_raw_for_consolidation,
                max_unused_days=self._settings.memories_max_unused_days,
            )
            await asyncio.to_thread(sync_stage_one_artifacts, self._root, selected)
            digest = await asyncio.to_thread(stage_one_digest, self._root)
            current = await asyncio.to_thread(baseline_digest, self._root)
            artifacts_exist = (self._root / "MEMORY.md").is_file() and (
                self._root / "memory_summary.md"
            ).is_file()
            skipped = current == digest and artifacts_exist
            if not skipped:
                consolidated = await self._consolidate()
                await asyncio.to_thread(write_consolidated_artifacts, self._root, consolidated)
                await asyncio.to_thread(write_baseline, self._root, digest)
            if not await self._repository.complete_consolidation(claim, selected):
                raise RuntimeError("memory consolidation lease was lost before commit")
            return MemoryRunReport(
                claimed=report.claimed,
                extracted=report.extracted,
                empty=report.empty,
                failed=report.failed,
                consolidated=not skipped,
                consolidation_skipped=skipped,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._repository.fail_consolidation(
                    claim,
                    "memory consolidation cancelled",
                    retry_delay_seconds=self._settings.memories_retry_delay_seconds,
                )
            )
            raise
        except Exception as exc:  # noqa: BLE001 - preserve old published artifacts on failure
            await self._repository.fail_consolidation(
                claim,
                f"{type(exc).__name__}: {exc}",
                retry_delay_seconds=self._settings.memories_retry_delay_seconds,
            )
            self._warnings.append(f"phase-two memory consolidation failed: {exc}")
            return MemoryRunReport(
                claimed=report.claimed,
                extracted=report.extracted,
                empty=report.empty,
                failed=report.failed + 1,
            )

    async def _extract(self, claim: MemoryExtractionClaim) -> StageOneMemory | None:
        transcript = _render_transcript(claim.items)
        transcript = _truncate_head_tail(transcript, self._settings.memories_extraction_token_limit)
        input_text = self._prompts.render(
            "memory/stage_one_input",
            thread_id=str(claim.thread_id),
            cwd=str(claim.cwd),
            transcript=transcript,
        )
        payload = await self._sample_json(
            model=self._settings.memories_extraction_model or self._settings.model,
            instructions=self._prompts.render("memory/stage_one_system"),
            content=input_text,
            required={"raw_memory", "rollout_summary", "rollout_slug"},
            output_schema=_STAGE_ONE_SCHEMA,
            output_schema_name="corki_memory_extraction",
        )
        raw = _redact_secrets(_required_string(payload, "raw_memory").strip())
        summary = _redact_secrets(_required_string(payload, "rollout_summary").strip())
        slug_value = payload["rollout_slug"]
        if slug_value is not None and not isinstance(slug_value, str):
            raise ValueError("rollout_slug must be a string or null")
        if not raw or not summary:
            if raw or summary:
                raise ValueError("raw_memory and rollout_summary must both be empty or non-empty")
            return None
        return StageOneMemory(
            thread_id=claim.thread_id,
            cwd=claim.cwd,
            source_updated_at=claim.source_updated_at,
            raw_memory=raw,
            rollout_summary=summary,
            rollout_slug=_redact_secrets(slug_value.strip()) if slug_value else None,
        )

    async def _consolidate(self) -> ConsolidatedMemory:
        content = _bounded_json_payload(
            {
                "previous_memory": read_optional(self._root / "MEMORY.md"),
                "previous_summary": read_optional(self._root / "memory_summary.md"),
                "raw_memories": read_optional(self._root / "raw_memories.md"),
                "ad_hoc_notes": _read_memory_notes(self._root),
            },
            token_limit=max(1_024, self._settings.context_window_tokens * 7 // 10),
        )
        payload = await self._sample_json(
            model=self._settings.memories_consolidation_model or self._settings.model,
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
        instructions: str,
        content: str,
        required: set[str],
        output_schema: dict[str, object],
        output_schema_name: str,
    ) -> dict[str, object]:
        turn_id = new_turn_id()
        request = ModelRequest(
            model=model,
            instructions=instructions,
            context_items=(),
            items=(UserMessageItem(content, turn_id),),
            tools=(),
            output_schema=output_schema,
            output_schema_name=output_schema_name,
        )
        completed: ModelCompleted | None = None
        async for event in self._model.stream(request):
            if isinstance(event, ModelCompleted):
                completed = event
        if completed is None:
            raise ValueError("memory model stream ended without completion")
        text = "\n".join(
            item.content for item in completed.items if isinstance(item, AssistantMessageItem)
        ).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("memory model returned invalid JSON") from exc
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError(
                f"memory model output must contain exactly: {', '.join(sorted(required))}"
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


def _render_transcript(items: tuple[ConversationItem, ...]) -> str:
    lines: list[str] = []
    for item in items:
        if isinstance(item, UserMessageItem):
            lines.append(f"USER: {item.content}")
        elif isinstance(item, AssistantMessageItem):
            lines.append(f"ASSISTANT: {item.content}")
        elif isinstance(item, ToolCallItem):
            arguments = item.call.raw_arguments or json.dumps(
                item.call.arguments, ensure_ascii=False
            )
            lines.append(f"TOOL_CALL {item.call.name}: {arguments}")
        elif isinstance(item, ToolResultItem):
            lines.append(f"TOOL_RESULT {item.tool_name}: {item.content}")
    return _redact_secrets("\n\n".join(lines))


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


def _redact_secrets(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            result = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result
