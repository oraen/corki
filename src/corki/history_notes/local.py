"""Provider-independent history/notes recovery, without native ingestion or decryption."""

import asyncio
import logging
import sqlite3
from pathlib import Path

from corki.history_notes.archive import history_action
from corki.history_notes.notes_store import NotesStore
from corki.protocol.ids import ThreadId
from corki.sessions.repository import SessionRepository


class LocalHistoryNotesBackend:
    """Use this thread's archive and transactionally persisted virtual notes."""

    def __init__(self, repository: SessionRepository, path: Path, thread_id: ThreadId) -> None:
        self._repository, self._thread_id = repository, thread_id
        self._notes = NotesStore(path, thread_id)
        self._closed = False

    async def call(self, action, arguments, *, session_id, budget):
        """Use captured identity, never caller-supplied context or host filesystem paths."""
        if self._closed:
            raise ValueError("Local history/notes backend is closed")
        namespace, name = action.split("::")
        if namespace == "history":
            stored = await self._repository.load_items(self._thread_id)
            return await _joined(history_action, name, arguments, stored, self._thread_id, budget)
        if namespace == "notes":
            try:
                return await _joined(self._notes.call, name, arguments, budget)
            except (sqlite3.Error, OSError):
                raise ValueError(
                    "Local recovery storage operation failed; execution outcome may be unknown"
                ) from None
        raise ValueError("Unknown recovery namespace")

    async def aclose(self):
        """Runtime joins active calls before closure; no persistent connection is held."""
        self._closed = True


async def _joined(operation, *args):
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled() and task.exception() is not None:
            logging.getLogger(__name__).warning(
                "Local recovery operation failed during cancellation"
            )
        raise
