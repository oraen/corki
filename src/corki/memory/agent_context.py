"""Memory evidence is context data, never an explicit skill-invoking user input."""

from corki.prompting import PromptContribution, PromptRole, PromptSlot


class ConsolidationInputs:
    def __init__(self, content: str):
        self.content = content

    def contributions(self, **kwargs) -> tuple[PromptContribution, ...]:
        return (
            PromptContribution(
                key="memory.consolidation.inputs",
                content_kind="corki.memory.consolidation_inputs",
                template_name="memory/consolidation_inputs",
                role=PromptRole.USER,
                slot=PromptSlot.EXTENSIONS,
                variables={"inputs": self.content},
            ),
        )
