"""Stable incremental context and automatic model-generated compaction."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from copy import copy
from dataclasses import dataclass, replace
from uuid import uuid4

from corki.config.settings import CorkiSettings
from corki.config.token_budget import TokenBudgetConfig
from corki.context import model_transition
from corki.context.builder import ContextSnapshot
from corki.context.function_output import project_function_items
from corki.context.history import active_history
from corki.context.hosted_output import project_hosted_items
from corki.context.input_context import bind_input_context
from corki.context.local_retention import retained_copy as _retained_copy
from corki.context.local_retention import retained_user_messages as _retained_user_messages
from corki.context.message_groups import freeze_context_messages, needs_initial_context
from corki.context.token_budget import BudgetStatus, window_identities, with_window_context
from corki.context.tokens import estimate_request_tokens
from corki.context.usage import BodyPrefixWindow, context_tokens_from_usage
from corki.context.world_state import changed_context_items, snapshot_content
from corki.models.backoff import backoff, retry_limit
from corki.models.base import ModelError, ModelErrorKind, ModelPort
from corki.models.types import ModelCompleted, ModelRequest, ModelRetrying
from corki.prompting import PromptStore
from corki.protocol.context import ContextLimits, ModelContextInfo
from corki.protocol.context_messages import context_message_groups
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
from corki.protocol.tools import ToolSpec
from corki.protocol.truncation import TruncationPolicy
from corki.sessions.repository import SessionRepository

_LOG = logging.getLogger(__name__)
CompactionRetryCallback = Callable[[ModelRetrying], Awaitable[None]]
HistoryProjector = Callable[[tuple[ConversationItem, ...]], tuple[ConversationItem, ...]]


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
        remote_compaction: bool = False,
        remote_compaction_v2: bool = True,
        remote_request_options: dict | None = None,
        remote_identity=None,
        model_context_lookup=None,
    ) -> None:
        self._repository = repository
        self._truncation_policy = truncation_policy or TruncationPolicy()
        self._history_notes = history_notes
        self._remote_request_options = dict(remote_request_options or {})
        self._model_context_lookup = model_context_lookup
        self._retained_turn_model: ModelContextInfo | None = None
        self._local_compaction_owner: ContextWindowManager | None = None
        self.token_budget_enabled = token_budget_enabled
        self._token_budget = token_budget or TokenBudgetConfig()
        self._fallback_buffer = (
            self._token_budget.fallback_buffer_tokens if token_budget_enabled else 0
        )
        self._last_snapshot: ContextSnapshot | None = None
        self._last_tools: tuple[ToolSpec, ...] = ()
        self._history_projector: HistoryProjector | None = None
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

    def with_model_settings(self, settings: CorkiSettings) -> ContextWindowManager:
        """Create a Turn-owned policy view, retaining the thread's body-prefix state."""
        window = copy(self)
        info = settings.model_context_info(settings.model)
        limits = settings.main_context_limits
        window._model_name = settings.model
        window._model_context_lookup = settings.model_context_info
        window._truncation_policy = info.truncation_policy
        window._context_window_tokens = limits.usable_tokens
        window._auto_compact_tokens = limits.trigger_tokens
        if window._body_prefix is not None:
            window._auto_compact_tokens = (
                settings.auto_compact_tokens
                if settings.auto_compact_tokens is not None
                else limits.auto_compact_limit
            )
        window._remote_request_options = {
            **self._remote_request_options,
            "model_info": info,
            "reasoning_effort": settings.reasoning_effort,
            "reasoning_summary": settings.reasoning_summary,
            "service_tier": settings.session_service_tier,
        }
        window._last_snapshot = None
        window._last_tools = ()
        window._history_projector = None
        window._retained_turn_model = None
        window._local_compaction_owner = None
        return window

    def retain_turn_metadata(
        self, info: ModelContextInfo, *, local_compaction_owner: ContextWindowManager
    ) -> None:
        """Retain native legacy consumers while Step request/reset budgets change."""
        self._retained_turn_model = info
        self._truncation_policy = info.truncation_policy
        self._local_compaction_owner = local_compaction_owner

    def model_history(self, items: tuple[ConversationItem, ...]) -> tuple[ConversationItem, ...]:
        """Build bounded model copies without rewriting the durable history."""
        if self._history_projector is not None:
            items = self._history_projector(items)
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
        on_compact=None,
        tool_inventory_for_model=None,
        tool_resolver_for_model=None,
        history_projector: HistoryProjector | None = None,
        legacy_base_unknown: bool = False,
    ) -> PreparedHistory:
        # The Step owns this projection just like its tool resolver. Budgeting,
        # summaries and ordinary requests must all see the same valid definitions.
        self._history_projector = history_projector
        all_items = await self._repository.load_items(thread_id)
        if on_compact is not None:
            for marker in all_items:
                if isinstance(marker, CompactionItem) and marker.turn_id == turn_id:
                    await on_compact(
                        "PostCompact",
                        str(marker.id),
                        self._summary_model_name,
                        "auto",
                        stage="recover",
                    )
            all_items = await self._repository.load_items(thread_id)
        transitioned = False
        if self._model_context_lookup is not None:
            info = self._model_context_lookup(self._model_name)
            snapshot = replace(
                snapshot,
                items=(
                    *(i for i in snapshot.items if i.key != model_transition.KEY),
                    model_transition.model_snapshot(self._retained_turn_model or info, turn_id),
                ),
            )
            if pending_items:
                historical = self._history(all_items)
                usage = await self._repository.load_context_usage(thread_id)
                measured = await self._context_tokens_from_usage(usage, all_items, historical)
                active_tokens = (
                    measured
                    if measured is not None
                    else await self._estimate_request_tokens(
                        snapshot.instructions, historical, tools
                    )
                )
                target = model_transition.transition_target(
                    all_items,
                    info,
                    self._model_context_lookup,
                    active_tokens=active_tokens,
                    usable_tokens=self._context_window_tokens,
                    auto_limit=self._auto_compact_tokens,
                    body_scope=self._body_prefix is not None,
                )
                if target is not None:
                    old_info, reason = target
                    old = copy(self)
                    # A temporary previous-model Turn is its own local compact
                    # owner; it must not fall back to this Turn's initial model.
                    old._local_compaction_owner = None
                    old._model_name = old_info.model
                    old._context_window_tokens = (
                        old_info.resolved_context_window
                        * old_info.effective_context_window_percent
                        // 100
                        if old_info.resolved_context_window is not None
                        else self._context_window_tokens
                    )
                    old._truncation_policy = old_info.truncation_policy
                    old._remote_request_options = {
                        **self._remote_request_options,
                        "reasoning_effort": old_info.reasoning_effort_for_request(
                            old_info.reasoning_effort_for_model_switch(
                                self._remote_request_options.get("reasoning_effort")
                            )
                        ),
                        "reasoning_effort_resolved": True,
                        "model_info": old_info,
                    }
                    old._body_prefix = None
                    await old.compact(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        instructions=snapshot.instructions,
                        snapshot=snapshot,
                        tools=tool_resolver_for_model(old_info, historical)
                        if tool_resolver_for_model is not None
                        else tools,
                        on_retry=on_retry,
                        on_compact=on_compact,
                        legacy_base_unknown=legacy_base_unknown,
                        phase="pre_turn",
                        reason=reason,
                    )
                    all_items = await self._repository.load_items(thread_id)
                    transitioned = True
        snapshot = await snapshot.for_window(initial=needs_initial_context(all_items))
        if self._history_notes is not None:
            snapshot = await self._history_notes.decorate(snapshot, all_items, turn_id)
        if self.token_budget_enabled:
            snapshot = with_window_context(
                snapshot, all_items, thread_id, turn_id, self._token_budget.guidance_message
            )
        if self._media is not None:
            all_items = await self._media.prepare_items(all_items)
            pending_items = await self._media.prepare_items(pending_items)
        changed_context = freeze_context_messages(
            changed_context_items(
                all_items,
                snapshot.items,
                turn_id,
                omitted_sections=snapshot.omitted_sections,
                world_state_sections=snapshot.world_state_sections,
            ),
            initial=needs_initial_context(all_items),
            initial_extension_keys=snapshot.initial_extension_keys,
        )
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
        estimated = await self._estimate_request_tokens(snapshot.instructions, candidate, tools)
        usage = await self._repository.load_context_usage(thread_id)
        usage_tokens = await self._context_tokens_from_usage(usage, all_items, candidate)
        active_tokens = estimated if usage_tokens is None else usage_tokens
        scope_tokens = active_tokens
        if self._body_prefix is not None:
            window_id = next(
                (str(item.id) for item in reversed(all_items) if isinstance(item, CompactionItem)),
                None,
            )
            active_tokens, scope_tokens = self._body_prefix.measure(
                window_id=window_id,
                estimated_prefill=await self._estimate_request_tokens(
                    snapshot.instructions, historical, tools
                )
                if all_items
                else estimated,
                local_tokens=estimated,
                active_tokens=active_tokens,
                usage=usage,
            )
        compacted = transitioned
        # Incoming pre-turn input is not yet accepted into the history being
        # summarized. An already accepted mid-turn user is part of that history,
        # independently of retaining its verbatim copy in the replacement.
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
            (
                scope_tokens >= self._auto_compact_tokens + self._fallback_buffer
                or active_tokens >= self._context_window_tokens
                # The final admission check also enforces the local estimate.
                # Give compactable history the same recovery opportunity when
                # provider usage is lower than that independently enforced cap.
                or estimated >= self._context_window_tokens
                or (
                    self.token_budget_enabled
                    and any(
                        isinstance(item, ToolResultItem)
                        and not item.is_error
                        and item.state_update.new_context_requested
                        for item in historical
                    )
                )
            )
            and any(not isinstance(item, ContextItem) for item in compactable)
            and (
                bool(new_pending)
                or bool(changed_context)
                or _has_post_compaction_activity(all_items)
            )
        ):
            if legacy_base_unknown:
                raise ValueError("missing admitted Turn base instructions")
            if on_compact is not None:
                await on_compact(
                    "PreCompact",
                    str(all_items[-1].id) if all_items else "empty",
                    self._summary_model_name,
                    "auto",
                )
            summary = await self._summarize(
                compactable if pending_items else historical,
                instructions=snapshot.instructions,
                turn_id=turn_id,
                on_retry=on_retry,
            )
            checkpoint = CompactionItem(summary, all_items[-1].id, turn_id)
            snapshot = await snapshot.for_window(initial=True)
            if self.token_budget_enabled:
                # The replacement belongs to this marker, not to the window
                # captured before summarization. Install both in one append.
                snapshot = with_window_context(
                    snapshot,
                    (*all_items, checkpoint),
                    thread_id,
                    turn_id,
                    self._token_budget.guidance_message,
                )
            # The marker supersedes every previously persisted item, including
            # any pending item copied back below. Keep its audit pointer real
            # even when active-history normalization synthesized an item.
            # Model and personality instructions are diffs, not unconditional
            # catalog fragments. Preserve their rendered/snapshot pairs so
            # projection cannot lose the previous model boundary.
            instruction_updates = {
                item.key: item
                for item in changed_context_items(
                    (*all_items, checkpoint),
                    snapshot.items,
                    turn_id,
                    omitted_sections=snapshot.omitted_sections,
                    world_state_sections=snapshot.world_state_sections,
                )
                if item.key in {"model.instructions", "personality"}
                or item.key in {section.key for section in snapshot.world_state_sections}
            }
            reinjected = freeze_context_messages(
                tuple(
                    replace(
                        instruction_updates.get(item.key, item),
                        id=ItemId(str(uuid4())),
                        turn_id=turn_id,
                        content=(
                            instruction_updates[item.key].content
                            if item.key in instruction_updates
                            else snapshot_content(item)
                        ),
                        snapshot_content=(
                            instruction_updates[item.key].snapshot_content
                            if item.key in instruction_updates
                            else None
                        ),
                    )
                    for item in snapshot.items
                ),
                initial=True,
                initial_extension_keys=snapshot.initial_extension_keys,
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
            fixed_tokens = await self._estimate_request_tokens(
                snapshot.instructions, fixed_items, fixed_tools
            )
            request_headroom = max(0, self._context_window_tokens - fixed_tokens - 1)
            retained_users = _retained_user_messages(
                historical,
                excluded_ids=protected_ids,
                token_budget=self._RETAINED_USER_TOKEN_BUDGET,
                request_token_budget=request_headroom,
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
            compacted_estimate = await self._estimate_request_tokens(
                snapshot.instructions, compacted_active, tools
            )
            if compacted_estimate >= self._context_window_tokens:
                raise ModelError(
                    "model-generated compaction is still too large "
                    f"({compacted_estimate}/{self._context_window_tokens} estimated tokens)",
                    kind=ModelErrorKind.CONTEXT_WINDOW,
                )
            if on_compact is not None:
                await on_compact(
                    "PostCompact",
                    str(checkpoint.id),
                    self._summary_model_name,
                    "auto",
                    stage="prepare",
                )
            await self._repository.append_items(thread_id, appended)
            if on_compact is not None:
                await on_compact(
                    "PostCompact", str(checkpoint.id), self._summary_model_name, "auto"
                )
            active = self._history(await self._repository.load_items(thread_id))
            if tool_resolver is not None:
                tools = tool_resolver(active)
            estimated = await self._estimate_request_tokens(snapshot.instructions, active, tools)
            compacted = True
            if self._body_prefix is not None:
                self._body_prefix.reset(str(checkpoint.id), estimated, usage)
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
        on_compact=None,
        tools: tuple[ToolSpec, ...] = (),
        phase="standalone_turn",
        reason=None,
        legacy_base_unknown: bool = False,
    ) -> PreparedHistory:
        """Standalone local compaction; the next normal turn rebuilds world state."""
        stored = await self._repository.load_items(thread_id)
        trigger = "manual" if phase == "standalone_turn" else "auto"
        if any(isinstance(i, CompactionItem) and i.turn_id == turn_id for i in stored):
            # Atomic history commit may precede the node checkpoint. Never ask
            # the model to summarize a second time after an installed result.
            if on_compact is not None:
                for marker in stored:
                    if isinstance(marker, CompactionItem) and marker.turn_id == turn_id:
                        await on_compact(
                            "PostCompact",
                            str(marker.id),
                            self._summary_model_name,
                            trigger,
                            stage="recover",
                        )
            active = self._history(stored)
            return PreparedHistory(
                active, await self._estimate_request_tokens(instructions, active, ()), True
            )
        if legacy_base_unknown:
            raise ValueError("missing admitted Turn base instructions")
        historical = self._history(stored)
        if self._media is not None:
            historical = await self._media.prepare_items(historical)
        if on_compact is not None:
            await on_compact(
                "PreCompact",
                str(stored[-1].id) if stored else "empty",
                self._summary_model_name,
                trigger,
            )
        summary = await self._summarize(
            historical, instructions=instructions, turn_id=turn_id, on_retry=on_retry
        )
        retained = _retained_user_messages(
            historical, excluded_ids=set(), token_budget=self._RETAINED_USER_TOKEN_BUDGET
        )
        marker = CompactionItem(summary, None, turn_id)
        marker = replace(
            marker,
            through_item_id=stored[-1].id if stored else None,
            replacement_item_count=len(retained),
            summary_insert_index=len(retained),
        )
        appended = (marker, *retained)
        if on_compact is not None:
            await on_compact(
                "PostCompact", str(marker.id), self._summary_model_name, trigger, stage="prepare"
            )
        await self._repository.append_items(thread_id, appended)
        if on_compact is not None:
            await on_compact("PostCompact", str(marker.id), self._summary_model_name, trigger)
        active = self._history((*stored, *appended))
        if self._body_prefix is not None:
            self._body_prefix.reset(
                str(marker.id),
                await self._estimate_request_tokens(instructions, active, ()),
                await self._repository.load_context_usage(thread_id),
            )
        return PreparedHistory(
            active, await self._estimate_request_tokens(instructions, active, ()), True
        )

    async def client_metadata(
        self, thread_id: ThreadId, turn_id: TurnId, *, tool_inventory_json=None
    ):
        """Ignore legacy checkpoint inventory; local notes need no remote ingestion."""
        return None

    async def remaining_tokens(
        self,
        thread_id: ThreadId,
        *,
        instructions: str | None = None,
        tools: tuple[ToolSpec, ...] | None = None,
    ) -> int | None:
        """Use this owner's limits with the call's captured request, including cold tools."""
        result = await self._budget_status(thread_id, instructions=instructions, tools=tools)
        return result[0].remaining if result is not None else None

    async def _estimate_request_tokens(self, instructions, items, tools):
        if self._media is not None:
            items = await self._media.prepare_items(items, for_model=True)
        return estimate_request_tokens(instructions, items, tools)

    async def _context_tokens_from_usage(self, usage, stored, candidate):
        if usage is None:
            return None
        if self._media is not None:
            candidate = await self._media.prepare_items(candidate, for_model=True)
        return context_tokens_from_usage(usage, stored, candidate)

    async def _budget_status(self, thread_id, *, instructions=None, tools=None):
        snapshot = self._last_snapshot
        if instructions is None and snapshot is None:
            return None
        stored = await self._repository.load_items(thread_id)
        recorded = {item.id for item in stored}
        active = tuple(item for item in self._history(stored) if item.id in recorded)
        local = await self._estimate_request_tokens(
            snapshot.instructions if instructions is None else instructions,
            active,
            self._last_tools if tools is None else tools,
        )
        usage = await self._repository.load_context_usage(thread_id)
        measured = await self._context_tokens_from_usage(usage, stored, active)
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
        # Auto-compaction follows provider usage when available, but the hard
        # admission limit also enforces the complete local request estimate.
        # Report the same hard ceiling to model tools and budget notices.
        hard_tokens = max(local, total)
        return BudgetStatus(
            max(
                0, min(self._auto_compact_tokens - scope, self._context_window_tokens - hard_tokens)
            ),
            scope >= self._auto_compact_tokens + self._fallback_buffer
            or hard_tokens >= self._context_window_tokens,
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

    @property
    def _summary_model_name(self):
        # Follow the same owner as _summarize, not the current Step's model.
        owner = self._local_compaction_owner
        return owner._summary_model_name if owner is not None else self._model_name

    async def _summarize(
        self,
        items: tuple[ConversationItem, ...],
        *,
        instructions: str,
        turn_id: TurnId,
        on_retry: CompactionRetryCallback | None = None,
    ) -> str:
        if self._local_compaction_owner is not None:
            # Native run_auto_compact passes StepContext.turn to compact.rs,
            # unlike remote/token-budget paths that consume StepContext.settings.
            return await self._local_compaction_owner._summarize(
                items, instructions=instructions, turn_id=turn_id, on_retry=on_retry
            )
        # Local Codex compaction retains base instructions and appends a user
        # compaction request. It does not recursively summarize isolated chunks.
        prompt = UserMessageItem(
            self._compact_prompt or self._prompt_store.render("tasks/compact"), turn_id
        )
        history = items
        retries = 0
        while True:
            request_items = (*history, prompt)
            # compact.rs submits accepted history first. A conservative local
            # estimate must not erase evidence that the provider could accept;
            # only its ContextWindowExceeded response below permits trimming.
            request = ModelRequest(
                model=self._model_name,
                thread_id=self._remote_request_options.get("thread_id"),
                content_item_kinds=self._remote_request_options.get("content_item_kinds", True),
                instructions=instructions,
                context_items=(),
                items=await self._media.prepare_items(request_items, for_model=True)
                if self._media is not None
                else request_items,
                tools=(),
                harness_managed_retries=True,
                reasoning_effort=self._remote_request_options.get("reasoning_effort"),
                reasoning_summary=self._remote_request_options.get("reasoning_summary"),
                service_tier=self._remote_request_options.get("service_tier"),
                fast_mode_enabled=self._remote_request_options.get("fast_mode_enabled", True),
                model_info=self._remote_request_options.get("model_info"),
                reasoning_effort_resolved=self._remote_request_options.get(
                    "reasoning_effort_resolved", False
                ),
            )
            try:
                return await self._summary_attempt(request)
            except ModelError as exc:
                # User-required provider boundary: payment failures need an
                # external account change, including during summary sampling.
                if exc.status_code == 402:
                    raise
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
        task = asyncio.current_task()
        cancellations = task.cancelling() if task is not None else 0
        try:
            stream = self._model.stream(request)
            primary_error: BaseException | None = None
            try:
                async for event in stream:
                    if isinstance(event, ModelCompleted):
                        completed = event
                        break
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                try:
                    await stream.aclose()
                except Exception:
                    if primary_error is None:
                        raise
                    _LOG.warning("Summary stream cleanup failed while unwinding", exc_info=True)
        except Exception as exc:
            # A generator's finally/aclose may replace CancelledError with its
            # own failure. A newly requested cancellation still owns the turn.
            if task is not None and task.cancelling() > cancellations:
                raise asyncio.CancelledError from exc
            raise
        # A ModelPort can also suppress cancellation and return normally (even
        # with ModelCompleted). Closing the stream must not make that cancelled
        # attempt eligible to install a new history window.
        if task is not None and task.cancelling() > cancellations:
            raise asyncio.CancelledError
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
        and (item.retained_from_id or item.id) == (pending.retained_from_id or pending.id)
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


def _drop_oldest_pair(items: tuple[ConversationItem, ...]) -> tuple[ConversationItem, ...]:
    """Trim whole recorded messages/tool pairs in the request view, never raw history."""
    first, *rest = items
    if isinstance(first, ContextItem) and first.message_group_id is not None:
        group = next(context_message_groups(items))
        excluded = {item.id for item in group}
        retained = tuple(item for item in items if item.id not in excluded)
    elif isinstance(first, ToolCallItem):
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
