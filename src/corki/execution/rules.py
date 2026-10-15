"""Owned rule-file append and live publication after explicit host approval."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from corki.config.exec_policy import ExecPolicySnapshot
from corki.config.permissions import ExecutionPermissions
from corki.execution.owned_process import run_owned
from corki.execution.response import decode_helper_response

_LOG = logging.getLogger(__name__)


class _RuleState:
    """Shared live policy, independently of each session's write destination."""

    def __init__(self) -> None:
        self.prefixes: tuple[tuple[str, ...], ...] = ()
        self.lock = asyncio.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self.loop is not None and self.loop is not loop:
            raise RuntimeError("live exec policy requires its owning event loop")
        self.loop = loop

    def check_loop(self) -> None:
        if self.loop is not None:
            self.bind_loop()


@dataclass(frozen=True, slots=True)
class ExecPolicyHandle:
    """Host-only live inheritance; never serialize this handle into settings/history."""

    snapshot: ExecPolicySnapshot
    _state: _RuleState

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, ExecPolicySnapshot) or not isinstance(
            self._state, _RuleState
        ):
            raise ValueError("live exec policy requires a captured snapshot and owned rule state")


class ExecutionRuleUpdates:
    """Session write destination with optionally shared, cancellation-joined policy state."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self._state = _RuleState()

    @property
    def prefixes(self) -> tuple[tuple[str, ...], ...]:
        self._state.check_loop()
        return self._state.prefixes

    def bind_loop(self) -> None:
        self._state.bind_loop()

    def capture(self, snapshot: ExecPolicySnapshot) -> ExecPolicyHandle:
        self._state.bind_loop()
        return ExecPolicyHandle(snapshot, self._state)

    def inherit(self, handle: ExecPolicyHandle) -> None:
        """Runtime calls only after native config/managed-policy eligibility checks."""
        handle._state.bind_loop()
        self._state = handle._state

    async def persist(
        self,
        permissions: ExecutionPermissions,
        prefix: tuple[str, ...],
        on_warning: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._state.bind_loop()
        writer = asyncio.create_task(
            self._append(permissions, prefix), name="corki-exec-rule-update"
        )
        cancelled = False
        while not writer.done():
            try:
                await asyncio.shield(writer)
            except asyncio.CancelledError:
                cancelled = True
        warning = writer.result()
        if cancelled:
            raise asyncio.CancelledError
        if warning is not None:
            # Native ExecApproval keeps the current approval on append failure.
            # Do not claim persistence, cache a session grant, or retry the write.
            if on_warning is not None:
                await on_warning(warning)
            else:
                _LOG.warning("%s", warning)

    async def _append(
        self, permissions: ExecutionPermissions, prefix: tuple[str, ...]
    ) -> str | None:
        try:
            async with self._state.lock:
                if self.path is None:
                    raise ValueError("host exec policy path is not configured")
                snapshot = permissions.exec_policy_snapshot
                sources = (
                    snapshot.sources
                    if snapshot is not None
                    and snapshot.declared_sources == permissions.exec_policy_sources
                    else permissions.exec_policy_sources
                )
                request = {
                    "path": str(self.path),
                    "prefix": prefix,
                    "current_policy": {
                        "sources": [asdict(source) for source in sources],
                        "approved_prefixes": self.prefixes,
                        "requirements": [
                            {
                                "source": layer.source,
                                "base_dir": str(layer.base_dir)
                                if layer.base_dir is not None
                                else None,
                                "value": json.loads(layer.value_json),
                            }
                            for layer in permissions.requirements
                        ],
                    },
                }
                output = await run_owned(
                    [str(permissions.compiler)],
                    (json.dumps({"append_execpolicy": request}) + "\n").encode(),
                    cwd=permissions.policy_cwd,
                    output_limit=4_000_000,
                )
                result = decode_helper_response(
                    output, expected=dict, error_prefix="execpolicy amendment failed"
                )
                if result.get("execpolicy_amendment_written") is not True:
                    raise ValueError(
                        "compiler did not acknowledge rule persistence; outcome may be unknown"
                    )
                published = result.get("execpolicy_amendment_published")
                if type(published) is not bool:
                    raise ValueError(
                        "compiler did not acknowledge current-policy publication; "
                        "disk outcome may be known but live outcome is unknown"
                    )
                if published and prefix not in self.prefixes:
                    self._state.prefixes = (*self.prefixes, prefix)
        except Exception as error:
            return f"Failed to apply execpolicy amendment: {str(error)[:2000]}"
        return None
