"""Session-owned AGENTS snapshots with explicit environment/trust invalidation."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from corki.config.instructions import ProjectInstructionsConfig
from corki.config.permissions import ExecutionPermissions
from corki.context.project_instructions import ProjectInstruction, load_project_entries
from corki.context.user_instructions import Instructions, UserInstructionsProvider, owned_read


@dataclass(frozen=True, slots=True)
class LoadedInstructions:
    user: Instructions | None
    project: tuple[ProjectInstruction, ...]
    cwd: Path

    @property
    def text(self) -> str:
        project = "\n\n".join(entry.text for entry in self.project)
        if self.user is not None:
            return self.user.text + ("\n\n--- project-doc ---\n\n" + project if project else "")
        return project

    @property
    def sources(self) -> tuple[Path, ...]:
        return (
            *((self.user.source,) if self.user else ()),
            *(entry.source for entry in self.project),
        )


class InstructionManager:
    """Own one user snapshot and cache project reads by selection, never file mtime."""

    def __init__(
        self,
        provider: UserInstructionsProvider | None = None,
        *,
        inherited: Instructions | None = None,
    ) -> None:
        self._provider = provider
        self._user = inherited if inherited is not None and inherited.text.strip() else None
        self._inherited = self._user
        self._initialized = False
        self._lock = asyncio.Lock()
        self._key = None
        self._loaded: LoadedInstructions | None = None
        self._warnings: list[str] = []

    @property
    def user_instructions(self) -> Instructions | None:
        return self._user

    @property
    def sources(self) -> tuple[Path, ...]:
        return self._loaded.sources if self._loaded is not None else ()

    def take_warnings(self) -> tuple[str, ...]:
        warnings, self._warnings = tuple(self._warnings), []
        return warnings

    def discard_startup_snapshot(self) -> None:
        """Roll back unpublished state after the Runtime has joined its startup reader."""
        self._user = self._inherited
        self._initialized = False
        self._key, self._loaded = None, None
        self._warnings.clear()

    async def refresh(
        self,
        cwd: Path,
        config: ProjectInstructionsConfig,
        permissions: ExecutionPermissions | None,
        environment_key: object = None,
    ) -> LoadedInstructions:
        async with self._lock:
            if not self._initialized:
                if self._provider is not None:
                    result = await self._provider.load_user_instructions()
                    self._user = (
                        result.instructions
                        if result.instructions and result.instructions.text.strip()
                        else None
                    )
                    self._warnings.extend(result.warnings)
                self._initialized = True
            key = (cwd, permissions, environment_key, config.trust_level)
            if self._key == key and self._loaded is not None:
                return self._loaded
            # Clear before the new read: a tightened policy must never fall
            # back to the old authorized source list after a failure/cancel.
            self._key, self._loaded = None, None
            project = ()
            if config.trust_level != "untrusted" and config.max_bytes:
                from corki.execution.backend import read_project_instructions

                if permissions is None:
                    try:
                        project = await owned_read(load_project_entries, cwd, config)
                    except OSError as exc:
                        self._warnings.append(f"error trying to find AGENTS.md docs: {exc}")
                else:
                    project, warnings = await read_project_instructions(permissions, cwd, config)
                    self._warnings.extend(warnings)
            loaded = LoadedInstructions(self._user, project, cwd)
            self._key, self._loaded = key, loaded
            return loaded
