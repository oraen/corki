"""Creation-time host instruction providers; never inferred from project ancestry."""

import asyncio
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ParamSpec, Protocol, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")


async def owned_read(function: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """Join a filesystem reader through cancellation before releasing its owner."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
    if cancelled is not None:
        try:
            task.result()
        except Exception as exc:
            raise cancelled from exc
        raise cancelled
    return task.result()


@dataclass(frozen=True, slots=True)
class Instructions:
    """Host-provided text and its independent, absolute provenance path."""

    text: str
    source: Path

    def __post_init__(self) -> None:
        if (
            not isinstance(self.text, str)
            or not isinstance(self.source, Path)
            or not self.source.is_absolute()
        ):
            raise ValueError("user instructions require text and an absolute source path")


@dataclass(frozen=True, slots=True)
class LoadedUserInstructions:
    instructions: Instructions | None = None
    warnings: tuple[str, ...] = ()


class UserInstructionsProvider(Protocol):
    """Load once for a new root Runtime, including cold resume and root fork."""

    async def load_user_instructions(self) -> LoadedUserInstructions: ...


class HomeUserInstructionsProvider:
    """Load override/default with native empty/error fallback and lossy UTF-8."""

    def __init__(self, home: Path) -> None:
        self.home = home.absolute()

    async def load_user_instructions(self) -> LoadedUserInstructions:
        return await owned_read(self._load)

    def _load(self) -> LoadedUserInstructions:
        warnings = []
        for name in ("AGENTS.override.md", "AGENTS.md"):
            path = self.home / name
            try:
                if not stat.S_ISREG(path.stat().st_mode):
                    continue
                text = path.read_bytes().decode("utf-8", "replace").strip()
            except FileNotFoundError:
                continue
            except OSError as exc:
                warnings.append(
                    f"Failed to read global AGENTS.md instructions from `{path}`: {exc}"
                )
                continue
            if text:
                return LoadedUserInstructions(Instructions(text, path), tuple(warnings))
        return LoadedUserInstructions(warnings=tuple(warnings))
