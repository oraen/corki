"""Interactive application lifecycle and runtime-event rendering."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from corki.cli.commands import CommandAction, CommandDispatcher, CommandResult
from corki.cli.history_pager import HistoryPager
from corki.cli.hook_output import hook_output_lines
from corki.cli.input_owner import (
    CycleModeInput,
    InputInterrupted,
    InputOwner,
    QueuedInput,
    submission_text,
)
from corki.cli.stream_animation import animated_events
from corki.config import CorkiPaths, CorkiSettings
from corki.core import AgentRuntime
from corki.core.model_settings import capture_model_settings
from corki.mcp.elicitation import ElicitationRequest
from corki.protocol.collaboration import CollaborationMode
from corki.protocol.events import (
    AssistantMessageCompleted,
    AssistantMessageInterrupted,
    AssistantReasoningCompleted,
    AssistantReasoningDelta,
    AssistantTextDelta,
    ContextCompacted,
    ContextCompactionStarted,
    HookCompleted,
    HookStarted,
    ModelRetryScheduled,
    PlanUpdated,
    ProposedPlanCompleted,
    ProposedPlanDelta,
    RealtimeInputAccepted,
    RuntimeEvent,
    TokenUsageUpdated,
    ToolCallCompleted,
    ToolCallStarted,
    ToolOutputDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    UserInputRequested,
    WarningEvent,
)
from corki.protocol.items import (
    AssistantMessageItem,
    ConversationItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
)
from corki.protocol.memory import ThreadMemoryMode
from corki.realtime import RealtimeTurnClosedError
from corki.sessions.models import DisplayHistory

_LOG = logging.getLogger(__name__)


class ApplicationUI(Protocol):
    def show_welcome(self) -> None: ...

    def replay_history(self, items: tuple[ConversationItem, ...]) -> None: ...

    async def read_message(self) -> str | QueuedInput | CycleModeInput: ...

    def restore_queued_inputs(self, messages: tuple[str, ...]) -> None: ...

    async def read_elicitation(self, request: ElicitationRequest) -> tuple[str, Any]: ...
    async def read_user_input(self, request: UserInputRequested) -> dict | None: ...

    def begin_assistant_message(self) -> None: ...

    def append_assistant_delta(self, delta: str) -> None: ...

    def end_assistant_message(self) -> None: ...

    async def complete_assistant_message(self, text: str) -> None: ...

    def begin_reasoning(self) -> None: ...

    def append_reasoning_delta(self, delta: str) -> None: ...

    def end_reasoning(self) -> None: ...

    def show_tool_started(self, name: str, arguments_preview: str) -> None: ...

    def show_tool_output(self, output: str) -> None: ...

    def show_tool_completed(self, name: str, *, is_error: bool) -> None: ...

    def show_plan(
        self, plan: tuple[dict[str, str], ...], *, explanation: str | None = None
    ) -> None: ...

    def show_assistant_message(self, message: str, *, is_error: bool = False) -> None: ...

    def show_proposed_plan(self, text: str) -> None: ...

    def begin_proposed_plan(self, item_id: str | None = None) -> None: ...

    async def append_proposed_plan_delta_live(self, delta: str) -> None: ...

    async def complete_proposed_plan(self, text: str) -> None: ...

    def end_proposed_plan(self) -> None: ...

    def show_notice(self, message: str) -> None: ...

    def clear(self) -> None: ...

    def show_goodbye(self) -> None: ...


class CorkiApplication:
    """Coordinate terminal input and typed runtime events."""

    def __init__(
        self,
        settings: CorkiSettings,
        paths: CorkiPaths,
        runtime: AgentRuntime,
        ui: ApplicationUI,
    ) -> None:
        self._runtime = runtime
        self._settings = settings
        self._default_collaboration_settings = capture_model_settings(settings)
        self._ui = ui
        self._commands = CommandDispatcher(settings, paths)
        self._realtime_enabled = settings.realtime_enabled
        self._pending_messages: deque[str] = deque()
        self._queue_autosend = True
        self._turn_cancelled = False
        self._returned_input_ids: set[str] = set()
        self._returned_inputs: list[str] = []
        bind_editor = getattr(ui, "set_queue_editor", None)
        if bind_editor is not None:
            bind_editor(self._pop_queued_input)
        self._input = InputOwner(ui)
        self._replayed_messages = set()
        self._replayed_reasoning = set()
        self._replayed_calls = set()
        self._replayed_results = set()
        self._replayed_answer_turns = set()
        self._history_pager = None
        bind = getattr(runtime, "set_mcp_elicitation_handler", None)
        if bind is not None and hasattr(ui, "read_elicitation"):
            bind(self._handle_elicitation)
        bind_execution = getattr(runtime, "set_execution_approval_handler", None)
        if bind_execution is not None and hasattr(ui, "read_elicitation"):
            bind_execution(self._handle_elicitation)

    async def _handle_elicitation(self, request) -> None:
        action, content = await self._input.elicit(request)
        if request.kind in {"shell_approval", "patch_approval"}:
            scope = content.get("scope") if isinstance(content, dict) else None
            if scope is not None and scope not in {"once", "session", "rule"}:
                raise ValueError("invalid execution approval scope")
            amendment = None
            if scope == "rule" and action == "accept":
                if request.kind == "patch_approval":
                    raise ValueError("patch approval cannot save an execution rule")
                amendment = request.params["_meta"]["execpolicy_amendment"]
                if amendment is None:
                    raise ValueError("no execution rule was proposed")
            self._runtime.respond_execution_approval(
                request.request_id,
                action,
                remember=(
                    scope == "session"
                    or scope is None
                    and isinstance(content, dict)
                    and content.get("remember") is True
                ),
                execpolicy_amendment=amendment,
            )
            return
        self._runtime.respond_mcp_elicitation(
            request.server_name,
            request.request_id,
            action,
            content=content,
        )

    async def _handle_user_input(self, request: UserInputRequested) -> None:
        try:
            response = await self._input.ask_user(request)
            if response is None:
                if self._runtime.cancel_user_input(request.turn_id, request.call_id):
                    self._turn_cancelled = True
            else:
                self._runtime.respond_user_input(request.turn_id, request.call_id, response)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - a failed panel must not strand the tool
            _LOG.warning("Question panel failed: %s", type(error).__name__)
            self._runtime.respond_user_input(request.turn_id, request.call_id, None)

    async def run(self) -> int:
        """Run until Ctrl+C/EOF and close runtime resources deterministically."""

        self._ui.show_welcome()
        watch_resize = getattr(self._ui, "watch_resize", None)
        resize = asyncio.create_task(watch_resize()) if watch_resize is not None else None
        try:
            load_history = getattr(self._runtime, "load_display_history", None)
            load_history = getattr(self._runtime, "load_display_snapshot", None) or load_history
            replay = getattr(self._ui, "replay_history", None)
            if load_history is not None and replay is not None:
                paged = all(
                    callable(getattr(self._runtime, name, None))
                    for name in (
                        "load_display_items_page",
                        "load_display_turns_page",
                        "contains_display_item",
                    )
                ) and callable(getattr(self._ui, "set_history_loader", None))
                if paged:
                    self._history_pager = HistoryPager(self._runtime, self._ui)
                    history = await self._history_pager.initialize()
                else:
                    history = await load_history()
                    replay(history)
                items = history.items if isinstance(history, DisplayHistory) else history
                self._replayed_messages = {
                    i.id for i in items if isinstance(i, AssistantMessageItem)
                }
                self._replayed_reasoning = {i.id for i in items if isinstance(i, ReasoningItem)}
                self._replayed_answer_turns = {
                    i.turn_id for i in items if isinstance(i, AssistantMessageItem)
                }
                self._replayed_calls = {i.call.id for i in items if isinstance(i, ToolCallItem)}
                self._replayed_results = {i.call_id for i in items if isinstance(i, ToolResultItem)}
            if getattr(self._ui, "keep_composer_during_turn", False):
                started = asyncio.Event()
                try:
                    await self._consume_interactive_events(
                        _signal_turn_start(self._runtime.resume_pending(), started),
                        steering_enabled=False,
                        started=started,
                    )
                except asyncio.CancelledError:
                    task = asyncio.current_task()
                    if task is not None:
                        task.uncancel()
                    self._ui.show_notice("Turn interrupted.")
            else:
                await self._consume_events(self._runtime.resume_pending())
            while True:
                cycle_mode = False
                try:
                    if self._pending_messages and self._queue_autosend:
                        message = self._pending_messages.popleft()
                        self._refresh_pending_inputs()
                    else:
                        enable_cycle = getattr(self._ui, "set_mode_cycle_enabled", None)
                        if enable_cycle is not None:
                            enable_cycle(True)
                        try:
                            submission = await self._input.read_message()
                        finally:
                            if enable_cycle is not None:
                                enable_cycle(False)
                        cycle_mode = isinstance(submission, CycleModeInput)
                        message = submission_text(submission)
                except (KeyboardInterrupt, EOFError, InputInterrupted):
                    break
                if not message and not cycle_mode:
                    continue

                command = (
                    CommandResult(handled=True, action=CommandAction.CYCLE_MODE)
                    if cycle_mode
                    else self._commands.dispatch(message)
                )
                if command.handled and command.action not in (
                    CommandAction.COMPACT,
                    CommandAction.PLAN,
                    CommandAction.CYCLE_MODE,
                ):
                    if command.action is CommandAction.CLEAR:
                        self._ui.clear()
                    elif command.action is CommandAction.REALTIME_ON:
                        self._realtime_enabled = True
                        self._commands.set_realtime_enabled(True)
                        if update := getattr(self._ui, "set_live_input_enabled", None):
                            update(True)
                    elif command.action is CommandAction.REALTIME_OFF:
                        self._realtime_enabled = False
                        self._commands.set_realtime_enabled(False)
                        if update := getattr(self._ui, "set_live_input_enabled", None):
                            update(False)
                    elif command.action is CommandAction.MCP_REFRESH:
                        self._runtime.request_mcp_refresh()
                    elif command.action is CommandAction.MEMORY_RESET:
                        await self._reset_memory()
                    elif command.action is CommandAction.MEMORY_RESET_PREVIEW:
                        self._show_memory_reset_targets()
                    elif command.action in (
                        CommandAction.MEMORY_MODE_ENABLED,
                        CommandAction.MEMORY_MODE_DISABLED,
                    ):
                        await self._set_memory_mode(command.action)
                    if command.output:
                        self._ui.show_notice(command.output)
                    continue

                try:
                    if command.action is CommandAction.CYCLE_MODE:
                        if self._runtime.thread_settings.collaboration_mode == "default":
                            await self._enter_plan_mode()
                        else:
                            base = self._default_collaboration_settings
                            await self._set_collaboration_mode(
                                CollaborationMode("default", base.model, base.reasoning_effort)
                            )
                    elif command.action is CommandAction.PLAN:
                        entered_plan = False
                        try:
                            entered_plan = await self._enter_plan_mode()
                        finally:
                            # Until mode publication returns normally, this
                            # command has not handed its body to a Turn.
                            if not entered_plan:
                                self._pending_messages.appendleft(message)
                                self._queue_autosend = False
                                self._refresh_pending_inputs()
                        if not entered_plan:
                            continue
                        if command.input_text:
                            await self._consume_turn(command.input_text)
                    elif command.action is CommandAction.COMPACT:
                        if self._realtime_enabled or getattr(
                            self._ui, "keep_composer_during_turn", False
                        ):
                            await self._consume_realtime_turn(None)
                        else:
                            await self._consume_events(self._runtime.compact())
                    else:
                        await self._consume_turn(message)
                except KeyboardInterrupt:
                    self._ui.show_notice("Turn interrupted.")
                except asyncio.CancelledError:
                    # Python translates SIGINT during asyncio.run into task
                    # cancellation. Clear this cancellation so the composer can
                    # continue, matching Codex's first-Ctrl+C turn cancellation.
                    task = asyncio.current_task()
                    if task is not None:
                        task.uncancel()
                    self._ui.show_notice("Turn interrupted.")
                except Exception as exc:  # noqa: BLE001 - outer process boundary
                    self._ui.show_assistant_message(f"Runtime error: {exc}", is_error=True)
        finally:
            try:
                try:
                    if self._history_pager is not None:
                        await self._history_pager.aclose()
                finally:
                    await _join_realtime_tasks(resize)
            finally:
                await self._runtime.aclose()
                self._ui.show_goodbye()
        return 0

    async def _consume_turn(self, message: str) -> None:
        if not self._realtime_enabled and not getattr(self._ui, "keep_composer_during_turn", False):
            await self._consume_events(self._runtime.stream(message))
            return
        await self._consume_realtime_turn(message)

    def _refresh_pending_inputs(self) -> None:
        display = getattr(self._ui, "set_pending_inputs", None)
        if display is not None:
            display(tuple(self._pending_messages))

    def _pop_queued_input(self) -> str | None:
        if not self._pending_messages:
            return None
        message = self._pending_messages.pop()
        if not self._pending_messages:
            self._queue_autosend = True
        self._refresh_pending_inputs()
        return message

    def _receive_unsubmitted_inputs(self, items) -> None:
        returned = [item for item in items if item.id not in self._returned_input_ids]
        self._returned_input_ids.update(item.id for item in returned)
        # Preserve original input order until the output and input owners join.
        self._returned_inputs.extend(item.content for item in returned)

    async def _enter_plan_mode(self) -> bool:
        return await self._set_collaboration_mode(
            CollaborationMode(
                "plan",
                self._runtime.thread_settings.model,
                self._settings.plan_mode_reasoning_effort or "medium",
            )
        )

    async def _set_collaboration_mode(self, mode: CollaborationMode) -> bool:
        cancelled = None
        try:
            current = self._runtime.thread_settings
            selected = await self._runtime.update_thread_settings(collaboration_mode=mode)
        except asyncio.CancelledError as error:
            # Runtime joins an in-flight settings commit before propagating
            # cancellation. Its resulting snapshot, not cancellation itself,
            # tells us whether the mode was actually published.
            selected = self._runtime.thread_settings
            cancelled = error
        except Exception as error:  # noqa: BLE001 - retain command input on failed publication
            self._ui.show_notice(f"{mode.mode.title()} mode update failed: {error}")
            return False
        if current.collaboration_mode == "default" and selected.collaboration_mode == "plan":
            self._default_collaboration_settings = current
        self._commands.set_model_settings(selected)
        if update := getattr(self._ui, "set_collaboration_mode", None):
            update(selected.collaboration_mode)
        if cancelled is not None:
            raise cancelled
        self._ui.show_notice(f"{selected.collaboration_mode.title()} mode enabled.")
        return True

    async def _reset_memory(self) -> None:
        try:
            roots = await self._runtime.reset_memory()
        except Exception as exc:  # noqa: BLE001 - host operation fails independently of a turn
            self._ui.show_assistant_message(f"Memory reset failed: {exc}", is_error=True)
            return
        self._ui.show_notice(
            f"Reset local memories in {', '.join(str(root) for root in roots)}. "
            "No backup was created; conversations and memory settings were preserved."
        )

    def _show_memory_reset_targets(self) -> None:
        self._ui.show_notice(
            "Memory reset targets: "
            + ", ".join(str(root) for root in self._runtime.memory_reset_targets)
        )

    async def _set_memory_mode(self, action: CommandAction) -> None:
        mode = (
            ThreadMemoryMode.ENABLED
            if action is CommandAction.MEMORY_MODE_ENABLED
            else ThreadMemoryMode.DISABLED
        )
        try:
            await self._runtime.set_thread_memory_mode(mode)
        except Exception as exc:  # noqa: BLE001 - host metadata failure is not a model turn
            self._ui.show_assistant_message(
                f"Thread memory mode update failed: {exc}", is_error=True
            )
            return
        self._ui.show_notice(
            f"Thread memory source mode: {mode.value}. "
            "This controls future source eligibility, not global generation or recall settings. "
            "Existing history and extracted data are retained; this is not erasure."
        )

    async def _consume_realtime_turn(self, message: str | None) -> None:
        """Keep the composer available during ordinary work or standalone compaction."""

        if message is None:
            events = self._runtime.compact()
        elif self._realtime_enabled:
            events = self._runtime.stream(message, realtime=True)
        else:
            events = self._runtime.stream(message)
        await self._consume_interactive_events(
            events, steering_enabled=message is not None and self._realtime_enabled
        )

    async def _consume_interactive_events(
        self,
        events: AsyncIterator[RuntimeEvent],
        *,
        steering_enabled: bool,
        started: asyncio.Event | None = None,
    ) -> None:
        """Own one event consumer and modal-aware reader, including recovered turns."""
        reader: asyncio.Task | None = None
        start_waiter: asyncio.Task | None = None
        self._turn_cancelled = False
        consumer = asyncio.create_task(
            self._consume_events(events),
            name="corki-realtime-output",
        )
        try:
            if started is not None:
                start_waiter = asyncio.create_task(started.wait(), name="corki-resume-start")
                await asyncio.wait((consumer, start_waiter), return_when=asyncio.FIRST_COMPLETED)
            while not consumer.done():
                reader = asyncio.create_task(
                    self._input.read_message(),
                    name="corki-realtime-input",
                )
                done, _ = await asyncio.wait(
                    (consumer, reader),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if consumer in done:
                    if reader.done() and not reader.cancelled():
                        try:
                            completed_input = submission_text(reader.result())
                        except (KeyboardInterrupt, EOFError, InputInterrupted):
                            completed_input = ""
                        if completed_input:
                            self._pending_messages.append(completed_input)
                            self._refresh_pending_inputs()
                    else:
                        reader.cancel()
                    break
                try:
                    submission = reader.result()
                    steering = submission_text(submission)
                except (KeyboardInterrupt, EOFError, InputInterrupted):
                    self._turn_cancelled = True
                    await self._runtime.cancel_active()
                    break
                if isinstance(submission, QueuedInput):
                    if steering:
                        self._pending_messages.append(steering)
                        self._refresh_pending_inputs()
                        self._ui.show_notice("Queued for the next turn.")
                    continue
                if self._commands.dispatch(steering).action is CommandAction.PLAN:
                    self._ui.show_notice("/plan is unavailable while a task is running.")
                    continue
                if steering == "/stop":
                    self._turn_cancelled = True
                    await self._runtime.cancel_active()
                    break
                if steering.lower().split()[:1] == ["/memory"]:
                    command = self._commands.dispatch(steering)
                    if command.action is CommandAction.MEMORY_RESET:
                        await self._reset_memory()
                    elif command.action is CommandAction.MEMORY_RESET_PREVIEW:
                        self._show_memory_reset_targets()
                    elif command.action in (
                        CommandAction.MEMORY_MODE_ENABLED,
                        CommandAction.MEMORY_MODE_DISABLED,
                    ):
                        await self._set_memory_mode(command.action)
                    if command.output:
                        self._ui.show_notice(command.output)
                    continue
                if steering.lower() == "/compact":
                    self._ui.show_assistant_message(
                        "'/compact' is disabled while a task is in progress.", is_error=True
                    )
                    continue
                if steering:
                    if not steering_enabled:
                        # Compaction and non-realtime turns cannot accept steering.
                        # The existing input owner keeps it as a later user turn.
                        self._pending_messages.append(steering)
                        self._refresh_pending_inputs()
                        self._ui.show_notice("Queued for the next turn.")
                        continue
                    try:
                        await self._runtime.steer(steering)
                    except RealtimeTurnClosedError:
                        # The task can close input before its terminal event is
                        # delivered. Keep this submission for the next turn.
                        self._pending_messages.append(steering)
                        self._refresh_pending_inputs()
                        break
            await asyncio.shield(consumer)
        except asyncio.CancelledError:
            self._turn_cancelled = True
            await self._runtime.cancel_active()
            raise
        finally:
            try:
                await _join_realtime_tasks(consumer, reader, start_waiter, input_task=reader)
            except asyncio.CancelledError:
                self._turn_cancelled = True
                raise
            finally:
                take = getattr(self._runtime, "take_unsubmitted_inputs", None)
                if take is not None:
                    returned = take()
                    if returned:
                        self._receive_unsubmitted_inputs(returned)
                        self._turn_cancelled = True
                restore = (*self._returned_inputs, *self._pending_messages)
                if self._turn_cancelled and restore:
                    # A broken renderer must not resume queued work or mask cancellation.
                    primary = sys.exception()
                    self._queue_autosend = False
                    try:
                        self._ui.restore_queued_inputs(restore)
                        self._returned_inputs.clear()
                        self._pending_messages.clear()
                        self._queue_autosend = True
                        self._refresh_pending_inputs()
                    except Exception as exc:  # noqa: BLE001 - preserve ownership cleanup failure
                        # Failed draft restoration stays recoverable via queue editing,
                        # with automatic submission paused (including replacement).
                        self._pending_messages.extendleft(reversed(self._returned_inputs))
                        self._returned_inputs.clear()
                        if primary is None:
                            raise
                        _LOG.warning("Input restore failed: %s", type(exc).__name__)

    async def _consume_events(self, events: AsyncIterator[RuntimeEvent]) -> None:
        try:
            await self._render_events(events)
        finally:
            # A renderer exception must close the runtime generator now, not at GC.
            close = getattr(events, "aclose", None)
            if close is not None:
                # The renderer owns this cleanup even if cancellation is repeated.
                closing = asyncio.ensure_future(close())
                cancelled = isinstance(sys.exception(), asyncio.CancelledError)
                while not closing.done():
                    try:
                        await asyncio.shield(closing)
                    except asyncio.CancelledError:
                        cancelled = True
                    except Exception:
                        break
                if cancelled:
                    if not closing.cancelled() and closing.exception() is not None:
                        _LOG.warning(
                            "Event iterator cleanup failed: %s", type(closing.exception()).__name__
                        )
                    raise asyncio.CancelledError
                closing.result()

    async def _render_events(self, events: AsyncIterator[RuntimeEvent]) -> None:
        state = _DisplayStreams()
        enable_animation = getattr(self._ui, "enable_stream_animation", None)
        animated = bool(enable_animation and enable_animation())
        displayed = animated_events(events, self._ui) if animated else events
        try:
            await self._render_events_owned(displayed, state)
        finally:
            primary = sys.exception()
            failure = None
            clear_hooks = getattr(self._ui, "clear_hooks", None)
            if clear_hooks is not None:
                try:
                    clear_hooks()
                except Exception as error:
                    failure = error
            if state.plan_streaming:
                try:
                    self._ui.end_proposed_plan()
                except BaseException as error:
                    failure = error
            if animated:
                try:
                    await displayed.aclose()
                except BaseException as error:
                    failure = error
                try:
                    await self._ui.finish_stream_animation()
                except BaseException as error:
                    failure = failure or error
            try:
                await _join_realtime_tasks(*state.questions.values())
            except BaseException as error:
                failure = error
            for active, method in (
                (state.reasoning, "end_reasoning"),
                (state.streaming, "end_assistant_message"),
            ):
                if active:
                    try:
                        getattr(self._ui, method)()
                    except Exception as exc:  # noqa: BLE001 - preserve the original stream failure
                        failure = failure or exc
                        _LOG.warning("Display stream cleanup failed: %s", type(exc).__name__)
            try:
                self._close_tool_displays(state)
            except Exception as exc:  # noqa: BLE001 - cleanup cannot replace an active exception
                failure = failure or exc
            if primary is None and failure is not None:
                raise failure

    async def _end_assistant_stream(self):
        if getattr(self._ui, "_animation_enabled", False):
            await self._ui.commit_stream_tick(finish=True)
        self._ui.end_assistant_message()

    def _close_tool_displays(self, state: _DisplayStreams) -> None:
        failure = None
        try:
            self._flush_tool_displays(state)
        except Exception as exc:  # noqa: BLE001 - still close every displayed tool
            failure = exc
        pending, state.tools = state.tools, {}
        for name in pending.values():
            try:
                self._ui.show_notice(f"{name} interrupted; completion not confirmed.")
            except Exception as exc:  # noqa: BLE001 - attempt every outstanding display
                failure = failure or exc
                _LOG.warning("Tool display cleanup failed: %s", type(exc).__name__)
        if failure is not None:
            raise failure

    def _flush_tool_displays(self, state: _DisplayStreams) -> None:
        """Drain lifecycle events in FIFO order, including during stream cleanup."""
        failure = None
        while state.deferred_tools:
            event = state.deferred_tools.popleft()
            try:
                if isinstance(event, ToolCallStarted):
                    state.tools[event.tool_call_id] = event.tool_name
                    self._ui.show_tool_started(event.tool_name, event.arguments_preview)
                elif isinstance(event, ToolOutputDelta):
                    self._ui.show_tool_output(event.delta)
                elif isinstance(event, ToolCallCompleted):
                    state.tools.pop(event.tool_call_id, None)
                    self._ui.show_tool_completed(event.tool_name, is_error=event.is_error)
                elif isinstance(event, PlanUpdated):
                    self._ui.show_plan(event.plan, explanation=event.explanation)
                elif isinstance(event, ProposedPlanCompleted):
                    self._ui.show_proposed_plan(event.text)
            except Exception as exc:  # noqa: BLE001 - one renderer fault cannot strand the queue
                failure = failure or exc
        if failure is not None:
            raise failure

    async def _render_events_owned(
        self, events: AsyncIterator[RuntimeEvent], state: _DisplayStreams
    ) -> None:
        saw_text = False
        recovering = False
        async for event in events:
            if isinstance(event, (TurnFailed, TurnCancelled, TurnCompleted)):
                clear_hooks = getattr(self._ui, "clear_hooks", None)
                if clear_hooks is not None:
                    clear_hooks()
            if state.plan_streaming and isinstance(
                event, (AssistantMessageInterrupted, TurnFailed, TurnCancelled, TurnCompleted)
            ):
                self._ui.end_proposed_plan()
                state.plan_streaming = False
            if isinstance(event, ToolCallCompleted):
                question = state.questions.get(event.tool_call_id)
                if question is not None and not question.done():
                    question.cancel()
            pager = getattr(self, "_history_pager", None)
            if pager is not None and isinstance(event, TurnStarted):
                recovering = event.resumed
                pager.identities.clear()
                if not recovering:
                    self._replayed_messages.clear()
                    self._replayed_reasoning.clear()
                    self._replayed_calls.clear()
                    self._replayed_results.clear()
                    self._replayed_answer_turns.clear()
            if pager is not None and recovering:
                if isinstance(event, (AssistantReasoningDelta, AssistantReasoningCompleted)):
                    if await pager.contains("reasoning", event.item_id):
                        self._replayed_reasoning.add(event.item_id)
                elif isinstance(
                    event,
                    (
                        AssistantTextDelta,
                        AssistantMessageCompleted,
                        ProposedPlanCompleted,
                        ProposedPlanDelta,
                    ),
                ):
                    if await pager.contains("assistant_message", event.item_id):
                        self._replayed_messages.add(event.item_id)
                        self._replayed_answer_turns.add(event.turn_id)
                elif isinstance(
                    event, (ToolCallStarted, ToolOutputDelta, ToolCallCompleted, PlanUpdated)
                ):
                    if await pager.contains("tool_result", event.tool_call_id):
                        self._replayed_results.add(event.tool_call_id)
                    if isinstance(event, ToolCallStarted) and await pager.contains(
                        "tool_call", event.tool_call_id
                    ):
                        self._replayed_calls.add(event.tool_call_id)
                elif isinstance(event, TurnCompleted) and await pager.contains(
                    "assistant_turn", event.turn_id
                ):
                    self._replayed_answer_turns.add(event.turn_id)
            if isinstance(event, (AssistantReasoningDelta, AssistantReasoningCompleted)) and (
                event.item_id is not None
                and event.item_id in getattr(self, "_replayed_reasoning", ())
            ):
                continue
            if isinstance(
                event,
                (
                    AssistantTextDelta,
                    AssistantMessageCompleted,
                    ProposedPlanCompleted,
                    ProposedPlanDelta,
                ),
            ) and (
                event.item_id is not None
                and event.item_id in getattr(self, "_replayed_messages", ())
            ):
                saw_text = True
                continue
            if isinstance(event, ToolCallStarted) and event.tool_call_id in getattr(
                self, "_replayed_calls", ()
            ):
                if event.tool_call_id not in getattr(self, "_replayed_results", ()):
                    state.tools[event.tool_call_id] = event.tool_name
                continue
            if isinstance(event, (ToolOutputDelta, ToolCallCompleted, PlanUpdated)) and (
                event.tool_call_id is not None
                and event.tool_call_id in getattr(self, "_replayed_results", ())
            ):
                continue
            if isinstance(event, TurnStarted):
                if event.resumed:
                    self._ui.show_notice("Resuming interrupted turn...")
            elif isinstance(event, UserInputRequested):
                if state.reasoning:
                    self._ui.end_reasoning()
                    state.reasoning = False
                if state.streaming:
                    await self._end_assistant_stream()
                    state.streaming = False
                self._flush_tool_displays(state)
                task = asyncio.create_task(
                    self._handle_user_input(event), name="corki-question-panel"
                )
                state.questions[event.call_id] = task

                def discard_question(done, call_id=event.call_id):
                    if state.questions.get(call_id) is done:
                        state.questions.pop(call_id)

                task.add_done_callback(discard_question)
            elif isinstance(event, AssistantReasoningDelta):
                if event.channel == "raw":
                    continue
                key = (event.item_id, event.section_index)
                if state.reasoning and key != state.reasoning_key:
                    self._ui.end_reasoning()
                    state.reasoning = False
                if not state.reasoning:
                    self._ui.begin_reasoning()
                    state.reasoning = True
                state.reasoning_key = key
                self._ui.append_reasoning_delta(event.delta)
            elif isinstance(event, AssistantReasoningCompleted):
                if state.reasoning and state.reasoning_key[0] in (None, event.item_id):
                    self._ui.end_reasoning()
                    state.reasoning = False
            elif isinstance(event, AssistantTextDelta):
                if state.reasoning:
                    self._ui.end_reasoning()
                    state.reasoning = False
                if not state.streaming:
                    self._ui.begin_assistant_message()
                    state.streaming = True
                append = getattr(self._ui, "append_assistant_delta_live", None)
                if append is None:
                    self._ui.append_assistant_delta(event.delta)
                else:
                    await append(event.delta)
                saw_text = True
            elif isinstance(event, ProposedPlanDelta):
                if state.reasoning:
                    self._ui.end_reasoning()
                    state.reasoning = False
                if not state.plan_streaming:
                    self._ui.begin_proposed_plan(event.item_id)
                    state.plan_streaming = True
                await self._ui.append_proposed_plan_delta_live(event.delta)
            elif isinstance(event, ProposedPlanCompleted):
                saw_text = True
                if state.reasoning:
                    self._ui.end_reasoning()
                    state.reasoning = False
                await self._ui.complete_proposed_plan(event.text)
                state.plan_streaming = False
            elif isinstance(event, AssistantMessageCompleted):
                if state.streaming:
                    complete = getattr(self._ui, "complete_assistant_message", None)
                    if complete is not None:
                        await complete(event.text)
                    else:
                        await self._end_assistant_stream()
                    state.streaming = False
                elif event.text:
                    self._ui.show_assistant_message(event.text)
                saw_text |= bool(event.text)
                self._flush_tool_displays(state)
            elif isinstance(event, AssistantMessageInterrupted):
                if state.reasoning:
                    self._ui.end_reasoning()
                    state.reasoning = False
                if state.streaming:
                    await self._end_assistant_stream()
                    state.streaming = False
                self._flush_tool_displays(state)
                self._ui.show_notice(
                    "Response interrupted; retrying with updated history..."
                    if event.reason == "retry"
                    else "Response interrupted; applying your latest input..."
                )
            elif isinstance(event, RealtimeInputAccepted):
                # The active prompt already leaves submitted text in scrollback,
                # so only acknowledge it instead of echoing it a second time.
                self._ui.show_notice("Steering accepted.")
            elif isinstance(
                event, (ToolCallStarted, ToolOutputDelta, ToolCallCompleted, PlanUpdated)
            ):
                if isinstance(event, ToolCallStarted) and state.reasoning:
                    self._ui.end_reasoning()
                    state.reasoning = False
                state.deferred_tools.append(event)
                if not state.streaming:
                    self._flush_tool_displays(state)
            elif isinstance(event, WarningEvent):
                self._ui.show_notice(f"Warning: {event.message}")
            elif isinstance(event, HookStarted):
                if state.reasoning:
                    self._ui.end_reasoning()
                    state.reasoning = False
                if state.streaming:
                    await self._end_assistant_stream()
                    state.streaming = False
                self._flush_tool_displays(state)
                started = getattr(self._ui, "hook_started", None)
                if started is not None:
                    started(event.run)
            elif isinstance(event, HookCompleted):
                completed = getattr(self._ui, "hook_completed", None)
                if completed is not None:
                    completed(event.run)
                lines = hook_output_lines(event.run)
                if lines:
                    render_hook = getattr(self._ui, "show_hook_output", None)
                    if render_hook is not None:
                        render_hook(event.run)
                    else:
                        self._ui.show_notice("\n".join(lines))
            elif isinstance(event, ModelRetryScheduled):
                limit = event.max_attempts if event.max_attempts is not None else "∞"
                label = "Retrying compaction" if event.purpose == "compaction" else "Reconnecting"
                reason = "".join(
                    char if char.isprintable() or char == "\n" else " "
                    for char in event.error[:4000]
                ).strip()
                self._ui.show_notice(
                    f"{label}... {event.attempt}/{limit} in {event.delay_seconds:g}s"
                    + (f"\nReason: {reason}" if reason else "")
                )
            elif isinstance(event, ContextCompacted):
                self._ui.show_notice(
                    f"Context compacted ({event.estimated_tokens} estimated tokens)."
                )
            elif isinstance(event, ContextCompactionStarted):
                self._ui.show_notice("Compacting context...")
            elif isinstance(event, TokenUsageUpdated):
                continue
            elif isinstance(event, TurnCompleted):
                if state.reasoning:
                    self._ui.end_reasoning()
                    state.reasoning = False
                if state.streaming:
                    await self._end_assistant_stream()
                    state.streaming = False
                self._close_tool_displays(state)
                if (
                    not saw_text
                    and event.final_answer
                    and event.turn_id not in getattr(self, "_replayed_answer_turns", ())
                ):
                    self._ui.show_assistant_message(event.final_answer)
            elif isinstance(event, TurnFailed):
                if state.reasoning:
                    self._ui.end_reasoning()
                    state.reasoning = False
                if state.streaming:
                    await self._end_assistant_stream()
                    state.streaming = False
                self._close_tool_displays(state)
                self._ui.show_assistant_message(event.error, is_error=True)
            elif isinstance(event, TurnCancelled):
                self._turn_cancelled = True
                self._receive_unsubmitted_inputs(event.unsubmitted_inputs)
                if state.reasoning:
                    self._ui.end_reasoning()
                    state.reasoning = False
                if state.streaming:
                    await self._end_assistant_stream()
                    state.streaming = False
                self._close_tool_displays(state)
                self._ui.show_notice("Turn interrupted.")


@dataclass
class _DisplayStreams:
    questions: dict[str, asyncio.Task] = field(default_factory=dict)
    streaming: bool = False
    plan_streaming: bool = False
    reasoning: bool = False
    reasoning_key: tuple[str | None, int | None] = (None, None)
    tools: dict[str, str] = field(default_factory=dict)
    deferred_tools: deque[
        ToolCallStarted | ToolOutputDelta | ToolCallCompleted | PlanUpdated | ProposedPlanCompleted
    ] = field(default_factory=deque)


async def _signal_turn_start(
    events: AsyncIterator[RuntimeEvent], started: asyncio.Event
) -> AsyncIterator[RuntimeEvent]:
    """Observe the existing recovery stream without pulling or replaying it twice."""
    try:
        async for event in events:
            if isinstance(event, TurnStarted):
                started.set()
            yield event
    finally:
        close = getattr(events, "aclose", None)
        if close is not None:
            await close()


async def _join_realtime_tasks(
    *tasks: asyncio.Task | None, input_task: asyncio.Task | None = None
) -> None:
    """Do not let a cancelled prompt outlive the turn or interrupt its cleanup twice."""
    owned = tuple(task for task in tasks if task is not None)
    for task in owned:
        if not task.done() and not task.cancelling():
            task.cancel()
    joined = asyncio.gather(*owned, return_exceptions=True)
    cancelled = False
    while not joined.done():
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError:
            cancelled = True
    for task, result in zip(owned, joined.result(), strict=True):
        if task is input_task and isinstance(result, (EOFError, InputInterrupted)):
            continue
        if isinstance(result, Exception):
            # Preserve the primary turn/cancellation error; do not silently lose cleanup failures.
            _LOG.warning("Realtime task cleanup failed: %s", type(result).__name__)
    if cancelled:
        raise asyncio.CancelledError
