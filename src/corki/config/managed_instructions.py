"""Source-bearing developer policy supplied exclusively by the embedding host."""

from dataclasses import dataclass

REPLACEMENT = (
    "These managed developer instructions replace all previously provided "
    "managed developer instructions."
)
REMOVAL = "The previously provided managed developer instructions no longer apply."


def render(instructions: str) -> str:
    return f"<managed_developer_instructions>\n{instructions}\n</managed_developer_instructions>"


@dataclass(frozen=True, slots=True)
class ManagedDeveloperInstructions:
    source: str
    text: str

    def __post_init__(self):
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("managed developer instructions require a source")
        if not isinstance(self.text, str):
            raise ValueError("additional_developer_instructions must be a string")
        if len(render(REPLACEMENT + "\n\n" + self.text).encode("utf-8")) > 40_000:
            raise ValueError(
                f"additional_developer_instructions from {self.source} "
                "exceeds 10000 estimated tokens"
            )
