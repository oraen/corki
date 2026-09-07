"""Runtime-owned recovery tools, window hints and optional native ingestion metadata."""

import json
import logging
from collections.abc import Sequence
from dataclasses import replace

import httpx

from corki.config.settings import CorkiSettings
from corki.context.builder import ContextSnapshot
from corki.context.token_budget import window_identities
from corki.history_notes.backend import HistoryNotesBackend, HistoryNotesError, RecoveryBackend
from corki.history_notes.schemas import (
    ACTIONS,
    HISTORY_DESCRIPTION,
    NOTES_DESCRIPTION,
    action_schema,
)
from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import ContextItem, ContextRole, ConversationItem
from corki.protocol.tools import (
    EncryptedContent,
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


def history_notes_enabled(settings: CorkiSettings) -> bool:
    """Require explicit backend credentials; ordinary API keys are not eligible."""
    return bool(
        settings.token_budget_enabled
        and settings.token_budget
        and settings.token_budget.use_history_notes_extension
        and settings.codex_backend
        and settings.provider_name == "openai"
        and settings.api_mode == "responses"
        and settings.api_key
    )


def history_notes_requested(settings: CorkiSettings) -> bool:
    """An explicit request activates native or usable local compatibility recovery."""
    return bool(
        settings.token_budget_enabled
        and settings.token_budget
        and settings.token_budget.use_history_notes_extension
    )


class HistoryNotesTool:
    """One model-only v2 action executed through the normal tool ledger."""

    def __init__(
        self, backend: RecoveryBackend, session_id: ThreadId, name: str, budget: int
    ) -> None:
        self.backend, self.session_id, self.budget = backend, session_id, budget
        schema = action_schema(name)
        description = HISTORY_DESCRIPTION if name.startswith("history::") else NOTES_DESCRIPTION
        if not backend.native:
            for parameter in schema["properties"].values():
                parameter.pop("encrypted", None)
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
            session_id=self.session_id,
            budget=self.budget,
        )
        images = ()
        if isinstance(result, dict) and "images" in result:
            result = dict(result)
            images = result.pop("images")
            if not isinstance(images, list):
                raise HistoryNotesError("History backend returned invalid image content.")
        encrypted = result.get("encrypted_output") if isinstance(result, dict) else None
        parts = (
            [EncryptedContent(encrypted)]
            if isinstance(encrypted, str)
            else [TextContent(json.dumps(result, ensure_ascii=False, separators=(",", ":")))]
        )
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
    """Single-root identity, native tools and one bounded hint per active window."""

    def __init__(
        self,
        settings: CorkiSettings,
        thread_id: ThreadId,
        *,
        client: httpx.AsyncClient | None = None,
        backend: RecoveryBackend | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.backend = (
            backend
            if backend is not None
            else HistoryNotesBackend(settings.api_base, settings.api_key, client=client)
        )
        self.tools = tuple(
            HistoryNotesTool(self.backend, thread_id, name, settings.tool_output_char_budget)
            for name in ACTIONS
        )
        self._hint_window, self._hint = None, None

    async def decorate(
        self, snapshot: ContextSnapshot, stored: Sequence[ConversationItem], turn_id: TurnId
    ) -> ContextSnapshot:
        """Fetch on a new window; failures omit the hint but never swallow cancellation."""
        window = window_identities(stored, self.thread_id)[-1]
        if self._hint_window != window:
            hint = None
            try:
                result = await self.backend.call(
                    "notes::thread_hint", {}, session_id=self.thread_id, budget=4000
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
            items += (ContextItem("notes.thread_hint", ContextRole.DEVELOPER, self._hint, turn_id),)
        return replace(snapshot, items=items)

    def metadata(
        self, stored: Sequence[ConversationItem], turn_id: TurnId
    ) -> dict[str, str] | None:
        """Build trusted ingestion fields from committed history, not model arguments."""
        if not self.backend.native:
            return None
        windows = window_identities(stored, self.thread_id)
        window_id = f"{self.thread_id}:{len(windows) - 1}"
        value = {
            "session_id": str(self.thread_id),
            "thread_id": str(self.thread_id),
            "turn_id": str(turn_id),
            "agent_name": "/root",
            "window_id": window_id,
            "context_window_id": windows[-1],
            "window_number": len(windows) - 1,
            "history_ingest_requested": True,
            "request_kind": "turn",
        }
        return {
            "session_id": str(self.thread_id),
            "thread_id": str(self.thread_id),
            "turn_id": str(turn_id),
            "x-codex-window-id": window_id,
            "x-codex-turn-metadata": json.dumps(value, ensure_ascii=True, separators=(",", ":")),
        }

    async def aclose(self) -> None:
        """Release the service's owned network resources."""
        await self.backend.aclose()
