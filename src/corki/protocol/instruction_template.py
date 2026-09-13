"""Bounded, local model instruction templates; no provider transport behavior."""

from dataclasses import dataclass

_PLACEHOLDER = "{{ personality }}"
_MAX_BYTES = 30_000


@dataclass(frozen=True, slots=True)
class ModelInstructionTemplate:
    template: str | None = None
    variables_present: bool = False
    personality_default: str | None = None
    personality_friendly: str | None = None
    personality_pragmatic: str | None = None

    def __post_init__(self) -> None:
        if type(self.variables_present) is not bool:
            raise ValueError("instruction template variables_present must be a bool")
        for value in (
            self.template,
            self.personality_default,
            self.personality_friendly,
            self.personality_pragmatic,
        ):
            if value is not None and (
                not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_BYTES
            ):
                raise ValueError("instruction template values must be at most 30000 UTF-8 bytes")
        for personality in (None, "none", "friendly", "pragmatic"):
            self.render(personality)

    @classmethod
    def from_catalog(cls, messages: object) -> "ModelInstructionTemplate | None":
        if messages is None:
            return None
        if not isinstance(messages, dict):
            raise ValueError("model_messages must be a table")
        if not {"instructions_template", "instructions_variables"}.intersection(messages):
            return None  # Legacy permission-only metadata does not replace the base.
        variables = messages.get("instructions_variables")
        if variables is not None and not isinstance(variables, dict):
            raise ValueError("instructions_variables must be a table")
        values = variables or {}
        return cls(
            template=messages.get("instructions_template"),
            variables_present=variables is not None,
            personality_default=values.get("personality_default"),
            personality_friendly=values.get("personality_friendly"),
            personality_pragmatic=values.get("personality_pragmatic"),
        )

    @property
    def supports_personality(self) -> bool:
        return (
            self.template is not None
            and _PLACEHOLDER in self.template
            and self.variables_present
            and all(
                value is not None
                for value in (
                    self.personality_default,
                    self.personality_friendly,
                    self.personality_pragmatic,
                )
            )
        )

    def personality_message(self, personality: str | None) -> str | None:
        if personality not in (None, "none", "friendly", "pragmatic"):
            raise ValueError("invalid personality")
        if not self.variables_present:
            return None
        if personality == "none":
            return ""
        return getattr(self, f"personality_{personality or 'default'}")

    def render(self, personality: str | None = None, *, enabled: bool = True) -> str:
        message = self.personality_message(personality if enabled else None) or ""
        template = self.template or ""
        if enabled and personality == "none":
            template = _strip_personality_section(template)
        if not self.variables_present and enabled:
            return template
        count = template.count(_PLACEHOLDER)
        size = len(template.encode("utf-8")) + count * (
            len(message.encode("utf-8")) - len(_PLACEHOLDER)
        )
        if size > _MAX_BYTES:
            raise ValueError("rendered instruction template exceeds 30000 UTF-8 bytes")
        return template.replace(_PLACEHOLDER, message)


def _strip_personality_section(template: str) -> str:
    start = None
    offset = 0
    for raw in template.split("\n"):
        line = raw.removesuffix("\r") if offset + len(raw) < len(template) else raw
        if start is not None:
            if line == "#" or line.startswith(("# ", "#\t")):
                return template[:start] + template[offset:]
        elif line == "# Personality":
            start = offset
        offset += len(raw) + 1
    return template if start is None else template[:start]
