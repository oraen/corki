"""Reuse Corki's ephemeral Agent runtime, with a dispatch-side tool whitelist."""

from __future__ import annotations

import tempfile
from contextlib import aclosing
from dataclasses import replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from corki.config.instructions import ProjectInstructionsConfig
from corki.context import estimate_request_tokens
from corki.mcp.elicitation import interactive_requests
from corki.models import ModelError
from corki.models.request_watch import request_observer
from corki.models.types import ModelCompleted
from corki.prompting import PromptStore
from corki.protocol.events import TurnCompleted
from corki.protocol.session_source import SessionSource, SubAgentSource
from corki.tools.registry import ToolRegistry

from .tools import SkillManageTool, revision


class DeniedTool:
    """Hermes dispatch-side whitelist: keep advertised schemas, deny other execution."""

    def __init__(self, spec):
        self.spec = spec

    async def execute(self, call, context):
        from corki.protocol.tools import ToolResult

        return ToolResult(
            call.id,
            call.name,
            "Background review denied this tool. Read with skill_list/skill_read; "
            "change skills with skill_manage. Do not retry this tool.",
            is_error=True,
        )


class ReviewSkillService:
    def __init__(self, service, writer):
        self.service, self.writer = service, writer

    def snapshot(self, *args, **kwargs):
        return self.service.snapshot(self.writer.cwd, **kwargs)

    def read(self, skill, relative_file="SKILL.md"):
        # Same read marks as Hermes skill_view/read_file, shared with this review's writer.
        path = self.writer._target(skill.path.parent, relative_file)
        value = self.writer._read(path)
        if value is None:
            raise FileNotFoundError(path)
        self.writer.reads[str(path.resolve())] = revision(value)
        self.writer.state.record_skill_activity(
            str(skill.path.parent.resolve()), skill.qualified_name
        )
        return value


class FrozenSource:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def load_fork_snapshot(self, thread):
        return self.snapshot


class ReviewModel:
    def __init__(
        self, model, fence, settings, *, prefix=None, record_usage=None, inherited_items=()
    ):
        self.model, self.fence = model, fence
        self.prefix, self.record_usage = prefix, record_usage
        self.inherited_items = inherited_items
        self.requests = self.input_tokens = self.output_tokens = self.estimated_input = 0
        self.max_requests = 16
        self.attempts = 0
        self.compacted = False
        self.budget = min(600_000, max(1, int(settings.main_context_limits.raw_tokens * 0.75)))

    @property
    def capabilities(self):
        return self.model.capabilities

    async def request_byte_budget(self, request):
        checker = getattr(self.model, "request_byte_budget", None)
        if checker is None:
            return None
        return await checker(self._adapt_request(request) if request.tools else request)

    def _adapt_request(self, request):
        summarizing = getattr(request, "purpose", "sampling") == "compaction"
        if self.prefix is not None and not summarizing:
            # Corki's storage fork remaps IDs for database isolation; restore them only
            # on the outbound inherited prefix, as Hermes replays its original snapshot.
            inherited = {
                str(uuid5(NAMESPACE_URL, f"corki:fork:{request.thread_id}:item:{item.id}")): item
                for item in self.inherited_items
            }
            request = replace(
                request,
                items=tuple(inherited.get(str(item.id), item) for item in request.items),
                instructions=self.prefix.instructions,
                context_items=self.prefix.context_items,
                tools=self.prefix.tools,
                thread_id=f"{self.prefix.thread_id}:review"
                if self.compacted
                else self.prefix.thread_id,
                client_metadata=request.client_metadata
                if self.compacted
                else self.prefix.client_metadata,
            )
        return request

    async def stream(self, request):
        self.fence.check()
        summarizing = getattr(request, "purpose", "sampling") == "compaction"
        if summarizing:
            self.compacted = True
        request = self._adapt_request(request)
        tokens = estimate_request_tokens(
            request.instructions, (*request.context_items, *request.items), request.tools
        )
        if not summarizing and (
            self.requests >= self.max_requests
            or (self.budget is not None and self.estimated_input >= self.budget)
        ):
            raise ModelError("自进化复盘已达到调用或输入预算", retryable=False)
        self.requests += not summarizing
        self.attempts += 1
        async with aclosing(self.model.stream(request)) as events:
            async for event in events:
                self.fence.check()
                if isinstance(event, ModelCompleted):
                    self.input_tokens += event.usage.input_tokens
                    self.output_tokens += event.usage.output_tokens
                    self.estimated_input += event.usage.input_tokens or tokens
                    if self.record_usage is not None:
                        await self.record_usage(self.attempts, event.usage)
                yield event

    async def aclose(self):
        pass  # The interactive runtime owns the borrowed transport.


