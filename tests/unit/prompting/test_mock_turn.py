"""Codex-style request composition demonstrated with one mock coding turn."""

from corki.prompting import (
    PromptAssembler,
    PromptContribution,
    PromptRole,
    PromptSlot,
    PromptStore,
)


def test_mock_default_coding_turn_uses_expected_prompt_layers() -> None:
    """Mirror the model-visible layers Codex emits for an ordinary first turn."""

    store = PromptStore()
    assembly = PromptAssembler(store).assemble(
        base_template="agent/base",
        contributions=[
            PromptContribution(
                key="mode.default",
                template_name="modes/default",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.COLLABORATION_MODE,
            ),
            PromptContribution(
                key="project.agents",
                template_name="context/agents",
                role=PromptRole.USER,
                slot=PromptSlot.PROJECT_INSTRUCTIONS,
                variables={
                    "directory": "/workspace/corki",
                    "instructions": "Run pytest before finishing.",
                },
            ),
            PromptContribution(
                key="environment.primary",
                template_name="context/environment",
                role=PromptRole.USER,
                slot=PromptSlot.ENVIRONMENT,
                variables={
                    "cwd": "/workspace/corki",
                    "shell": "zsh",
                    "current_date": "2026-09-05",
                    "timezone": "Asia/Shanghai",
                },
            ),
        ],
    )
    question = "请给这个项目增加一个健康检查命令"
    request = {
        "instructions": assembly.instructions,
        "input": [
            *(
                {
                    "role": fragment.role,
                    "content": fragment.content,
                    "prompt": fragment.key,
                }
                for fragment in assembly.fragments
            ),
            {"role": PromptRole.USER, "content": question, "prompt": None},
        ],
        # Just as in Codex, tool schemas are a separate request field rather
        # than text copied into one of the Markdown prompt templates.
        "tools": [
            {"type": "function", "name": "exec_command"},
            {"type": "function", "name": "apply_patch"},
        ],
    }

    assert request["instructions"].startswith("You are Corki")
    assert [(item["role"], item["prompt"]) for item in request["input"]] == [
        (PromptRole.DEVELOPER, "mode.default"),
        (PromptRole.USER, "project.agents"),
        (PromptRole.USER, "environment.primary"),
        (PromptRole.USER, None),
    ]
    assert request["input"][-1]["content"] == question
    assert [tool["name"] for tool in request["tools"]] == ["exec_command", "apply_patch"]
