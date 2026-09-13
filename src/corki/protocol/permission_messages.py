"""Immutable model-owned permission text, distinct from execution authority."""

from dataclasses import dataclass, fields

_APPROVALS = ("on_request", "on_request_auto_review", "never", "unless_trusted")
_PERMISSIONS = ("danger_full_access", "workspace_write", "read_only")


@dataclass(frozen=True, slots=True)
class ModelPermissionMessages:
    """None uses the built-in section; an empty string suppresses that section."""

    on_request: str | None = None
    on_request_auto_review: str | None = None
    never: str | None = None
    unless_trusted: str | None = None
    danger_full_access: str | None = None
    workspace_write: str | None = None
    read_only: str | None = None

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"model permission message {field.name} must be a string or None")

    @classmethod
    def from_catalog(cls, messages: object) -> "ModelPermissionMessages | None":
        """Project only known native sections without normalizing empty text away."""
        if messages is None:
            return None
        if not isinstance(messages, dict):
            raise ValueError("model_messages must be a table")
        selected = {}
        for section, names in (("approvals", _APPROVALS), ("permissions", _PERMISSIONS)):
            value = messages.get(section)
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError(f"model_messages.{section} must be a table")
            selected.update({name: value.get(name) for name in names})
        return cls(**selected) if selected else None

    def native_fields(self) -> dict:
        """Return the pinned renderer's two typed message objects."""
        return {
            "approval_messages": {name: getattr(self, name) for name in _APPROVALS},
            "permission_messages": {name: getattr(self, name) for name in _PERMISSIONS},
        }