async def review(controller, *, identity, settings, model, repository, thread, fence, job=None):
    from corki.core.runtime import LangGraphRuntime

    fence.check()
    prefix = job.prefix if job is not None else None

    async def record_usage(index, usage):
        from corki.skills.io import run_skill_io
        from corki.storage.usage_ledger import record_external_usage

        from .state import now

        path = getattr(repository, "path", None)
        if path is not None:
            await run_skill_io(
                record_external_usage,
                path,
                step_id=f"review:{identity}:{index}",
                thread_id=thread,
                turn_id=identity,
                created_at=now(),
                provider=settings.provider_id,
                model=settings.model,
                usage=usage,
            )

    routed = getattr(job, "routed", False)
    if routed and job.snapshot is not None:
        from .replay import routed_snapshot

        job.snapshot = routed_snapshot(job.snapshot)
    wrapper = ReviewModel(
        model,
        fence,
        settings,
        prefix=None if routed else prefix,
        record_usage=record_usage,
        inherited_items=(
            tuple({item.id: item for item in (*job.snapshot.items, *prefix.items)}.values())
            if job is not None and job.snapshot is not None and prefix is not None
            else ()
        ),
    )
    if job is not None and job.policy.max_input_tokens is not None:
        wrapper.budget = job.policy.max_input_tokens if job.policy.max_input_tokens > 0 else None
    if getattr(job, "curator", False):
        wrapper.max_requests, wrapper.budget = 9999, None
    if job is not None:
        job.usage = wrapper
    capabilities = getattr(model, "capabilities", None)
    if capabilities is not None and not capabilities.supports_tools:
        return wrapper  # No model request can author skills without tool calls.
    with tempfile.TemporaryDirectory(prefix="corki-evolution-") as temporary:
        root = Path(temporary)
        from corki.skills.tools import SkillListTool, SkillReadTool

        writer = SkillManageTool(
            controller.home,
            controller.service,
            controller.cwd,
            controller.switch,
            fence=fence,
            review=identity,
        )
        service = ReviewSkillService(controller.service, writer)
        allowed = {
            "skill_manage": writer,
            "skill_list": SkillListTool(service),
            "skill_read": SkillReadTool(service),
        }
        for name in getattr(getattr(job, "policy", None), "extra_tools", ()):
            if name not in allowed and (tool := controller.registry.get(name)):
                allowed[name] = tool
        registry = ToolRegistry()
        specs = tuple(t.spec for t in allowed.values())
        if prefix is not None and not routed:
            from corki.code_mode.tools import CodeModeExecTool, CodeModeWaitTool
            from corki.tools.search import ToolSearchTool

            dispatch = job.dispatch
            specs = tuple(
                spec
                for spec in dispatch.specs()
                if not isinstance(
                    dispatch.get(spec.name), (CodeModeExecTool, CodeModeWaitTool, ToolSearchTool)
                )
            )
        for spec in specs:
            tool = allowed.pop(spec.name, None)
            registry.register(tool if tool is not None else DeniedTool(spec))
        child_settings = replace(
            settings,
            working_directory=controller.cwd,
            self_evolution_enabled=False,
            base_instructions=prefix.instructions if prefix is not None and not routed else "",
            project_instructions=ProjectInstructionsConfig(max_bytes=0),
            include_environment_context=False,
            include_permissions_instructions=False,
            skills_enabled=False,
            memories_enabled=False,
            memories_generate=False,
            memories_use=False,
            memories_background_enabled=False,
            memories_dedicated_tools=False,
            plugins_enabled=False,
            plugin_dirs=(),
            mcp_servers=(),
            hooks_enabled=False,
            goals_enabled=False,
            realtime_enabled=False,
        )
        # The root's desktop request watchdog must not observe auxiliary model calls.
        observer = request_observer.set(None)
        interaction = interactive_requests.set(False)
        runtime = None
        try:
            runtime = await LangGraphRuntime.acreate(
                settings=child_settings,
                model=wrapper,
                registry=registry,
                fork_from_thread_id=thread if job is None or job.snapshot is not None else None,
                fork_source_repository=FrozenSource(job.snapshot)
                if job is not None and job.snapshot is not None
                else None
                if job is not None
                else repository,
                ephemeral=True,
                database_path=root / "state/thread.db",
                home_path=root / "home",
                compatibility_home=root,
                session_source=SessionSource.subagent(SubAgentSource("review")),
                load_plugins=False,
                mcp_requirements=controller.requirements,
            )
            from .locks import SkillLockGuard

            runtime._skill_guard = SkillLockGuard(
                controller.home, controller.service, controller.cwd
            )
            terminal = None
            prompt = getattr(job, "prompt", None) or PromptStore().render(
                "extensions/evolution/review"
            )
            prompt += "\nOnly the review's permitted tools may execute; other tools will be denied."
            async with aclosing(runtime.stream(prompt)) as events:
                async for event in events:
                    terminal = event
            if not isinstance(terminal, TurnCompleted):
                raise RuntimeError(getattr(terminal, "error", "自进化复盘未成功完成"))
            fence.check()
        finally:
            try:
                if runtime is not None:
                    await runtime.aclose()
            finally:
                request_observer.reset(observer)
                interactive_requests.reset(interaction)
        return wrapper
