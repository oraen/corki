"""Inputs to project instruction discovery, separate from host user instructions."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectInstructionsConfig:
    """An admitted project's discovery configuration and host-selected trust."""

    max_bytes: int = 32_768
    fallback_filenames: tuple[str, ...] = ()
    root_markers: tuple[str, ...] = (".git",)
    trust_level: str | None = None

    def __post_init__(self) -> None:
        if type(self.max_bytes) is not int or self.max_bytes < 0:
            raise ValueError("project_doc_max_bytes must be a nonnegative integer")
        for field in ("fallback_filenames", "root_markers"):
            values = getattr(self, field)
            if not isinstance(values, (tuple, list)) or any(not isinstance(v, str) for v in values):
                raise ValueError(f"project instruction {field} must be an array of strings")
            object.__setattr__(self, field, tuple(values))
        if self.trust_level not in {None, "trusted", "untrusted"}:
            raise ValueError("project trust must be trusted, untrusted or unknown")

    @classmethod
    def from_document(
        cls, document: dict, *, trust_level: str | None = None
    ) -> "ProjectInstructionsConfig":
        """Read native discovery keys; trust is independently selected by the host."""
        return cls(
            max_bytes=document.get("project_doc_max_bytes", 32_768),
            fallback_filenames=document.get("project_doc_fallback_filenames", ()),
            root_markers=document.get("project_root_markers", (".git",)),
            trust_level=trust_level,
        )
