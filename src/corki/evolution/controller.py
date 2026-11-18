"""Opt-in learning lifecycle: foreground priority, bounded reviews, retained ownership."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import weakref
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from corki.prompting import PromptContribution, PromptPhase, PromptRole, PromptSlot
from corki.protocol.events import ToolCallStarted, TurnCompleted
from corki.skills.io import run_skill_io

from .policy import ReviewPolicy
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
        self.timer = None

    async def enter(self, controller, view):
        self.active[controller] = view
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        # Hermes cancels the owning session's review, not unrelated sessions' reviews.
        await controller.preempt()

    def idle(self, controller, *, delay=None):
        if self.active or self.timer is not None:
            return
        from .maintenance import Curator

        curator = Curator(
            controller.home, controller.service, controller.cwd, controller.switch.document
        )
        if curator.options.get("enabled", True) is False:
            return
        reference = weakref.ref(controller)

        def run():
            self.timer = None
            current = reference()
            if current is not None and not current.closed and not self.active:
                current.maintenance_task = asyncio.create_task(
                    current.maintain(), name="corki-curator"
                )
                _RETAINED_TASKS.add(current.maintenance_task)
                current.maintenance_task.add_done_callback(_RETAINED_TASKS.discard)

        self.timer = asyncio.get_running_loop().call_later(
            curator.number("min_idle_hours", 2) * 3600 if delay is None else delay, run
        )


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
        self.maintenance_task = None
        self.last_settings = settings
        self.hub = None
        self.view = None
        self.views = {}
        self.write_fence = None
        self.service.read_observer = self._skill_read

    def _skill_read(self, skill, path, contents):
        if self.admitted or self.state.path.exists():
            try:
                self.state.record_skill_activity(
                    str(skill.path.parent.resolve()), skill.qualified_name
                )
            except (OSError, sqlite3.Error):
                _LOG.warning("Skill usage telemetry unavailable", exc_info=True)

    async def before_turn(self):
        if self.closed:
            return
        if self.hub is None:
            self.hub = _hub(self.home)
            self.hub.controllers.add(self)
        if self.write_fence:
            self.write_fence.revoke()
        self.admitted = self.switch.enabled()
        self.view = SimpleNamespace(
            admitted=self.admitted,
            steps=0,
            authored=threading.Event(),
            request=None,
            dispatch=None,
            settings=None,
            policy=ReviewPolicy.load(self.switch.document),
        )
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
            and isinstance(event, ToolCallStarted)
            and event.tool_name == "skill_manage"
        ):
            # Hermes tool_executor resets on ANY skill_manage use, not just a successful write.
            view.steps = 0
            view.authored.set()

    def observe_request(self, request, settings=None):
        if self.view and self.view.admitted:
            self.view.steps += 1
            self.view.request = request
            self.view.settings = settings
            self.view.dispatch = self.registry.snapshot()

    async def after_turn(self, terminal, *, settings, repository, model, operation, turn_id):
        view = self.views.pop(str(turn_id), None)
        if self.hub and self.hub.active.get(self) is view:
            self.hub.active.pop(self, None)
        if view is not None and view.admitted:
            settings = view.settings or settings
            self.last_settings = settings
            self.hub.idle(self)
        if (
            self.closed
            or view is None
            or not view.admitted
            or terminal is None
            or operation == "compact"
        ):
            return
        # Durable counters are not a cancellable background review. Otherwise
        # rapid consecutive turns can cancel every increment before it starts.
        due = await run_skill_io(
            self.state.completed_turn,
            str(terminal.thread_id),
            str(terminal.turn_id),
            view.steps,
            authored=view.authored.is_set(),
            interval=view.policy.interval,
        )
        if (
            not due
            or self.closed
            or not view.policy.enabled
            or not isinstance(terminal, TurnCompleted)
            or not terminal.final_answer
        ):
            return
        self.fence = WriteFence(self.switch, enabled=view.admitted)
        snapshot = await repository.load_fork_snapshot(terminal.thread_id)
        job = SimpleNamespace(
            fence=self.fence,
            identity=None,
            usage=None,
            prefix=view.request,
            dispatch=view.dispatch,
            snapshot=replace(snapshot, usage=()),
            policy=view.policy,
            routed=False,
        )
        self.task = asyncio.create_task(
            self._learn(
                terminal,
                settings=settings,
                repository=repository,
                model=model,
                job=job,
            ),
            name="corki-evolution",
        )
        # Retain cleanup ownership even if a foreground waiter times out.
        _RETAINED_TASKS.add(self.task)
        self.task.add_done_callback(_RETAINED_TASKS.discard)

    async def _learn(self, terminal, *, settings, repository, model, job):
        fence = job.fence
        identity, status, usage, error_message = None, "cancelled", None, None
        try:
            # Yield delivery/goal continuation first; never keep the UI waiting on review setup.
            await asyncio.sleep(0)
            fence.check()
            if job.policy.provider not in (None, "", "auto") or job.policy.model not in (
                None,
                "",
                "auto",
            ):
                try:
                    previous_model = (settings.provider_id, settings.model)
                    settings = settings.for_model(
                        job.policy.provider
                        if job.policy.provider not in (None, "", "auto")
                        else settings.provider_id,
                        job.policy.model
                        if job.policy.model not in (None, "", "auto")
                        else settings.model,
                    )
                    job.routed = (settings.provider_id, settings.model) != previous_model
                except ValueError:
                    _LOG.warning(
                        "Self-evolution auxiliary route unavailable; using the parent model"
                    )
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

    async def preempt(self):
        if self.maintenance_task and not self.maintenance_task.done():
            self.maintenance_task.cancel()
            await asyncio.wait((self.maintenance_task,), timeout=2)
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

    async def maintain(self):
        from .maintenance import Curator, consolidate

        curator = None
        try:
            if not self.switch.enabled():
                return
            curator = Curator(self.home, self.service, self.cwd, self.switch.document)
            report = await run_skill_io(curator.run)
            if report is not None and curator.options.get("consolidate", False):
                await consolidate(
                    curator, self.last_settings, self.switch, requirements=self.requirements
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.warning("Self-evolution skill maintenance failed", exc_info=True)
        finally:
            if not self.closed and curator is not None:
                delay = await run_skill_io(curator.next_delay)
                if delay is not None:
                    self.hub.idle(self, delay=delay)

    async def aclose(self):
        self.closed = True
        if self.write_fence:
            self.write_fence.revoke()
        if self.hub:
            self.hub.active.pop(self, None)
            self.hub.controllers.discard(self)
            if not self.hub.controllers and self.hub.timer is not None:
                self.hub.timer.cancel()
                self.hub.timer = None
        await self.preempt()
