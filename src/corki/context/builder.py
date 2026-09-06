"""Construct one immutable model-visible context snapshot per turn."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from corki.context.extensions import ContextContributor
from corki.context.instructions import load_project_instructions
from corki.context.project import inspect_project
from corki.prompting import (
    PromptAssembler,
    PromptContribution,
    PromptRole,
    PromptSlot,
    PromptStore,
)
from corki.protocol.ids import TurnId
from corki.protocol.items import ContextItem, ContextRole


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    instructions: str
    items: tuple[ContextItem, ...]
    project_root: Path


class ContextBuilder:
    """Assemble prompts, project instructions, environment, and Git facts."""

    def __init__(
        self,
        store: PromptStore | None = None,
        *,
        contributors: tuple[ContextContributor, ...] = (),
    ) -> None:
        self._assembler = PromptAssembler(store or PromptStore())
        self._contributors = contributors

    async def build(
        self,
        *,
        cwd: Path,
        turn_id: TurnId,
        user_input: str = "",
        realtime_active: bool = False,
    ) -> ContextSnapshot:
        project = await inspect_project(cwd)
        agents = load_project_instructions(project.root, cwd)
        now = datetime.now().astimezone()
        contributions = [
            PromptContribution(
                key="mode.default",
                template_name="modes/default",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.COLLABORATION_MODE,
            ),
            PromptContribution(
                key="environment.primary",
                template_name="context/environment",
                role=PromptRole.USER,
                slot=PromptSlot.ENVIRONMENT,
                variables={
                    "cwd": str(cwd),
                    "shell": Path(os.environ.get("SHELL", "unknown")).name,
                    "current_date": now.date().isoformat(),
                    "timezone": str(now.tzinfo),
                },
            ),
            PromptContribution(
                key="project.snapshot",
                template_name="context/project",
                role=PromptRole.USER,
                slot=PromptSlot.ENVIRONMENT,
                order=10,
                variables={
                    "project_root": str(project.root),
                    "git_branch": project.git_branch,
                    "git_status": project.git_status,
                },
            ),
        ]
        if agents:
            contributions.append(
                PromptContribution(
                    key="project.agents",
                    template_name="context/agents",
                    role=PromptRole.USER,
                    slot=PromptSlot.PROJECT_INSTRUCTIONS,
                    variables={"directory": str(cwd), "instructions": agents},
                )
            )
        for contributor in self._contributors:
            contributions.extend(
                contributor.contributions(
                    cwd=cwd,
                    user_input=user_input,
                    realtime_active=realtime_active,
                )
            )
        assembly = self._assembler.assemble(
            base_template="agent/base",
            contributions=contributions,
        )
        items = tuple(
            ContextItem(
                key=fragment.key,
                role=(
                    ContextRole.DEVELOPER
                    if fragment.role is PromptRole.DEVELOPER
                    else ContextRole.USER
                ),
                content=fragment.content,
                turn_id=turn_id,
            )
            for fragment in assembly.fragments
        )
        return ContextSnapshot(
            instructions=assembly.instructions,
            items=items,
            project_root=project.root,
        )
