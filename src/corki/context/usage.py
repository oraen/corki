"""Provider usage plus local additions, fenced to one replacement window."""

from corki.context.tokens import estimate_item_tokens
from corki.protocol.items import CompactionItem, ConversationItem, ReasoningItem, UserMessageItem
from corki.sessions.models import ContextUsage


class BodyPrefixWindow:
    """Session-local estimated/server prefill, reset by history replacement."""

    def __init__(self) -> None:
        self.initialized = False
        self.window_id: str | None = None
        self.prefill_tokens = 0
        self.server_observed = False
        self.ignored_sample_id: str | None = None

    def reset(self, window_id: str | None, estimated: int, usage: ContextUsage | None) -> None:
        self.initialized = True
        self.window_id = window_id
        self.prefill_tokens = max(0, estimated)
        self.server_observed = False
        self.ignored_sample_id = usage.sample_id if usage is not None else None

    def measure(
        self,
        *,
        window_id: str | None,
        estimated_prefill: int,
        local_tokens: int,
        active_tokens: int,
        usage: ContextUsage | None,
    ) -> tuple[int, int]:
        if not self.initialized or window_id != self.window_id:
            self.reset(window_id, estimated_prefill, usage)
        if (
            not self.server_observed
            and usage is not None
            and usage.input_tokens is not None
            and usage.sample_id is not None
            and usage.sample_id != self.ignored_sample_id
        ):
            self.prefill_tokens = max(0, usage.input_tokens)
            self.server_observed = True
        if not self.server_observed:
            # Resume/recompute replaces old usage with a local estimate until
            # a new response supplies the first input count for this session window.
            active_tokens = local_tokens
        return active_tokens, max(0, active_tokens - self.prefill_tokens)


def context_tokens_from_usage(
    usage: ContextUsage | None,
    stored: tuple[ConversationItem, ...],
    candidate: tuple[ConversationItem, ...],
) -> int | None:
    if usage is None:
        return None
    anchor = next((index for index, item in enumerate(stored) if item.id == usage.anchor_id), None)
    if anchor is None or any(isinstance(item, CompactionItem) for item in stored[anchor + 1 :]):
        return None
    counted = {item.id for item in stored[: anchor + 1]}
    tokens = usage.total_tokens + sum(
        estimate_item_tokens(item) for item in candidate if item.id not in counted
    )
    # Ordinary usage has no private server declaration controlling this estimate.
    # Legacy ContextUsage flags must not reactivate that protocol on resume.
    last_user = next(
        (
            index
            for index in range(len(candidate) - 1, -1, -1)
            if isinstance(candidate[index], UserMessageItem)
        ),
        0,
    )
    tokens += sum(
        estimate_item_tokens(item)
        for item in candidate[:last_user]
        if isinstance(item, ReasoningItem)
        and item.encrypted_content is not None
        and item.id in counted
    )
    return tokens
