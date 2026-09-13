"""Construct one immutable world-state and optional turn-input snapshot per step."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from copy import copy
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from corki.config.instructions import ProjectInstructionsConfig
from corki.config.permissions import ExecutionPermissions
from corki.context import local_time
from corki.context.environment import EnvironmentSnapshot
from corki.context.extension_world_state import WorldStateContributor, WorldStateSection
from corki.context.extensions import (
    AsyncContextContributor,
    AsyncInputContextContributor,
    ContextContributor,
    InitialContextContributor,
    InputContextContributions,
    InputContextContributor,
    ModelWindowContributor,
    SnapshotInputContextContributor,
    StepContextContributor,
    StructuredInputContextContributor,
    ToolSnapshotContributor,
)
from corki.context.instruction_manager import InstructionManager
from corki.context.permissions import PermissionContext
from corki.context.project import inspect_project
from corki.prompting import (
    PromptAssembler,
    PromptContribution,
    PromptPhase,
    PromptRole,
    PromptSlot,
    PromptStore,
)
from corki.protocol.collaboration import ModeKind, validate_mode
from corki.protocol.ids import TurnId
from corki.protocol.items import ContextItem, ContextRole
from corki.protocol.tools import ToolSpec
from corki.shell import default_user_shell


@dataclass(frozen=True, slots=True)
class InitialContext:
    items: tuple[ContextItem, ...]
    section_warnings: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """Current world state plus input-attached fragments, with distinct lifetimes."""

    instructions: str
    items: tuple[ContextItem, ...]
    project_root: Path
    input_items: tuple[ContextItem, ...] = ()
    warnings: tuple[str, ...] = ()
    section_warnings: tuple[tuple[str, str], ...] = ()
    # Transient initial-window ordering; never reorders persisted history/deltas.
    initial_extension_keys: frozenset[str] = frozenset()
    omitted_sections: frozenset[str] = frozenset()
    initial_context_keys: frozenset[str] = frozenset()
    initial_context_loader: Callable[[], Awaitable[InitialContext]] | None = None
    world_state_sections: tuple[WorldStateSection, ...] = ()

    async def for_window(self, *, initial: bool) -> ContextSnapshot:
        if self.initial_context_loader is None:
            return self
        items = tuple(i for i in self.items if i.key not in self.initial_context_keys)
        if not initial:
            return replace(
                self,
                items=items,
                omitted_sections=self.omitted_sections | self.initial_context_keys,
            )
        loaded = await self.initial_context_loader()
        return replace(
            self,
            items=(*loaded.items, *items),
            initial_extension_keys=self.initial_extension_keys | self.initial_context_keys,
            omitted_sections=self.omitted_sections - self.initial_context_keys,
            section_warnings=(
                *(w for w in self.section_warnings if w[0] not in self.initial_context_keys),
                *loaded.section_warnings,
            ),
        )


class ContextBuilder:
    """Assemble prompts, project instructions, environment, and Git facts."""

    def __init__(
        self,
        store: PromptStore | None = None,
        *,
        contributors: tuple[ContextContributor, ...] = (),
        step_contributors: tuple[StepContextContributor, ...] = (),
        source_directory: Path | None = None,
        include_environment_context: bool = True,
        shell_name: str | None = None,
        instruction_manager: InstructionManager | None = None,
        instruction_config: ProjectInstructionsConfig | None = None,
        execution_permissions: ExecutionPermissions | None = None,
        instruction_environment_key: object = None,
    ) -> None:
        self._assembler = PromptAssembler(store or PromptStore())
        self._contributors = contributors
        self._step_contributors = step_contributors
        self._source_directory = source_directory
        self._include_environment_context = include_environment_context
        self._shell_name = default_user_shell().name if shell_name is None else shell_name
        self.instruction_manager = instruction_manager or InstructionManager()
        self._instruction_config = instruction_config or ProjectInstructionsConfig()
        self._execution_permissions = execution_permissions
        self._permission_context = PermissionContext()
        self._permissions_configured = execution_permissions is not None
        self._include_permissions = False
        self._honor_allow_rules = True
        self._permission_messages = None
        self._instruction_environment_key = instruction_environment_key
        self._collaboration_model: str | None = None
        self._model_instructions: str | None = None
        self._personality: str | None = None
        self._personality_template = None
        self._personality_enabled = True
        self._session_base: tuple[str, str] | None = None
        self._base_provenance: str | None = "model"
        self._base_override: str | None = None
        self._managed_instructions = None
        self._managed_instructions_enabled = True

    def with_managed_instructions(self, policy, *, enabled: bool = True) -> ContextBuilder:
        from corki.config.managed_instructions import ManagedDeveloperInstructions

        if policy is not None and not isinstance(policy, ManagedDeveloperInstructions):
            raise ValueError("managed developer instructions must be host-owned")
        builder = copy(self)
        builder._managed_instructions = policy if enabled else None
        builder._managed_instructions_enabled = enabled
        return builder

    def managed_instructions_snapshot(
        self, *, turn_id: TurnId, instructions: str, project_root: Path
    ) -> ContextSnapshot:
        """Read validated host policy without reevaluating unrelated contributors."""
        items = ()
        if self._managed_instructions is not None:
            items = (
                ContextItem(
                    "managed_developer_instructions",
                    ContextRole.DEVELOPER,
                    self._managed_instructions.text,
                    turn_id,
                    content_kind="managed_config.developer_instructions",
                    separate_message=True,
                ),
            )
        return ContextSnapshot(
            instructions=instructions,
            items=items,
            project_root=project_root,
            omitted_sections=(
                frozenset()
                if self._managed_instructions_enabled
                else frozenset({"managed_developer_instructions"})
            ),
        )

    def with_instruction_settings(self, settings) -> ContextBuilder:
        """Capture an admitted view, sharing only the session-owned instruction manager."""
        builder = copy(self)
        builder._instruction_config = settings.project_instructions
        builder._collaboration_model = settings.model
        builder._execution_permissions = settings.execution_permissions
        builder._include_permissions = settings.include_permissions_instructions
        builder._permissions_configured = True
        model_info = settings.model_context_info(settings.model)
        builder._model_instructions = model_info.get_model_instructions(
            settings.personality, personality_enabled=settings.personality_enabled
        )
        builder._personality_enabled = settings.personality_enabled
        builder._personality = settings.personality
        builder._personality_template = model_info.instruction_template
        builder._base_override = settings.base_instructions
        builder._permission_messages = model_info.permission_messages
        authority = model_info.activation_authority
        builder._honor_allow_rules = authority is None or not authority.cyber
        builder._instruction_environment_key = (
            settings.allow_login_shell,
            settings.shell_environment_policy,
        )
        return builder

    def with_execution_rules(self, rules) -> ContextBuilder:
        """Bind the same session rule owner used by shell approval execution."""
        builder = copy(self)
        builder._permission_context = PermissionContext(rules)
        return builder

    async def initialize_instructions(self, cwd: Path):
        """Load before thread startup; later Steps reuse or invalidate this snapshot."""
        return await self.instruction_manager.refresh(
            self._source_directory or cwd,
            self._instruction_config,
            self._execution_permissions,
            self._instruction_environment_key,
        )

    def with_step_contributor(self, contributor: StepContextContributor) -> ContextBuilder:
        """Compose a Runtime-owned view without mutating a caller's shared builder."""
        builder = copy(self)
        builder._step_contributors = (*self._step_contributors, contributor)
        return builder

    def map_contributors(
        self, transform: Callable[[ContextContributor], ContextContributor]
    ) -> ContextBuilder:
        """Prepare a new contributor view, retaining old compiled Turn snapshots."""
        builder = copy(self)
        builder._contributors = tuple(transform(value) for value in self._contributors)
        return builder

    def base_instructions(self) -> str:
        """Read cached base instructions without refreshing world-state contributors."""
        if self._session_base is not None:
            return self._session_base[1]
        return self._assembler.assemble(base_template="agent/base", contributions=()).instructions

    async def with_session_base(self, repository, thread_id, model) -> ContextBuilder:
        ensure = getattr(repository, "ensure_base_instructions", None)
        if ensure is None:
            if self._model_instructions is not None or self._base_override is not None:
                raise ValueError("model instructions require session base-instruction persistence")
            return self
        candidate = (
            self._model_instructions
            if self._model_instructions is not None
            else self.base_instructions()
        )
        if self._base_override is not None:
            saved = await ensure(thread_id, model, self._base_override, provenance="custom")
        else:
            saved = await ensure(thread_id, model, candidate)
        if len(saved) not in (2, 3) or any(not isinstance(value, str) for value in saved[:2]):
            raise ValueError("invalid persisted session base instructions")
        builder = copy(self)
        builder._session_base = tuple(saved[:2])
        builder._base_provenance = saved[2] if len(saved) == 3 else "model"
        if builder._base_provenance not in (None, "model", "custom"):
            raise ValueError("invalid persisted base instruction provenance")
        if self._base_override is not None:
            # Resume overrides are runtime-local; do not rewrite original metadata.
            builder._session_base = (model, self._base_override)
            builder._base_provenance = "custom"
        elif builder._base_provenance is None and saved[1] == candidate:
            # Old metadata may lack origin. Infer only from exact current-model
            # text, never from its historical model label or Custom provenance.
            builder._session_base = (model, saved[1])
            builder._base_provenance = "model"
        return builder

    def with_context_window(self, tokens: int) -> ContextBuilder:
        """Capture model-dependent contributor allocations without changing old Turns."""
        builder = copy(self)
        builder._contributors = tuple(
            contributor.with_context_window(tokens)
            if isinstance(contributor, ModelWindowContributor)
            else contributor
            for contributor in self._contributors
        )
        return builder

    async def build(
        self,
        *,
        cwd: Path,
        turn_id: TurnId,
        user_input: str = "",
        realtime_active: bool = False,
        include_input_context: bool = True,
        defer_initial_context: bool = False,
        tool_specs: tuple[ToolSpec, ...] = (),
        tool_snapshot=None,
        timezone_name: str | None = None,
        input_mentions=(),
        collaboration_mode: ModeKind = "default",
        collaboration_instructions: str | None = None,
    ) -> ContextSnapshot:
        validate_mode(collaboration_mode, collaboration_instructions)
        source_cwd = self._source_directory or cwd
        agents = await self.initialize_instructions(cwd)
        project = await inspect_project(source_cwd)
        contributions = [
            PromptContribution(
                key=f"mode.{collaboration_mode}",
                content_kind="collaboration_mode.instructions",
                template_name=(
                    f"modes/{collaboration_mode}"
                    if collaboration_instructions is None
                    else "modes/custom"
                    if collaboration_instructions
                    else None
                ),
                variables={"instructions": collaboration_instructions or ""},
                snapshot_state=(
                    ""
                    if collaboration_instructions == ""
                    else json.dumps([collaboration_mode, self._collaboration_model])
                ),
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.COLLABORATION_MODE,
            ),
            PromptContribution(
                key="project.snapshot",
                content_kind="corki.project.snapshot",
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
        permissions = None
        if self._permissions_configured:
            permissions = await self._permission_context.snapshot(
                self._execution_permissions,
                cwd,
                honor_allow_rules=self._honor_allow_rules,
                messages=self._permission_messages,
            )
        if self._include_environment_context:
            now = datetime.now().astimezone()
            contributions.append(
                EnvironmentSnapshot(
                    cwd=str(cwd),
                    shell=self._shell_name,
                    current_date=now.date().isoformat(),
                    timezone=local_time.timezone_name() if timezone_name is None else timezone_name,
                    filesystem=permissions.filesystem if permissions is not None else None,
                    network=permissions.network if permissions is not None else None,
                ).contribution()
            )
        if agents.text:
            contributions.append(
                PromptContribution(
                    key="project.agents",
                    content_kind="agents_md.instructions",
                    template_name="context/agents" if agents.project else "context/agents_global",
                    role=PromptRole.USER,
                    slot=PromptSlot.PROJECT_INSTRUCTIONS,
                    variables=(
                        {"directory": str(source_cwd), "instructions": agents.text}
                        if agents.project
                        else {"instructions": agents.text}
                    ),
                )
            )
        warnings: list[str] = list(self.instruction_manager.take_warnings())
        if permissions is not None:
            contributions.append(permissions.contribution(compact=not self._include_permissions))
        initial_extension_keys: set[str] = set()
        initial_contributors = []
        world_state_sections = []
        for contributor in self._contributors:
            if isinstance(contributor, WorldStateContributor):
                sections = await contributor.world_state_contributions(
                    cwd=cwd, user_input=user_input, realtime_active=realtime_active
                )
                for section in sections:
                    if not isinstance(section, WorldStateSection):
                        raise TypeError("invalid extension world-state section")
                    world_state_sections.append(section)
                    contributions.append(
                        PromptContribution(
                            key=section.key,
                            template_name=None,
                            role=PromptRole.DEVELOPER,
                            slot=PromptSlot.WORLD_STATE_EXTENSIONS,
                            snapshot_state=section.state,
                            content_kind=section.key + ".instructions",
                        )
                    )
                if not hasattr(contributor, "contributions") and not isinstance(
                    contributor, AsyncContextContributor
                ):
                    continue
            if isinstance(contributor, InitialContextContributor):
                initial_contributors.append(contributor)
                continue
            contribution_start = len(contributions)
            contributions.extend(
                await contributor.async_contributions(
                    cwd=cwd,
                    user_input=user_input,
                    realtime_active=realtime_active,
                )
                if isinstance(contributor, AsyncContextContributor)
                else contributor.contributions(
                    cwd=cwd,
                    user_input=user_input,
                    realtime_active=realtime_active,
                )
            )
            initial_extension_keys.update(
                contribution.key
                for contribution in contributions[contribution_start:]
                if (
                    contribution.phase is PromptPhase.EXTENSION
                    or (
                        contribution.phase is None
                        and contribution.slot in (PromptSlot.EXTENSIONS, PromptSlot.TURN)
                    )
                )
                and contribution.role is PromptRole.DEVELOPER
                and not contribution.input_scoped
                and not contribution.separate_message
            )
            if include_input_context and isinstance(
                contributor, (InputContextContributor, AsyncInputContextContributor)
            ):
                selected = (
                    await contributor.async_input_contributions(
                        cwd=cwd,
                        user_input=user_input,
                        mentions=input_mentions,
                        tool_snapshot=tool_snapshot,
                    )
                    if isinstance(contributor, AsyncInputContextContributor)
                    else contributor.input_snapshot_contributions(
                        cwd=cwd,
                        user_input=user_input,
                        mentions=input_mentions,
                        tool_snapshot=tool_snapshot,
                    )
                    if isinstance(contributor, SnapshotInputContextContributor)
                    else contributor.input_contributions_with_mentions(
                        cwd=cwd, user_input=user_input, mentions=input_mentions
                    )
                    if input_mentions and isinstance(contributor, StructuredInputContextContributor)
                    else contributor.input_contributions(cwd=cwd, user_input=user_input)
                )
                if isinstance(selected, InputContextContributions):
                    contributions.extend(selected.contributions)
                    warnings.extend(selected.warnings)
                else:
                    contributions.extend(selected)
        for contributor in self._step_contributors:
            contributions.extend(
                contributor.snapshot_contributions(tool_snapshot=tool_snapshot)
                if tool_snapshot is not None and isinstance(contributor, ToolSnapshotContributor)
                else contributor.step_contributions(tool_specs=tool_specs)
            )
        # Native host skills insert before the full permissions section only.
        # Without that section they remain in the late world-state extension area.
        if not (self._permissions_configured and self._include_permissions):
            contributions = [
                replace(contribution, slot=PromptSlot.WORLD_STATE_EXTENSIONS)
                if contribution.slot is PromptSlot.HOST_SKILLS
                else contribution
                for contribution in contributions
            ]
        assembly = self._assembler.assemble(
            base_template="agent/base",
            contributions=contributions,
        )
        items: list[ContextItem] = []
        if self._session_base is not None:
            items.append(
                ContextItem(
                    "model.instructions",
                    ContextRole.DEVELOPER,
                    self._model_instructions or "",
                    turn_id,
                    snapshot_state=json.dumps(
                        {
                            "model": self._collaboration_model,
                            "base_model": self._session_base[0]
                            if self._base_provenance == "model"
                            and self._session_base[1] != self._model_instructions
                            else None,
                        }
                    ),
                    content_kind="model_switch.instructions",
                    separate_message=True,
                )
            )
        if self._collaboration_model is not None and self._personality_enabled:
            template = self._personality_template
            message = (
                template.personality_message(self._personality)
                if template is not None and self._personality is not None
                else None
            )
            items.append(
                ContextItem(
                    "personality",
                    ContextRole.DEVELOPER,
                    message or "",
                    turn_id,
                    content_kind="personality.spec_instructions",
                    separate_message=True,
                    snapshot_state=json.dumps(
                        {
                            "model": self._collaboration_model,
                            "personality": self._personality,
                            "baked": bool(
                                template is not None
                                and template.supports_personality
                                and self.base_instructions() == self._model_instructions
                            ),
                        }
                    ),
                )
            )
        input_items: list[ContextItem] = []
        for fragment in assembly.fragments:
            item = ContextItem(
                key=fragment.key,
                role=(
                    ContextRole.DEVELOPER
                    if fragment.role is PromptRole.DEVELOPER
                    else ContextRole.USER
                ),
                content=(
                    collaboration_instructions
                    if fragment.key == f"mode.{collaboration_mode}"
                    and collaboration_instructions is not None
                    else fragment.content
                ),
                turn_id=turn_id,
                snapshot_state=fragment.snapshot_state,
                content_kind=fragment.content_kind,
                separate_message=fragment.separate_message,
            )
            (input_items if fragment.input_scoped else items).append(item)
        policy = self.managed_instructions_snapshot(
            turn_id=turn_id, instructions=assembly.instructions, project_root=project.root
        )
        items.extend(policy.items)
        initial_keys = [key for c in initial_contributors for key in c.initial_context_keys]
        # Declarations may overlap when one source is disabled. The assembler
        # still rejects two producers actually emitting the same key.
        if set(initial_keys) & {i.key for i in (*items, *input_items)}:
            raise ValueError("duplicate initial context contribution keys")

        async def load_initial_context():
            selected = []
            for contributor in initial_contributors:
                values = (
                    await contributor.async_contributions(
                        cwd=cwd, user_input=user_input, realtime_active=realtime_active
                    )
                    if isinstance(contributor, AsyncContextContributor)
                    else contributor.contributions(
                        cwd=cwd, user_input=user_input, realtime_active=realtime_active
                    )
                )
                if any(
                    value.key not in contributor.initial_context_keys
                    or value.role is not PromptRole.DEVELOPER
                    or value.input_scoped
                    or value.separate_message
                    for value in values
                ):
                    raise ValueError("invalid initial developer context contribution")
                selected.extend(values)
            rendered = self._assembler.assemble(base_template="agent/base", contributions=selected)
            loaded_items = tuple(
                ContextItem(
                    key=fragment.key,
                    role=ContextRole.DEVELOPER,
                    content=fragment.content,
                    turn_id=turn_id,
                    snapshot_state=fragment.snapshot_state,
                    content_kind=fragment.content_kind,
                )
                for fragment in rendered.fragments
            )
            return InitialContext(
                loaded_items,
                tuple((f.key, warning) for f in rendered.fragments for warning in f.warnings),
            )

        snapshot = ContextSnapshot(
            instructions=self.base_instructions()
            if self._session_base is not None
            else assembly.instructions,
            items=tuple(items),
            project_root=project.root,
            initial_extension_keys=frozenset(initial_extension_keys),
            omitted_sections=policy.omitted_sections
            | (frozenset() if self._personality_enabled else frozenset({"personality"})),
            input_items=tuple(input_items),
            warnings=tuple(warnings),
            section_warnings=tuple(
                (fragment.key, message)
                for fragment in assembly.fragments
                for message in fragment.warnings
            ),
            initial_context_keys=frozenset(initial_keys),
            initial_context_loader=load_initial_context if initial_contributors else None,
            world_state_sections=tuple(world_state_sections),
        )
        return snapshot if defer_initial_context else await snapshot.for_window(initial=True)
