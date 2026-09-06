"""Stable incremental context and automatic model-generated compaction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from uuid import uuid4

from corki.context.builder import ContextSnapshot
from corki.context.history import active_history
from corki.context.tokens import (
    estimate_item_tokens,
    estimate_request_tokens,
    estimate_text_tokens,
)
from corki.models.base import ModelError, ModelErrorKind, ModelPort
from corki.models.types import ModelCompleted, ModelRequest
from corki.prompting import PromptStore
from corki.protocol.ids import ItemId, ThreadId, TurnId
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ConversationItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
)
from corki.protocol.tools import ToolSpec
from corki.sessions.repository import SessionRepository


@dataclass(frozen=True, slots=True)
class PreparedHistory:
    items: tuple[ConversationItem, ...]
    estimated_tokens: int
    compacted: bool = False


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
        auto_compact_tokens: int,
        prompt_store: PromptStore | None = None,
    ) -> None:
        self._repository = repository
        self._model = model
        self._model_name = model_name
        self._context_window_tokens = context_window_tokens
        self._auto_compact_tokens = auto_compact_tokens
        self._prompt_store = prompt_store or PromptStore()

    async def prepare(
        self,
        *,
        thread_id: ThreadId,
        turn_id: TurnId,
        snapshot: ContextSnapshot,
        tools: tuple[ToolSpec, ...],
        pending_items: tuple[ConversationItem, ...] = (),
        tool_resolver: Callable[[tuple[ConversationItem, ...]], tuple[ToolSpec, ...]] | None = None,
    ) -> PreparedHistory:
        all_items = await self._repository.load_items(thread_id)
        changed_context = _changed_context_items(all_items, snapshot.items, turn_id)
        stored_ids = {item.id for item in all_items}
        new_pending = tuple(item for item in pending_items if item.id not in stored_ids)
        historical = active_history(all_items)
        candidate = active_history((*all_items, *changed_context, *new_pending))
        if tool_resolver is not None:
            tools = tool_resolver(candidate)
        estimated = estimate_request_tokens(snapshot.instructions, candidate, tools)
        compacted = False
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
        compactable = tuple(
            item
            for item in historical
            if not isinstance(item, ContextItem) and item.id not in protected_ids
        )
        if (
            estimated >= self._auto_compact_tokens
            and len(compactable) > 1
            and (
                bool(new_pending)
                or bool(changed_context)
                or _has_post_compaction_activity(all_items)
            )
        ):
            summary = await self._summarize(compactable, depth=0)
            # The marker supersedes every previously persisted item, including
            # any pending item copied back below. Keep its audit pointer real
            # even when active-history normalization synthesized an item.
            checkpoint = CompactionItem(summary, all_items[-1].id, turn_id)
            reinjected = tuple(
                replace(item, id=ItemId(str(uuid4())), turn_id=turn_id) for item in snapshot.items
            )
            # A normal run has not persisted ``new_pending`` yet. The fallback
            # copy handles recovery from an older checkpoint whose user item
            # was already stored before context preparation.
            pending_after_checkpoint = tuple(
                item if item.id not in stored_ids else replace(item, id=ItemId(str(uuid4())))
                for item in pending_items
            )
            fixed_items = (checkpoint, *reinjected, *pending_after_checkpoint)
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
            if pending_after_checkpoint:
                # Pre-turn compaction: retained prior user messages and the
                # summary form the old history, then fresh context/current input.
                replacement = (*retained_users, *reinjected, *pending_after_checkpoint)
                summary_index = len(retained_users)
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
            compacted_active = active_history((*all_items, *appended))
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
        else:
            appended = (*changed_context, *new_pending)
            if appended:
                # Match Codex's turn-start ordering: world-state/context changes
                # are recorded before the user input they qualify. One append
                # also makes that order atomic for crash recovery.
                await self._repository.append_items(thread_id, appended)
            active = candidate

        if estimated >= self._context_window_tokens:
            raise ModelError(
                f"prepared context exceeds model window ({estimated}/"
                f"{self._context_window_tokens} estimated tokens)",
                kind=ModelErrorKind.CONTEXT_WINDOW,
            )
        return PreparedHistory(active, estimated, compacted)

    async def _summarize(self, items: tuple[ConversationItem, ...], *, depth: int) -> str:
        if depth > 4:
            raise ModelError(
                "compaction could not reduce history within four passes",
                kind=ModelErrorKind.CONTEXT_WINDOW,
            )
        chunks = _chunk_items(items, max(1_024, self._auto_compact_tokens // 2))
        if len(chunks) > 1:
            summaries = [await self._summarize_once(chunk) for chunk in chunks]
            intermediate = tuple(
                CompactionItem(summary, chunk[-1].id, chunk[-1].turn_id)
                for summary, chunk in zip(summaries, chunks, strict=True)
            )
            return await self._summarize(intermediate, depth=depth + 1)
        return await self._summarize_once(chunks[0])

    async def _summarize_once(self, items: tuple[ConversationItem, ...]) -> str:
        compaction_instructions = self._prompt_store.render("tasks/compact")
        estimated = estimate_request_tokens(compaction_instructions, items, ())
        if estimated >= self._context_window_tokens:
            raise ModelError(
                "one conversation item is too large to compact safely "
                f"({estimated}/{self._context_window_tokens} estimated tokens)",
                kind=ModelErrorKind.CONTEXT_WINDOW,
            )
        request = ModelRequest(
            model=self._model_name,
            instructions=compaction_instructions,
            context_items=(),
            items=items,
            tools=(),
        )
        completed: ModelCompleted | None = None
        async for event in self._model.stream(request):
            if isinstance(event, ModelCompleted):
                completed = event
        if completed is None:
            raise ModelError("compaction stream ended without completion")
        summary = "\n".join(
            item.content
            for item in completed.items
            if isinstance(item, AssistantMessageItem) and item.content.strip()
        ).strip()
        if not summary:
            raise ModelError("compaction model returned no summary")
        return summary


def _changed_context_items(
    history: tuple[ConversationItem, ...],
    snapshot: tuple[ContextItem, ...],
    turn_id: TurnId,
) -> tuple[ContextItem, ...]:
    latest: dict[str, ContextItem] = {}
    for item in history:
        if isinstance(item, ContextItem):
            latest[item.key] = item
    current_keys = {item.key for item in snapshot}
    changed = [
        replace(item, turn_id=turn_id)
        for item in snapshot
        if latest.get(item.key) is None or latest[item.key].content != item.content
    ]
    changed.extend(
        ContextItem(key, item.role, "", turn_id)
        for key, item in latest.items()
        if key not in current_keys and item.content
    )
    return tuple(changed)


def _same_user_input(item: UserMessageItem, pending: ConversationItem) -> bool:
    """Recognize a pending input copied by a pre-checkpoint recovery attempt."""

    return (
        isinstance(pending, UserMessageItem)
        and item.turn_id == pending.turn_id
        and item.content == pending.content
        and item.attachments == pending.attachments
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
        cost = estimate_item_tokens(item)
        if cost <= remaining:
            selected.append(replace(item, id=ItemId(str(uuid4()))))
            remaining -= cost
            continue
        text_budget = max(0, remaining - 12)
        if text_budget:
            content = _truncate_to_estimated_tokens(item.content, text_budget)
            selected.append(replace(item, id=ItemId(str(uuid4())), content=content))
        break
    selected.reverse()
    return tuple(selected)


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


def _chunk_items(
    items: tuple[ConversationItem, ...], budget: int
) -> tuple[tuple[ConversationItem, ...], ...]:
    """Split only at item boundaries while retaining every item in order."""

    chunks: list[tuple[ConversationItem, ...]] = []
    current: list[ConversationItem] = []
    tokens = 0
    for item in items:
        item_tokens = estimate_item_tokens(item)
        can_break = (
            bool(current)
            and not isinstance(current[-1], ToolCallItem)
            and not isinstance(item, ToolResultItem)
        )
        if can_break and tokens + item_tokens > budget:
            chunks.append(tuple(current))
            current = []
            tokens = 0
        current.append(item)
        tokens += item_tokens
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)
