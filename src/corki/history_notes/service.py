"""Runtime-owned local recovery tools and window hints."""

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import replace

from corki.config.settings import CorkiSettings
from corki.context.builder import ContextSnapshot
from corki.context.token_budget import window_identities
from corki.history_notes.backend import HistoryNotesError, RecoveryBackend
from corki.history_notes.schemas import (
    ACTIONS,
    HISTORY_DESCRIPTION,
    NOTES_DESCRIPTION,
    action_schema,
)
from corki.protocol.execution_identity import ExecutionIdentity
from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import ContextItem, ContextRole, ConversationItem
from corki.protocol.tools import (
    ImageAttachment,
    TextContent,
    ToolCall,
    ToolConcurrency,
    ToolExposure,
    ToolResult,
    ToolSpec,
)
from corki.tools.base import ToolContext

_LOG = logging.getLogger(__name__)


def history_notes_requested(settings: CorkiSettings) -> bool:
    """An explicit request activates local recovery for every provider."""
    return bool(
        settings.token_budget_enabled
        and settings.token_budget
        and settings.token_budget.use_history_notes_extension
    )


class HistoryNotesTool:
    """One local model-only action executed through the normal tool ledger."""

    def __init__(
        self,
        backend: RecoveryBackend,
        identity: Callable[[], ExecutionIdentity],
        name: str,
        budget: int,
    ) -> None:
        self.backend, self._identity, self.budget = backend, identity, budget
        schema = action_schema(name)
        description = HISTORY_DESCRIPTION if name.startswith("history::") else NOTES_DESCRIPTION
        if "item_id" in schema["properties"]:
            schema["properties"]["item_id"]["description"] = (
                "Opaque item_id returned by local history list/search; pass unchanged."
            )
        description += (
            " Local compatibility: readable, unencrypted output; only the current /root "
            "agent is available. Storage is isolated to this thread; reads, listings and "
            "searches reflect completed writes immediately. Returned IDs are opaque; "
            "use them unchanged for subsequent reads. Paths must not exceed 4096 UTF-8 bytes."
        )
        self.spec = ToolSpec(
            name,
            ACTIONS[name][2] + " Private model-only recovery; use silently.",
            schema,
            exposure=ToolExposure.DIRECT_MODEL_ONLY,
            concurrency=ToolConcurrency.EXCLUSIVE
            if name in ("notes::write_file", "notes::append_to_file")
            else ToolConcurrency.PARALLEL,
            namespace_description=description,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        """Keep opaque output intact and separate images before text serialization."""
        result = await self.backend.call(
            self.spec.name,
            dict(call.arguments or {}),
            session_id=self._identity().session_id,
            budget=self.budget,
        )
        images = ()
        if isinstance(result, dict) and "images" in result:
            result = dict(result)
            images = result.pop("images")
            if not isinstance(images, list):
                raise HistoryNotesError("History backend returned invalid image content.")
        parts = [TextContent(json.dumps(result, ensure_ascii=False, separators=(",", ":")))]
        for image in images:
            if (
                not isinstance(image, dict)
                or not isinstance(image.get("data"), str)
                or not isinstance(image.get("mime_type"), str)
                or image.get("detail") not in (None, "auto", "low", "high", "original")
            ):
                raise HistoryNotesError("History backend returned invalid image content.")
            parts.append(
                ImageAttachment(
                    f"data:{image['mime_type']};base64,{image['data']}",
                    image.get("detail") or "high",
                )
            )
        return ToolResult(
            call.id, call.name, "History operation completed.", content_items=tuple(parts)
        )


class HistoryNotesService:
    """Single-root identity, local tools and one bounded hint per active window."""

    def __init__(
        self,
        settings: CorkiSettings,
        thread_id: ThreadId,
        *,
        backend: RecoveryBackend,
    ) -> None:
        self.thread_id = thread_id
        self._identity: ExecutionIdentity | None = None
        self.backend = backend
        self.tools = tuple(
            HistoryNotesTool(
                self.backend, self._require_identity, name, settings.tool_output_char_budget
            )
            for name in ACTIONS
        )
        self._hint_window, self._hint = None, None

    def bind_identity(self, identity: ExecutionIdentity) -> None:
        """Bind durable host identity once, before any context or tool operation."""
        if not isinstance(identity, ExecutionIdentity):
            raise TypeError("history recovery requires ExecutionIdentity")
        if identity.thread_id != self.thread_id:
            raise ValueError("history recovery identity belongs to another thread")
        if self._identity is not None and self._identity != identity:
            raise RuntimeError("history recovery identity is already bound")
        self._identity = identity

    def _require_identity(self) -> ExecutionIdentity:
        if self._identity is None:
            raise RuntimeError("history recovery identity is not initialized")
        return self._identity

    async def decorate(
        self, snapshot: ContextSnapshot, stored: Sequence[ConversationItem], turn_id: TurnId
    ) -> ContextSnapshot:
        """Fetch on a new window; failures omit the hint but never swallow cancellation."""
        identity = self._require_identity()
        window = window_identities(stored, self.thread_id)[-1]
        if self._hint_window != window:
            hint = None
            try:
                result = await self.backend.call(
                    "notes::thread_hint", {}, session_id=identity.session_id, budget=4000
                )
                if (
                    isinstance(result, dict)
                    and isinstance(result.get("text"), str)
                    and len(result["text"].encode("utf-8")) <= 4000
                ):
                    hint = result["text"] or None
            except Exception:
                _LOG.warning("History-notes thread hint unavailable")
            self._hint_window, self._hint = window, hint
        items = tuple(item for item in snapshot.items if item.key != "notes.thread_hint")
        if self._hint:
            items += (
                ContextItem(
                    "notes.thread_hint",
                    ContextRole.DEVELOPER,
                    self._hint,
                    turn_id,
                    content_kind="corki.history_notes.thread_hint",
                ),
            )
        return replace(snapshot, items=items)

    async def aclose(self) -> None:
        """Release the service's owned storage resources."""
        await self.backend.aclose()
