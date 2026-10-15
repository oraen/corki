"""Memory-only canonical state using the same transaction and identity contracts."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from corki.storage.sqlite import SQLiteSessionRepository, _joined_write


class VolatileSessionRepository(SQLiteSessionRepository):
    """Own a private RAM connection; never open a database filename or spill temp tables."""

    def __init__(self) -> None:
        self._mutex = RLock()
        self._closed = False
        self._connection = sqlite3.connect(":memory:", check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA temp_store=MEMORY")
        try:
            self._create_schema()
        except BaseException:
            self._connection.close()
            raise

    @property
    def path(self) -> Path:
        raise RuntimeError("ephemeral session storage has no filesystem path")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Existing repository operations run on joined worker threads. Serialize
        # whole transactions, not individual statements on this shared connection.
        with self._mutex:
            if self._closed:
                raise RuntimeError("ephemeral session storage is closed")
            with self._connection:
                yield self._connection

    async def close(self) -> None:
        """Join connection cleanup after Runtime has stopped its writers/readers."""
        await _joined_write(self._close)

    async def materialize_transcript(self, thread_id):
        return None

    def _after_durable_write(self):
        # Ephemeral storage must never create a transcript or touch the filesystem.
        pass

    def _close(self) -> None:
        with self._mutex:
            if not self._closed:
                self._connection.close()
                self._closed = True
