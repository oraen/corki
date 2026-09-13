"""Owned live settings publication, separate from admission and captured Steps."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from corki.config import CorkiSettings
from corki.core.model_settings import capture_model_settings
from corki.protocol.context import ModelContextInfo
from corki.protocol.settings import (
    UNSET,
    ModelSettingsSnapshot,
    TurnSettingsUpdateResult,
    UnsetSetting,
)

if TYPE_CHECKING:
    from corki.core.graph import CorkiGraph
    from corki.core.turn_run import TurnRun

ModelMetadataResolver = Callable[[CorkiSettings, str], Awaitable[ModelContextInfo]]


async def resolve_model_metadata(settings: CorkiSettings, model: str) -> ModelContextInfo:
    """Static-catalog host path; remote catalog hosts may inject an async resolver."""
    return replace(settings, _admitted_model_info=None).model_context_info(model)


@dataclass(slots=True)
class StepSettingsState:
    """Process-local publication owner and bounded cache; snapshots are durable."""

    initial: ModelSettingsSnapshot
    current: ModelSettingsSnapshot
    view_snapshot: ModelSettingsSnapshot | None = None
    view: "CorkiGraph | None" = None


def check_model_authority(
    initial: ModelSettingsSnapshot,
    current: ModelSettingsSnapshot,
    destination: ModelSettingsSnapshot,
) -> None:
    """Native legacy guard for the current unmanaged, fixed full-access host path.

    Approval configuration/reviewer activation is not exposed by this host. The
    checks below do not implement managed authorization or Guardian execution.
    """
    snapshots = (initial, current, destination)
    if any(snapshot.model_info.used_fallback_model_metadata is not False for snapshot in snapshots):
        raise ValueError("model activation requires non-fallback metadata with recorded provenance")
    authorities = tuple(snapshot.model_info.activation_authority for snapshot in snapshots)
    if any(authority is None for authority in authorities):
        raise ValueError("model activation authority was not recorded")
    admitted, retained, selected = authorities
    assert admitted is not None and retained is not None and selected is not None
    keys = ["cyber", "computer_use_review_required", "guardian", "node_repl_disabled", "reviewer"]
    # Native defaults: GuardianApproval=true, GuardianV2=false. A model-owned
    # guardian policy activates the V2 equality guard even without the V2 flag.
    if selected.guardian is not None:
        keys.append("guardian_v2")
    if selected.reviewer is None:
        keys.extend(("node_policy", "policy", "policy_template"))
    for key in keys:
        if any(getattr(value, key) != getattr(selected, key) for value in (admitted, retained)):
            raise ValueError(f"the destination changes admitted model authority: {key}")


class TurnSettingsController:
    """Serialize sparse patches through resolution and join all owned preparation."""

    def __init__(
        self,
        *,
        settings: Callable[[], CorkiSettings],
        active: Callable[[], "TurnRun | None"],
        closed: Callable[[], bool],
        lifecycle_lock: asyncio.Lock,
        resolver: ModelMetadataResolver = resolve_model_metadata,
    ) -> None:
        self._settings, self._active, self._closed = settings, active, closed
        self._lifecycle_lock = lifecycle_lock
        self._resolver = resolver
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[TurnSettingsUpdateResult]] = set()

    async def update(
        self,
        turn_id: str,
        *,
        model: str | None = None,
        reasoning_effort: str | None | UnsetSetting = UNSET,
        reasoning_summary: str | None = None,
        service_tier: str | None | UnsetSetting = UNSET,
    ) -> TurnSettingsUpdateResult:
        """An update owns only its child task, never the caller's surrounding work."""
        if not self._settings().step_model_switching:
            return TurnSettingsUpdateResult(
                "rejected", "turn settings updates require step_model_switching"
            )
        if self._closed():
            return TurnSettingsUpdateResult("target_unavailable")
        changes = {}
        if model is not None:
            changes["model"] = model
        if reasoning_effort is not UNSET:
            changes["reasoning_effort"] = reasoning_effort
        if reasoning_summary is not None:
            changes["reasoning_summary"] = reasoning_summary
        if service_tier is not UNSET:
            changes["service_tier"] = (
                "default"
                if service_tier is None
                else "priority"
                if service_tier == "fast"
                else service_tier
            )
        task = asyncio.create_task(self._apply(turn_id, changes), name="corki-turn-settings-update")
        self._tasks.add(task)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            task.cancel()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled() and (error := task.exception()) is not None:
                logging.getLogger(__name__).warning(
                    "Cancelled model resolution cleanup failed", exc_info=error
                )
            raise
        finally:
            self._tasks.discard(task)

    async def _apply(self, turn_id: str, changes: dict) -> TurnSettingsUpdateResult:
        async with self._lock:
            run = self._active()
            if not self._available(run) or run.turn_id != turn_id:
                return TurnSettingsUpdateResult("target_unavailable")
            assert run is not None and run.models is not None
            owner, done, current = run.models, run.done, run.models.current
            error = None
            try:
                selected = replace(current, **{k: v for k, v in changes.items() if k != "model"})
                model = changes.get("model", current.model)
                if model != current.model:
                    # Host resources may change while a Turn is active, but
                    # model resolution must retain its admitted personality.
                    host = replace(
                        self._settings(),
                        personality=selected.personality,
                        personality_enabled=selected.personality_enabled,
                    )
                    info = await self._resolver(host, model)
                    if not isinstance(info, ModelContextInfo) or info.model != model:
                        raise ValueError(
                            "model resolver must return metadata for the requested model"
                        )
                    resolved = capture_model_settings(
                        replace(
                            host,
                            model=model,
                            _admitted_model_info=info,
                            reasoning_effort=selected.reasoning_effort,
                            reasoning_summary=selected.reasoning_summary,
                            service_tier=selected.service_tier,
                        )
                    )
                    # Startup config may mask priority when FastMode is disabled;
                    # activation retains the selection and masks only the request.
                    selected = replace(
                        resolved,
                        service_tier=selected.service_tier,
                        collaboration_mode=selected.collaboration_mode,
                        collaboration_instructions=selected.collaboration_instructions,
                    )
            except (ValueError, TypeError) as exc:
                error = str(exc)
            async with self._lifecycle_lock:
                # Availability precedes preparation failure. Never retry against a
                # successor, even one reusing the turn ID or owner object.
                if (
                    not self._available(run)
                    or run.done is not done
                    or run.models is not owner
                    or owner.current is not current
                ):
                    return TurnSettingsUpdateResult("target_unavailable")
                if error is not None:
                    return TurnSettingsUpdateResult("rejected", error)
                try:
                    check_model_authority(owner.initial, current, selected)
                except ValueError as exc:
                    return TurnSettingsUpdateResult("rejected", str(exc))
                owner.current = selected
                return TurnSettingsUpdateResult("applied")

    def _available(self, run: "TurnRun | None") -> bool:
        return (
            not self._closed()
            and run is not None
            and self._active() is run
            and run.models is not None
            and not run.cancel_requested
            and not run.finishing
            and not run.done.is_set()
        )

    async def aclose(self) -> None:
        """Called before taking the publication lock or closing catalog resources."""
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
