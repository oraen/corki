"""Own bounded history hydration separately from live output and model recovery."""

import asyncio
from copy import copy
from io import StringIO

from rich.console import Console

from corki.cli.history_projection import HistoryProjection
from corki.cli.transcript import Transcript
from corki.sessions.models import DisplayHistory


class HistoryPager:
    def __init__(self, runtime, ui):
        self.runtime, self.ui = runtime, ui
        self.items, self.turns = (), ()
        self.item_cursor = self.turn_cursor = None
        self.item_seen, self.turn_seen = set(), set()
        self.through_sequence = -1
        self.identities = {}
        self.task = None
        self.closed = False
        self.start = len(ui._transcript.calls)
        self.anchor = ui._transcript.calls[-1] if self.start else None
        self.segment = ()

    @property
    def has_older(self):
        return not self.closed and (self.item_cursor is not None or self.turn_cursor is not None)

    def _history(self):
        turns = self.turns
        if self.item_cursor is not None:
            # An older Turn with unloaded items is not an empty failed Turn.
            order = {turn.id: index for index, turn in enumerate(turns)}
            first = next(
                (order[item.turn_id] for item in self.items if item.turn_id in order), None
            )
            turns = turns[first:] if first is not None else ()
        return DisplayHistory(self.items, turns)

    async def initialize(self):
        await self._read(initial=True)
        for _ in range(3):
            if self.items or self.item_cursor is None:
                break
            await self._read()
        history = self._history()
        self.ui.replay_history(history)
        self.segment = tuple(self.ui._transcript.calls[self.start :])
        self.ui.set_history_loader(self)
        return history

    @staticmethod
    def _advance(current, following, seen):
        if following is not None:
            if following == current or following in seen:
                raise ValueError("history cursor did not advance")
            seen.add(following)
        return following

    async def _read(self, *, initial=False):
        # Stage all changes: a partial request failure must remain retryable
        # from the old cursor without inserting half a page into the view.
        items, turns = self.items, self.turns
        item_cursor, turn_cursor = self.item_cursor, self.turn_cursor
        item_seen, turn_seen = set(self.item_seen), set(self.turn_seen)
        boundary = self.through_sequence
        if initial or turn_cursor is not None:
            page = await self.runtime.load_display_turns_page(cursor=turn_cursor, limit=5)
            turn_cursor = self._advance(turn_cursor, page.next_cursor, turn_seen)
            known = {turn.id for turn in turns}
            turns = tuple(turn for turn in page.turns if turn.id not in known) + turns
        if initial or item_cursor is not None:
            page = await self.runtime.load_display_items_page(cursor=item_cursor, limit=100)
            item_cursor = self._advance(item_cursor, page.next_cursor, item_seen)
            if initial:
                boundary = page.newest_sequence if page.newest_sequence is not None else -1
            visible = HistoryProjection(page.leading_replacements).feed(page.items)
            known = {item.id for item in items}
            items = tuple(item for item in visible if item.id not in known) + items
        known_turns = {turn.id for turn in turns}
        missing = {item.turn_id for item in items} - known_turns
        while missing and turn_cursor is not None:
            page = await self.runtime.load_display_turns_page(cursor=turn_cursor, limit=5)
            turn_cursor = self._advance(turn_cursor, page.next_cursor, turn_seen)
            turns = tuple(turn for turn in page.turns if turn.id not in known_turns) + turns
            known_turns.update(turn.id for turn in page.turns)
            missing.difference_update(known_turns)
        self.items, self.turns = items, turns
        self.item_cursor, self.turn_cursor = item_cursor, turn_cursor
        self.item_seen, self.turn_seen = item_seen, turn_seen
        self.through_sequence = boundary

    def request_older(self, *, beginning=False):
        if not self.has_older or self.task is not None:
            return
        self.task = asyncio.create_task(self._load_older(beginning))
        self.ui._session.app.invalidate()

    async def _load_older(self, beginning):
        try:
            while self.has_older:
                checkpoint = (
                    self.items,
                    self.turns,
                    self.item_cursor,
                    self.turn_cursor,
                    self.item_seen,
                    self.turn_seen,
                    self.through_sequence,
                )
                try:
                    await self._read()
                    self._replace_history()
                except BaseException:
                    (
                        self.items,
                        self.turns,
                        self.item_cursor,
                        self.turn_cursor,
                        self.item_seen,
                        self.turn_seen,
                        self.through_sequence,
                    ) = checkpoint
                    raise
                if not beginning:
                    break
            if beginning and not self.closed:
                self.ui._history_view.row = 0
        except Exception as error:  # noqa: BLE001 - display failure must not fail a model Turn
            if not self.closed:
                self.ui.show_notice(f"Could not load older history: {error}")
        finally:
            self.task = None
            self.ui._session.app.invalidate()

    def _replace_history(self):
        transcript, view = self.ui._transcript, self.ui._history_view
        if self.closed or (
            self.anchor is not None
            and (
                len(transcript.calls) < self.start
                or transcript.calls[self.start - 1] is not self.anchor
            )
        ):
            # /clear replaces the welcome anchor. Do not resurrect cleared display.
            self.closed = True
            return
        end = self.start + len(self.segment)
        if any(
            a is not b
            for a, b in zip(transcript.calls[self.start : end], self.segment, strict=True)
        ):
            raise ValueError("display history region changed while loading")
        view.text()
        old_rows, old_row = view.rows, view.row
        follow_bottom = old_row >= old_rows - 1
        shadow = copy(self.ui)
        shadow._console = Console(file=StringIO(), width=self.ui._console.width)
        shadow._history_view = None
        shadow._transcript = Transcript(shadow)
        shadow._reasoning_buffer = shadow._reasoning_header = ""
        shadow._reasoning_active = False
        shadow.replay_history(self._history())
        replacement = tuple(shadow._transcript.calls)
        shadow._transcript.calls = (
            transcript.calls[: self.start] + list(replacement) + transcript.calls[end:]
        )
        # Build the candidate frame before publishing either the source or its
        # cursor. A rendering exception must leave the same page retryable.
        candidate = copy(view)
        candidate.ui = shadow
        candidate.text()
        transcript.calls[self.start : end] = replacement
        self.segment = replacement
        view.content, view.formatted, view.lines = (
            candidate.content,
            candidate.formatted,
            candidate.lines,
        )
        view.rows, view.row, view.cache_key = candidate.rows, candidate.row, candidate.cache_key
        if not follow_bottom:
            view.row = min(view.rows - 1, max(0, old_row + view.rows - old_rows))
        self.ui._session.app.invalidate()

    async def contains(self, kind, identity):
        if identity is None or self.through_sequence < 0:
            return False
        key = (kind, identity)
        if key not in self.identities:
            self.identities[key] = await self.runtime.contains_display_item(
                kind=kind, identity=identity, through_sequence=self.through_sequence
            )
        return self.identities[key]

    def stop(self):
        """Revoke display ownership immediately; aclose still joins the read."""
        self.closed = True
        if self.task is not None:
            self.task.cancel()

    async def aclose(self):
        self.stop()
        task = self.task
        if task is not None:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            if not task.cancelled():
                task.result()
