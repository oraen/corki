"""Cached access to prompt templates bundled with Corki."""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath
from threading import RLock

from corki.prompting.models import PromptTemplate
from corki.prompting.renderer import (
    InvalidPromptTemplateError,
    MissingPromptVariablesError,
    parse_template,
    render_template,
)

_PACKAGED_TEMPLATE_DIRECTORY = "_prompt_templates"
_SOURCE_TEMPLATE_DIRECTORY = "prompts"


class InvalidPromptNameError(ValueError):
    """Raised when a logical prompt name could escape the template root."""


class PromptNotFoundError(FileNotFoundError):
    """Raised when a requested prompt resource does not exist."""


class PromptStore:
    """Load, cache, and render Markdown prompt resources.

    Names are POSIX-style logical paths without a required ``.md`` suffix, for
    example ``agent/base``.  The store caches parsed source templates only;
    rendered text is intentionally not cached because variables vary by turn.
    """

    def __init__(self, root: Traversable | None = None) -> None:
        self._root = root or _default_prompt_root()
        self._cache: dict[str, PromptTemplate] = {}
        self._lock = RLock()

    def load(self, template_name: str) -> PromptTemplate:
        """Return one parsed template, reading it at most once per store."""

        normalized_name = _normalize_prompt_name(template_name)
        with self._lock:
            cached = self._cache.get(normalized_name)
            if cached is not None:
                return cached

            resource = self._root.joinpath(*normalized_name.split("/"))
            if not resource.is_file():
                raise PromptNotFoundError(f"prompt template not found: {normalized_name}")

            template = parse_template(normalized_name, resource.read_text(encoding="utf-8"))
            self._cache[normalized_name] = template
            return template

    def render(self, template_name: str, **variables: str) -> str:
        """Render one template with strict ``#{variable}`` substitution."""

        return render_template(self.load(template_name), variables)

    def clear_cache(self) -> None:
        """Forget loaded source templates, primarily for tests and development."""

        with self._lock:
            self._cache.clear()


def _normalize_prompt_name(name: str) -> str:
    """Convert a logical prompt name into a safe Markdown resource path."""

    if not name or name != name.strip() or "\\" in name or "//" in name or name.endswith("/"):
        raise InvalidPromptNameError(f"invalid prompt template name: {name!r}")

    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InvalidPromptNameError(f"invalid prompt template name: {name!r}")

    if path.suffix and path.suffix != ".md":
        raise InvalidPromptNameError("prompt templates must use the .md suffix")
    if not path.suffix:
        path = path.with_suffix(".md")
    return path.as_posix()


def _default_prompt_root() -> Traversable:
    """Resolve packaged resources first and the source tree during development."""

    packaged_root = resources.files("corki").joinpath(_PACKAGED_TEMPLATE_DIRECTORY)
    if packaged_root.is_dir():
        return packaged_root

    # ``store.py`` is ``<repo>/src/corki/prompting/store.py`` in a checkout.
    source_root = Path(__file__).resolve().parents[3] / _SOURCE_TEMPLATE_DIRECTORY
    if source_root.is_dir():
        return source_root

    raise PromptNotFoundError(
        "Corki prompt resources are missing from both the installed package and source tree"
    )


__all__ = [
    "InvalidPromptNameError",
    "InvalidPromptTemplateError",
    "MissingPromptVariablesError",
    "PromptNotFoundError",
    "PromptStore",
]
