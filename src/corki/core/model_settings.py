"""Separate future thread defaults from immutable, recoverable Turn selections."""

import asyncio
import logging
from dataclasses import replace

from corki.config import CorkiSettings
from corki.protocol.collaboration import CollaborationMode
from corki.protocol.ids import ThreadId
from corki.protocol.settings import UNSET, ModelSettingsSnapshot, ThreadModelSettings, UnsetSetting
from corki.sessions.repository import SessionRepository


def capture_model_settings(settings: CorkiSettings) -> ModelSettingsSnapshot:
    """Resolve metadata once at admission without retaining credentials in state."""
    return ModelSettingsSnapshot(
        settings.model,
        settings.provider_id or settings.provider_name or "default",
        settings.reasoning_effort,
        settings.reasoning_summary,
        settings.service_tier,
        settings.model_context_info(settings.model),
        settings.main_context_limits.raw_tokens,
        settings.collaboration_mode,
        settings.collaboration_instructions,
        personality=settings.personality,
        personality_enabled=settings.personality_enabled,
    )


def bind_model_settings(base: CorkiSettings, snapshot: ModelSettingsSnapshot) -> CorkiSettings:
    """Bind a saved selection to current host resources, never a different provider."""
    if snapshot.provider != (base.provider_id or base.provider_name or "default"):
        raise ValueError("pending Turn model settings belong to a different provider")
    return replace(
        base,
        model=snapshot.model,
        reasoning_effort=snapshot.reasoning_effort,
        reasoning_summary=snapshot.reasoning_summary,
        service_tier=snapshot.service_tier,
        context_window_tokens=snapshot.raw_context_window,
        _admitted_model_info=snapshot.model_info,
        collaboration_mode=snapshot.collaboration_mode,
        collaboration_instructions=snapshot.collaboration_instructions,
        personality=snapshot.personality,
        personality_enabled=snapshot.personality_enabled,
        # The exact saved view already includes its old overrides. Other model
        # lookups still need the current host's window/output configuration.
    )


class ThreadSettingsController:
    """Publish defaults under the Runtime's shared lifecycle/admission permit."""

    def __init__(self, settings: CorkiSettings, repository: SessionRepository, thread_id: ThreadId):
        self.settings = settings
        self._repository = repository
        self._thread_id = thread_id

    @property
    def snapshot(self) -> ModelSettingsSnapshot:
        return capture_model_settings(self.settings)

    async def update(
        self,
        *,
        model: str | None = None,
        reasoning_effort: str | None | UnsetSetting = UNSET,
        reasoning_summary: str | None = None,
        service_tier: str | None | UnsetSetting = UNSET,
        collaboration_mode: CollaborationMode | None = None,
        personality: str | None | UnsetSetting = UNSET,
    ) -> ModelSettingsSnapshot:
        """Sparse edits; caller retains its permit until commit/publication finishes."""
        changes = {}
        if personality is not UNSET:
            changes["personality"] = personality
        if model is not None:
            changes["model"] = model
        if reasoning_effort is not UNSET:
            changes["reasoning_effort"] = reasoning_effort
        if reasoning_summary is not None:
            changes["reasoning_summary"] = reasoning_summary
        if service_tier is not UNSET:
            changes["service_tier"] = "default" if service_tier is None else service_tier
        if collaboration_mode is not None:
            changes.update(collaboration_mode.settings_changes())
        candidate = replace(self.settings, **changes)
        snapshot = capture_model_settings(candidate)
        if not changes:
            return self.snapshot

        async def commit() -> ModelSettingsSnapshot:
            await self._repository.save_thread_model_settings(
                self._thread_id,
                ThreadModelSettings(
                    snapshot.model,
                    snapshot.provider,
                    snapshot.reasoning_effort,
                    snapshot.collaboration_mode,
                    snapshot.collaboration_instructions,
                    personality=snapshot.personality,
                ),
            )
            self.settings = candidate
            return snapshot

        task = asyncio.create_task(commit(), name="corki-thread-settings-commit")
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # A joined SQLite commit may have succeeded despite cancellation.
            # Complete in-memory publication before releasing admission/close.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled() and task.exception() is not None:
                logging.getLogger(__name__).warning(
                    "Thread settings commit failed during cancellation", exc_info=task.exception()
                )
            raise
