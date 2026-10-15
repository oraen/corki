"""Provider-neutral, continuous conversational memory facade."""

from __future__ import annotations

from corki.context.history import active_history
from corki.protocol.ids import ThreadId
from corki.protocol.items import ConversationItem
from corki.sessions.repository import SessionRepository


class ConversationMemory:
    """Expose durable history without relevance-based gaps or provider types."""

    def __init__(self, repository: SessionRepository) -> None:
        self._repository = repository

    async def current(self, thread_id: ThreadId) -> tuple[ConversationItem, ...]:
        return active_history(await self._repository.load_items(thread_id))

    async def append(self, thread_id: ThreadId, items: tuple[ConversationItem, ...]) -> None:
        await self._repository.append_items(thread_id, items)
