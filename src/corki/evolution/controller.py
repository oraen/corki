"""Opt-in learning lifecycle: foreground priority, bounded reviews, retained ownership."""

from __future__ import annotations

import asyncio
import logging
import threading
import weakref
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

from corki.prompting import PromptContribution, PromptPhase, PromptRole, PromptSlot
from corki.protocol.events import TokenUsageUpdated, TurnCompleted
from corki.skills.io import run_skill_io
from corki.storage.file_lock import open_lock, try_lock, unlock_and_close

from .state import EvolutionState
from .switch import EvolutionSwitch
from .tools import SkillManageTool, WriteFence

_LOG = logging.getLogger(__name__)
_HUBS = weakref.WeakKeyDictionary()
_RETAINED_TASKS = set()


class _Hub:
    def __init__(self):
        self.controllers = weakref.WeakSet()
        self.active = {}

    async def enter(self, controller, view):
        self.active[controller] = view
        # One application's foreground tasks outrank all of its background reviews.
        await asyncio.gather(*(c.preempt() for c in list(self.controllers)))


def _hub(home):
    loop = asyncio.get_running_loop()
    return _HUBS.setdefault(loop, {}).setdefault(str(home), _Hub())


class EvolutionController:
    def __init__(self, *, home, settings, registry, service, requirements):
        self.home = Path(home).resolve()
        self.cwd, self.service, self.registry = settings.working_directory, service, registry
        self.requirements = requirements
        self.switch = EvolutionSwitch(self.home, settings.self_evolution_enabled)
        self.state = EvolutionState(self.home)
        self.owner = registry.create_owner()
        self.admitted = False
        self.closed = False
        self.fence = None
        self.task = None
        self.hub = None
        self.view = None
        self.views = {}
        self.write_fence = None

    async def before_turn(self):
        if self.closed:
            return
        if self.hub is None:
            self.hub = _hub(self.home)
            self.hub.controllers.add(self)
        if self.write_fence:
            self.write_fence.revoke()
        self.admitted = self.switch.enabled()
        self.view = SimpleNamespace(admitted=self.admitted, steps=set(), authored=threading.Event())
        await self.hub.enter(self, self.view)
        tools = ()
        if self.admitted:
            self.write_fence = WriteFence(self.switch, enabled=self.admitted)
            tools = (
                SkillManageTool(
                    self.home,
                    self.service,
                    self.cwd,
                    self.switch,
                    fence=self.write_fence,
                    on_write=self.view.authored.set,
                ),
            )
        self.registry.replace_owned(self.owner, tools)

    def capture(self, turn_id):
        self.views[str(turn_id)] = self.view

    def contributions(self, **kwargs):
        return (
            PromptContribution(
                key="extensions.evolution",
                content_kind="corki.evolution",
                template_name="extensions/evolution/guidance" if self.admitted else None,
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.EXTENSIONS,
                phase=PromptPhase.EXTENSION,
                snapshot_state="on" if self.admitted else "off",
            ),
        )

    def observe(self, event):
        view = self.views.get(str(getattr(event, "turn_id", "")))
        if (
            view is not None
            and view.admitted
            and isinstance(event, TokenUsageUpdated)
            and event.usage_id
            and event.usage_id.startswith("model:")
        ):
            view.steps.add(event.usage_id)

    async def after_turn(
        self, terminal, *, settings, repository, model, operation, turn_id, selection=None
    ):
        view = self.views.pop(str(turn_id), None)
        if self.hub and self.hub.active.get(self) is view:
            self.hub.active.pop(self, None)
        if (
            self.closed
            or view is None
            or not view.admitted
            or not isinstance(terminal, TurnCompleted)
            or operation == "compact"
        ):
            return
        # Durable counters are not a cancellable background review. Otherwise
        # rapid consecutive turns can cancel every increment before it starts.
        due = await run_skill_io(
            self.state.completed_turn,
            str(terminal.thread_id),
            str(terminal.turn_id),
            len(view.steps),
            authored=view.authored.is_set(),
        )
        if not due or self.closed or self.hub.active:
            return
        self.fence = WriteFence(self.switch, enabled=view.admitted)
        job = SimpleNamespace(fence=self.fence, lease=None, identity=None, usage=None)
        self.task = asyncio.create_task(
            self._learn(
                terminal,
                settings=settings,
                repository=repository,
                model=model,
                job=job,
                selection=selection,
            ),
            name="corki-evolution",
        )
        # Retain cleanup ownership even if a foreground waiter times out.
        _RETAINED_TASKS.add(self.task)
        self.task.add_done_callback(_RETAINED_TASKS.discard)

    def _claim(self, job):
        self.state.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        owner = open_lock(self.state.root / "review.lock")
        if not try_lock(owner):
            owner.close()
            return False
        job.lease = owner
        return True

    async def _learn(self, terminal, *, settings, repository, model, job, selection=None):
        fence = job.fence
        identity, status, usage, error_message = None, "cancelled", None, None
        try:
            # Yield delivery/goal continuation first; never keep the UI waiting on review setup.
            await asyncio.sleep(0.1)
            if self.hub.active or not await run_skill_io(self._claim, job):
                return
            fence.check()
            if selection is not None:
                from corki.core.model_settings import bind_model_settings

                settings = bind_model_settings(settings, selection)
            model = model(settings)

            def begin():
                job.identity = self.state.begin_review(
                    str(terminal.thread_id), str(terminal.turn_id), settings
                )
                return job.identity

            identity = await run_skill_io(begin)
            from .worker import review

            work = asyncio.create_task(
                review(
                    self,
                    identity=identity,
                    settings=settings,
                    model=model,
                    repository=repository,
                    thread=terminal.thread_id,
                    fence=fence,
                    job=job,
                ),
                name="corki-evolution-review",
            )
            try:
                usage = await work
                edits = await run_skill_io(self.state.review_edit_count, identity)
                status = "skipped" if usage.requests == 0 else "completed" if edits else "noop"
            finally:
                if not work.done() and not work.cancelling():
                    work.cancel()
                await asyncio.gather(work, return_exceptions=True)
        except asyncio.CancelledError:
            fence.revoke()
        except Exception as error:
            from corki.memory.sanitizer import redact_secrets

            status = "failed"
            error_message = str(error) or type(error).__name__
            if settings.api_key:
                error_message = error_message.replace(settings.api_key, "[REDACTED_SECRET]")
            error_message = redact_secrets(error_message)[:2000]
            _LOG.warning("Self-evolution review failed: %s", error_message)
        finally:
            try:
                if job.identity:
                    with suppress(Exception):
                        usage = usage or job.usage
                        await run_skill_io(
                            self.state.finish_review,
                            job.identity,
                            status,
                            usage.input_tokens if usage else 0,
                            usage.output_tokens if usage else 0,
                            error_message,
                        )
            finally:
                if job.lease:
                    unlock_and_close(job.lease)
                    job.lease = None

    async def preempt(self):
        task = self.task
        if task is None or task.done():
            return
        if self.fence:
            self.fence.revoke()
        if not task.cancelling():
            task.cancel()
        done, _ = await asyncio.wait((task,), timeout=2)
        if not done:
            _LOG.warning("Self-evolution cleanup retained; foreground work continues")

    async def aclose(self):
        self.closed = True
        if self.write_fence:
            self.write_fence.revoke()
        if self.hub:
            self.hub.active.pop(self, None)
            self.hub.controllers.discard(self)
        await self.preempt()
