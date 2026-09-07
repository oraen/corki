"""Stable incremental context and automatic model-generated compaction."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, replace
from typing import TypeVar
from uuid import uuid4

from corki.config.token_budget import TokenBudgetConfig
from corki.context.builder import ContextSnapshot
from corki.context.function_output import project_function_items
from corki.context.history import active_history
from corki.context.hosted_output import project_hosted_items
from corki.context.input_context import bind_input_context
from corki.context.token_budget import BudgetStatus, window_identities, with_window_context
from corki.context.tokens import (
    estimate_item_tokens,
    estimate_request_tokens,
    estimate_text_tokens,
)
from corki.context.usage import BodyPrefixWindow, context_tokens_from_usage
from corki.context.world_state import changed_context_items, snapshot_content
from corki.models.backoff import backoff, retry_limit
from corki.models.base import ModelError, ModelErrorKind, ModelPort
from corki.models.types import ModelCompleted, ModelRequest, ModelRetrying
from corki.prompting import PromptStore
from corki.protocol.context import ContextLimits
from corki.protocol.ids import ItemId, ThreadId, TurnId
from corki.protocol.items import (
    AssistantMessageItem,
    BudgetNoticeItem,
    CompactionItem,
    ContextItem,
    ConversationItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
)
from corki.protocol.tools import TextContent, ToolSpec
from corki.protocol.truncation import TruncationPolicy
from corki.sessions.repository import SessionRepository

_LOG = logging.getLogger(__name__)
CompactionRetryCallback = Callable[[ModelRetrying], Awaitable[None]]
_ItemT = TypeVar("_ItemT", bound=ConversationItem)


@dataclass(frozen=True, slots=True)
class PreparedHistory:
    items: tuple[ConversationItem, ...]
    estimated_tokens: int
    compacted: bool = False
    warnings: tuple[str, ...] = ()


class ContextWindowManager:
    """Prepare one coherent model window while preserving append-only storage."""

    _RETAINED_USER_TOKEN_BUDGET = 20_000

    def __init__(
        self,
        *,
        repository: SessionRepository,
        model: ModelPort,
        model_name: str,
        context_window_tokens: int,
        auto_compact_tokens: int | None = None,
        auto_compact_token_limit_scope: str = "total",
        token_budget_enabled: bool = False,
        token_budget: TokenBudgetConfig | None = None,
        effective_context_window_percent: int = 95,
        prompt_store: PromptStore | None = None,
        media_preparation=None,
        max_retries: int = 5,
        retry_base_seconds: float = 0.2,
        compact_prompt: str | None = None,
        history_notes=None,
        truncation_policy: TruncationPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._truncation_policy = truncation_policy or TruncationPolicy()
        self._history_notes = history_notes
        self.token_budget_enabled = token_budget_enabled
        self._token_budget = token_budget or TokenBudgetConfig()
        self._fallback_buffer = (
            self._token_budget.fallback_buffer_tokens if token_budget_enabled else 0
        )
        self._last_snapshot: ContextSnapshot | None = None
        self._last_tools: tuple[ToolSpec, ...] = ()
        self._model = model
        self._model_name = model_name
        limits = ContextLimits(
            context_window_tokens, auto_compact_tokens, effective_context_window_percent
        )
        self._context_window_tokens = limits.usable_tokens
        self._auto_compact_tokens = limits.trigger_tokens
        if auto_compact_token_limit_scope not in ("total", "body_after_prefix"):
            raise ValueError("auto_compact_token_limit_scope must be total or body_after_prefix")
        self._body_prefix = (
            BodyPrefixWindow() if auto_compact_token_limit_scope == "body_after_prefix" else None
        )
        if self._body_prefix is not None:
            self._auto_compact_tokens = (
                auto_compact_tokens
                if auto_compact_tokens is not None
                else limits.auto_compact_limit
            )
        self._prompt_store = prompt_store or PromptStore()
        self._compact_prompt = (compact_prompt or "").strip() or None
        self._media = media_preparation
        self._max_retries = retry_limit(max_retries)
        if not math.isfinite(retry_base_seconds) or retry_base_seconds < 0:
            raise ValueError("compaction retry base must be finite and non-negative")
        self._retry_base_seconds = retry_base_seconds

    def model_history(self, items: tuple[ConversationItem, ...]) -> tuple[ConversationItem, ...]:
        """Build bounded model copies without rewriting the durable history."""
        return project_function_items(
            project_hosted_items(items, self._truncation_policy), self._truncation_policy
        )

    def _history(self, items):
        return self.model_history(active_history(items))

    async def prepare(
        self,
        *,
        thread_id: ThreadId,
        turn_id: TurnId,
        snapshot: ContextSnapshot,
        tools: tuple[ToolSpec, ...],
        pending_items: tuple[ConversationItem, ...] = (),
        tool_resolver: Callable[[tuple[ConversationItem, ...]], tuple[ToolSpec, ...]] | None = None,
        on_retry: CompactionRetryCallback | None = None,
    ) -> PreparedHistory:
        all_items = await self._repository.load_items(thread_id)
        if self._history_notes is not None:
            snapshot = await self._history_notes.decorate(snapshot, all_items, turn_id)
        if self.token_budget_enabled:
            snapshot = with_window_context(
                snapshot, all_items, thread_id, turn_id, self._token_budget.guidance_message
            )
        if self._media is not None:
            all_items = await self._media.prepare_items(all_items)
            pending_items = await self._media.prepare_items(pending_items)
        changed_context = changed_context_items(all_items, snapshot.items, turn_id)
        stored_ids = {item.id for item in all_items}
        new_pending = tuple(item for item in pending_items if item.id not in stored_ids)
        historical = self._history(all_items)
        input_context = bind_input_context(
            all_items, historical, snapshot.input_items, pending_items
        )
        new_input_context = tuple(item for item in input_context if item.id not in stored_ids)
        candidate = self._history((*all_items, *changed_context, *new_pending, *new_input_context))
        if tool_resolver is not None:
            tools = tool_resolver(candidate)
        self._last_snapshot, self._last_tools = snapshot, tools
        estimated = estimate_request_tokens(snapshot.instructions, candidate, tools)
        usage = await self._repository.load_context_usage(thread_id)
        usage_tokens = context_tokens_from_usage(usage, all_items, candidate)
        active_tokens = estimated if usage_tokens is None else usage_tokens
        scope_tokens = active_tokens
        if self._body_prefix is not None:
            window_id = next(
                (str(item.id) for item in reversed(all_items) if isinstance(item, CompactionItem)),
                None,
            )
            active_tokens, scope_tokens = self._body_prefix.measure(
                window_id=window_id,
                estimated_prefill=estimate_request_tokens(snapshot.instructions, historical, tools)
                if all_items
                else estimated,
                local_tokens=estimated,
                active_tokens=active_tokens,
                usage=usage,
            )
        compacted = False
        if (
            self.token_budget_enabled
            and all_items
            and (
                scope_tokens >= self._auto_compact_tokens + self._fallback_buffer
                or active_tokens >= self._context_window_tokens
                or any(
                    isinstance(item, ToolResultItem)
                    and not item.is_error
                    and item.state_update.new_context_requested
                    for item in historical
                )
            )
        ):
            return await self._reset_context(
                thread_id,
                turn_id,
                all_items,
                snapshot,
                pending=(*pending_items, *input_context) if pending_items else (),
                tool_resolver=tool_resolver,
            )
        # Compact only history that existed before this model step. In
        # particular, the current user input must remain verbatim after the
        # checkpoint; asking the summarizer to rewrite it can change intent.
        protected_pending = tuple(
            item
            for item in historical
            if isinstance(item, UserMessageItem)
            and any(_same_user_input(item, pending) for pending in pending_items)
        )
        protected_ids = {item.id for item in protected_pending}
        protected_ids.update(item.id for item in pending_items)
        protected_ids.update(item.id for item in input_context)
        # Between model steps there is no pending_input_items entry, but the
        # latest user input in this turn still must not be summarized/truncated.
        current_input = next(
            (
                item
                for item in reversed(historical)
                if isinstance(item, UserMessageItem) and item.turn_id == turn_id
            ),
            None,
        )
        protected_current = (
            (current_input,) if current_input is not None and not pending_items else ()
        )
        protected_ids.update(item.id for item in protected_current)
        compactable = tuple(item for item in historical if item.id not in protected_ids)
        if (
            not self.token_budget_enabled
            and (
                scope_tokens >= self._auto_compact_tokens
                or active_tokens >= self._context_window_tokens
            )
            and any(not isinstance(item, ContextItem) for item in compactable)
            and (
                bool(new_pending)
                or bool(changed_context)
                or _has_post_compaction_activity(all_items)
            )
        ):
            summary = await self._summarize(
                compactable, instructions=snapshot.instructions, turn_id=turn_id, on_retry=on_retry
            )
            # The marker supersedes every previously persisted item, including
            # any pending item copied back below. Keep its audit pointer real
            # even when active-history normalization synthesized an item.
            checkpoint = CompactionItem(summary, all_items[-1].id, turn_id)
            reinjected = tuple(
                replace(
                    item,
                    id=ItemId(str(uuid4())),
                    turn_id=turn_id,
                    content=snapshot_content(item),
                    snapshot_content=None,
                )
                for item in snapshot.items
            )
            # A normal run has not persisted ``new_pending`` yet. The fallback
            # copy handles recovery from an older checkpoint whose user item
            # was already stored before context preparation.
            pending_after_checkpoint = tuple(
                item if item.id not in stored_ids else _retained_copy(item)
                for item in (*pending_items, *input_context, *protected_current)
            )
            fixed_items = self._history((checkpoint, *reinjected, *pending_after_checkpoint))
            fixed_tools = tool_resolver(fixed_items) if tool_resolver is not None else tools
            fixed_tokens = estimate_request_tokens(snapshot.instructions, fixed_items, fixed_tools)
            retained_budget = min(
                self._RETAINED_USER_TOKEN_BUDGET,
                max(0, self._context_window_tokens - fixed_tokens - 1),
            )
            retained_users = _retained_user_messages(
                historical,
                excluded_ids=protected_ids,
                token_budget=retained_budget,
            )
            if pending_items:
                # Pre-turn compaction: retained prior user messages and the
                # summary form the old history, then fresh context/current input.
                replacement = (*retained_users, *reinjected, *pending_after_checkpoint)
                summary_index = len(retained_users)
            elif protected_current:
                replacement = (*retained_users, *reinjected, *pending_after_checkpoint)
                summary_index = len(replacement)
            elif retained_users:
                # Mid-turn compaction: Codex re-injects current world state just
                # before the last real user message and keeps the summary last.
                replacement = (*retained_users[:-1], *reinjected, retained_users[-1])
                summary_index = len(replacement)
            else:
                replacement = reinjected
                summary_index = len(replacement)
            checkpoint = replace(
                checkpoint,
                replacement_item_count=len(replacement),
                summary_insert_index=summary_index,
            )
            appended: tuple[ConversationItem, ...] = (checkpoint, *replacement)
            compacted_active = self._history((*all_items, *appended))
            if tool_resolver is not None:
                tools = tool_resolver(compacted_active)
            compacted_estimate = estimate_request_tokens(
                snapshot.instructions, compacted_active, tools
            )
            if compacted_estimate >= self._context_window_tokens:
                raise ModelError(
                    "model-generated compaction is still too large "
                    f"({compacted_estimate}/{self._context_window_tokens} estimated tokens)",
                    kind=ModelErrorKind.CONTEXT_WINDOW,
                )
            await self._repository.append_items(thread_id, appended)
            active = compacted_active
            estimated = compacted_estimate
            compacted = True
            if self._body_prefix is not None:
                self._body_prefix.reset(str(checkpoint.id), compacted_estimate, usage)
        else:
            appended = (*changed_context, *new_pending, *new_input_context)
            if appended:
                # Match Codex's turn-start ordering: world-state/context changes
                # are recorded before the user input they qualify. One append
                # also makes that order atomic for crash recovery.
                await self._repository.append_items(thread_id, appended)
            active = candidate

        checked_tokens = estimated if compacted else max(estimated, active_tokens)
        if checked_tokens >= self._context_window_tokens:
            raise ModelError(
                f"prepared context exceeds model window ({checked_tokens}/"
                f"{self._context_window_tokens} estimated tokens)",
                kind=ModelErrorKind.CONTEXT_WINDOW,
            )
        rendered_keys = {item.key for item in changed_context}
        warnings = tuple(
            message
            for key, message in snapshot.section_warnings
            if compacted or key in rendered_keys
        )
        return PreparedHistory(active, estimated, compacted, warnings)

    async def compact(
        self,
        *,
        thread_id: ThreadId,
        turn_id: TurnId,
        instructions: str,
        snapshot: ContextSnapshot | None = None,
        on_retry: CompactionRetryCallback | None = None,
    ) -> PreparedHistory:
        """Standalone local compaction; the next normal turn rebuilds world state."""
        stored = await self._repository.load_items(thread_id)
        if any(isinstance(i, CompactionItem) and i.turn_id == turn_id for i in stored):
            # Atomic history commit may precede the node checkpoint. Never ask
            # the model to summarize a second time after an installed result.
            active = self._history(stored)
            return PreparedHistory(active, estimate_request_tokens(instructions, active, ()), True)
        historical = self._history(stored)
        if self.token_budget_enabled:
            if snapshot is None:
                raise ValueError("token-budget manual compaction requires current world state")
            return await self._reset_context(thread_id, turn_id, stored, snapshot)
        if self._media is not None:
            historical = await self._media.prepare_items(historical)
        summary = await self._summarize(
            historical, instructions=instructions, turn_id=turn_id, on_retry=on_retry
        )
        retained = _retained_user_messages(
            historical, excluded_ids=set(), token_budget=self._RETAINED_USER_TOKEN_BUDGET
        )
        marker = CompactionItem(
            summary,
            stored[-1].id if stored else None,
            turn_id,
            replacement_item_count=len(retained),
            summary_insert_index=len(retained),
        )
        appended = (marker, *retained)
        await self._repository.append_items(thread_id, appended)
        active = self._history((*stored, *appended))
        if self._body_prefix is not None:
            self._body_prefix.reset(
                str(marker.id),
                estimate_request_tokens(instructions, active, ()),
                await self._repository.load_context_usage(thread_id),
            )
        return PreparedHistory(active, estimate_request_tokens(instructions, active, ()), True)

    async def _reset_context(
        self, thread_id, turn_id, stored, snapshot, *, pending=(), tool_resolver=None
    ):
        marker = CompactionItem("", stored[-1].id if stored else None, turn_id, context_reset=True)
        if self._history_notes is not None:
            snapshot = await self._history_notes.decorate(snapshot, (*stored, marker), turn_id)
        snapshot = with_window_context(
            snapshot, (*stored, marker), thread_id, turn_id, self._token_budget.guidance_message
        )
        replacement = tuple(
            replace(item, id=ItemId(str(uuid4())), turn_id=turn_id) for item in snapshot.items
        )
        marker = replace(marker, replacement_item_count=len(replacement) + len(pending))
        appended = (marker, *replacement, *pending)
        active = self._history((*stored, *appended))
        if tool_resolver is not None:
            self._last_tools = tool_resolver(active)
        estimated = estimate_request_tokens(snapshot.instructions, active, self._last_tools)
        if estimated >= self._context_window_tokens:
            raise ModelError(
                "fresh context window exceeds the usable model window",
                kind=ModelErrorKind.CONTEXT_WINDOW,
            )
        await self._repository.append_items(thread_id, appended)
        self._last_snapshot = snapshot
        if self._body_prefix is not None:
            self._body_prefix.reset(
                str(marker.id), estimated, await self._repository.load_context_usage(thread_id)
            )
        return PreparedHistory(active, estimated, True)

    async def client_metadata(self, thread_id: ThreadId, turn_id: TurnId):
        """Resolve native ingestion identity from committed window history."""
        if self._history_notes is None:
            return None
        return self._history_notes.metadata(await self._repository.load_items(thread_id), turn_id)

    async def remaining_tokens(self, thread_id: ThreadId) -> int | None:
        result = await self._budget_status(thread_id)
        return result[0].remaining if result is not None else None

    async def _budget_status(self, thread_id):
        snapshot = self._last_snapshot
        if snapshot is None:
            return None
        stored = await self._repository.load_items(thread_id)
        recorded = {item.id for item in stored}
        active = tuple(item for item in self._history(stored) if item.id in recorded)
        local = estimate_request_tokens(snapshot.instructions, active, self._last_tools)
        usage = await self._repository.load_context_usage(thread_id)
        measured = context_tokens_from_usage(usage, stored, active)
        total = local if measured is None else measured
        scope = total
        if self._body_prefix is not None:
            key = next(
                (str(item.id) for item in reversed(stored) if isinstance(item, CompactionItem)),
                None,
            )
            total, scope = self._body_prefix.measure(
                window_id=key,
                estimated_prefill=local,
                local_tokens=local,
                active_tokens=total,
                usage=usage,
            )
        return BudgetStatus(
            max(0, min(self._auto_compact_tokens - scope, self._context_window_tokens - total)),
            scope >= self._auto_compact_tokens + self._fallback_buffer
            or total >= self._context_window_tokens,
        ), stored

    async def record_budget_notices(self, thread_id, turn_id, *, needs_follow_up: bool):
        config = self._token_budget
        if not self.token_budget_enabled or (
            config.reminder_threshold_tokens is None and config.auto_compact_fallback_prompt is None
        ):
            return
        try:
            result = await self._budget_status(thread_id)
            if result is None:
                return
            status, stored = result
            window_id = window_identities(stored, thread_id)[-1]
            requested = any(
                isinstance(item, ToolResultItem)
                and not item.is_error
                and item.state_update.new_context_requested
                for item in self._history(stored)
            )
            rollover = needs_follow_up and (requested or status.limit_reached)
            messages = []
            if (
                config.reminder_threshold_tokens is not None
                and status.remaining <= config.reminder_threshold_tokens
            ):
                messages.append(
                    (
                        "reminder",
                        config.reminder_message_template.replace(
                            "{n_remaining}", str(status.remaining)
                        ),
                    )
                )
            if (
                not rollover
                and not status.limit_reached
                and status.remaining == 0
                and config.auto_compact_fallback_prompt is not None
            ):
                messages.append(("fallback", config.auto_compact_fallback_prompt))
            existing = {item.id for item in stored}
            items = tuple(
                BudgetNoticeItem(
                    content, turn_id, window_id, kind, id=ItemId(f"budget:{window_id}:{kind}")
                )
                for kind, content in messages
                if ItemId(f"budget:{window_id}:{kind}") not in existing
            )
            if items:
                await self._repository.append_items(thread_id, items)
        except Exception:
            _LOG.exception("Could not persist token-budget guidance")

    async def _summarize(
        self,
        items: tuple[ConversationItem, ...],
        *,
        instructions: str,
        turn_id: TurnId,
        on_retry: CompactionRetryCallback | None = None,
    ) -> str:
        # Local Codex compaction retains base instructions and appends a user
        # compaction request. It does not recursively summarize isolated chunks.
        prompt = UserMessageItem(
            self._compact_prompt or self._prompt_store.render("tasks/compact"), turn_id
        )
        history = items
        retries = 0
        while True:
            request_items = (*history, prompt)
            estimated = estimate_request_tokens(instructions, request_items, ())
            if estimated >= self._context_window_tokens:
                if not history:
                    raise ModelError(
                        "base instructions and compaction request exceed usable context window "
                        f"({estimated}/{self._context_window_tokens} estimated tokens)",
                        kind=ModelErrorKind.CONTEXT_WINDOW,
                    )
                history = _drop_oldest_pair(history)
                retries = 0
                continue
            request = ModelRequest(
                model=self._model_name,
                instructions=instructions,
                context_items=(),
                items=await self._media.prepare_items(request_items, for_model=True)
                if self._media is not None
                else request_items,
                tools=(),
                harness_managed_retries=True,
            )
            try:
                return await self._summary_attempt(request)
            except ModelError as exc:
                if exc.kind is ModelErrorKind.CONTEXT_WINDOW:
                    if not history:
                        raise
                    history = _drop_oldest_pair(history)
                    retries = 0
                    continue
                # compact.rs has a separate retry loop, not the main sampling
                # is_retryable predicate or its server-specified retry delay.
                if retries >= self._max_retries:
                    raise
                delay = backoff(self._retry_base_seconds, retries)
                retries += 1
                notice = ModelRetrying(retries, self._max_retries, delay, str(exc)[:4000])
                if on_retry is not None:
                    await on_retry(notice)
                else:
                    _LOG.warning(
                        "Compaction retry %s/%s: %s", retries, self._max_retries, notice.error
                    )
                await asyncio.sleep(delay)

    async def _summary_attempt(self, request: ModelRequest) -> str:
        completed: ModelCompleted | None = None
        async with aclosing(self._model.stream(request)) as stream:
            async for event in stream:
                if isinstance(event, ModelCompleted):
                    completed = event
                    break
        if completed is None:
            raise ModelError("compaction stream ended without completion")
        summary = next(
            (
                item.content.strip()
                for item in reversed(completed.items)
                if isinstance(item, AssistantMessageItem) and item.content.strip()
            ),
            "",
        )
        if not summary:
            raise ModelError("compaction model returned no summary")
        return summary


def _same_user_input(item: UserMessageItem, pending: ConversationItem) -> bool:
    """Recognize a pending input copied by a pre-checkpoint recovery attempt."""

    return (
        isinstance(pending, UserMessageItem)
        and item.turn_id == pending.turn_id
        and item.content == pending.content
        and item.attachments == pending.attachments
        and item.content_items == pending.content_items
    )


def _has_post_compaction_activity(items: tuple[ConversationItem, ...]) -> bool:
    """Avoid repeatedly compacting an unchanged replacement window."""

    marker_index: int | None = None
    marker: CompactionItem | None = None
    for index, item in enumerate(items):
        if isinstance(item, CompactionItem):
            marker_index, marker = index, item
    if marker_index is None or marker is None:
        return True
    replacement_end = marker_index + 1 + marker.replacement_item_count
    return replacement_end < len(items)


def _retained_user_messages(
    history: tuple[ConversationItem, ...],
    *,
    excluded_ids: set[ItemId],
    token_budget: int,
) -> tuple[UserMessageItem, ...]:
    """Retain newest real user requests under the same 20k policy as Codex."""

    selected: list[UserMessageItem] = []
    remaining = token_budget
    for item in reversed(history):
        if not isinstance(item, UserMessageItem) or item.id in excluded_ids:
            continue
        # Codex rebuilds past user messages from text only. Never let an old
        # authoritative content_items array override the retained text budget.
        text = (
            "\n".join(p.text for p in item.content_items if isinstance(p, TextContent) and p.text)
            if item.content_items
            else item.content
        )
        item = replace(item, content=text, content_items=(), attachments=())
        cost = estimate_item_tokens(item)
        if cost <= remaining:
            selected.append(_retained_copy(item))
            remaining -= cost
            continue
        text_budget = max(0, remaining - 12)
        if text_budget:
            content = _truncate_to_estimated_tokens(item.content, text_budget)
            selected.append(_retained_copy(replace(item, content=content)))
        break
    selected.reverse()
    return tuple(selected)


def _retained_copy(item: _ItemT) -> _ItemT:
    """Copy a persisted item without inventing a fresh user contribution."""
    if isinstance(item, UserMessageItem):
        return replace(
            item, id=ItemId(str(uuid4())), retained_from_id=item.retained_from_id or item.id
        )
    return replace(item, id=ItemId(str(uuid4())))


def _truncate_to_estimated_tokens(text: str, budget: int) -> str:
    """Return the longest UTF-8-safe prefix within a conservative token budget."""

    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_text_tokens(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _drop_oldest_pair(items: tuple[ConversationItem, ...]) -> tuple[ConversationItem, ...]:
    """Trim only the request view, removing a call's paired output as a unit."""
    first, *rest = items
    if isinstance(first, ToolCallItem):
        retained = tuple(
            item
            for item in rest
            if not (isinstance(item, ToolResultItem) and item.call_id == first.call.id)
        )
    elif isinstance(first, ToolResultItem):
        retained = tuple(
            item
            for item in rest
            if not (isinstance(item, ToolCallItem) and item.call.id == first.call_id)
        )
    else:
        retained = tuple(rest)
    _LOG.warning(
        "Compaction input exceeded its window; omitted %s oldest item(s) from the summary request. "
        "Raw history is preserved, but the summary cannot include omitted content.",
        len(items) - len(retained),
    )
    return retained
