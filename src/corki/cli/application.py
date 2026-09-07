"""Interactive application lifecycle and runtime-event rendering."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

from corki.cli.commands import CommandAction, CommandDispatcher
from corki.config import CorkiPaths, CorkiSettings
from corki.core import AgentRuntime
from corki.protocol.events import (
    AssistantMessageCompleted,
    AssistantMessageInterrupted,
    AssistantReasoningDelta,
    AssistantTextDelta,
    ContextCompacted,
    ContextCompactionStarted,
    ModelRetryScheduled,
    PlanUpdated,
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
    WarningEvent,
)
from corki.realtime import RealtimeTurnClosedError


class ApplicationUI(Protocol):
    def show_welcome(self) -> None: ...

    async def read_message(self) -> str: ...

    def begin_assistant_message(self) -> None: ...

    def append_assistant_delta(self, delta: str) -> None: ...

    def end_assistant_message(self) -> None: ...

    def begin_reasoning(self) -> None: ...

    def append_reasoning_delta(self, delta: str) -> None: ...

    def end_reasoning(self) -> None: ...

    def show_tool_started(self, name: str, arguments_preview: str) -> None: ...

    def show_tool_output(self, output: str) -> None: ...

    def show_tool_completed(self, name: str, *, is_error: bool) -> None: ...

    def show_plan(self, plan: tuple[dict[str, str], ...]) -> None: ...

    def show_assistant_message(self, message: str, *, is_error: bool = False) -> None: ...

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
        self._ui = ui
        self._commands = CommandDispatcher(settings, paths)
        self._realtime_enabled = settings.realtime_enabled
        self._pending_message: str | None = None

    async def run(self) -> int:
        """Run until Ctrl+C/EOF and close runtime resources deterministically."""

        self._ui.show_welcome()
        try:
            await self._consume_events(self._runtime.resume_pending())
            while True:
                try:
                    if self._pending_message is not None:
                        message = self._pending_message
                        self._pending_message = None
                    else:
                        message = (await self._ui.read_message()).strip()
                except (KeyboardInterrupt, EOFError):
                    break
                if not message:
                    continue

                command = self._commands.dispatch(message)
                if command.handled and command.action is not CommandAction.COMPACT:
                    if command.action is CommandAction.CLEAR:
                        self._ui.clear()
                    elif command.action is CommandAction.REALTIME_ON:
                        self._realtime_enabled = True
                        self._commands.set_realtime_enabled(True)
                    elif command.action is CommandAction.REALTIME_OFF:
                        self._realtime_enabled = False
                        self._commands.set_realtime_enabled(False)
                    elif command.action is CommandAction.MCP_REFRESH:
                        self._runtime.request_mcp_refresh()
                    if command.output:
                        self._ui.show_notice(command.output)
                    continue

                try:
                    if command.action is CommandAction.COMPACT:
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
            await self._runtime.aclose()
            self._ui.show_goodbye()
        return 0

    async def _consume_turn(self, message: str) -> None:
        if not self._realtime_enabled:
            await self._consume_events(self._runtime.stream(message))
            return
        await self._consume_realtime_turn(message)

    async def _consume_realtime_turn(self, message: str) -> None:
        """Keep the composer available while a model turn is streaming."""

        consumer = asyncio.create_task(
            self._consume_events(self._runtime.stream(message, realtime=True)),
            name="corki-realtime-output",
        )
        try:
            while not consumer.done():
                reader = asyncio.create_task(
                    self._ui.read_message(),
                    name="corki-realtime-input",
                )
                done, _ = await asyncio.wait(
                    (consumer, reader),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if consumer in done:
                    if reader.done() and not reader.cancelled():
                        try:
                            completed_input = reader.result().strip()
                        except (KeyboardInterrupt, EOFError):
                            completed_input = ""
                        self._pending_message = completed_input or None
                    else:
                        reader.cancel()
                        await asyncio.gather(reader, return_exceptions=True)
                    break
                try:
                    steering = reader.result().strip()
                except (KeyboardInterrupt, EOFError):
                    await self._runtime.cancel_active()
                    break
                if steering == "/stop":
                    await self._runtime.cancel_active()
                    break
                if steering.lower() == "/compact":
                    self._pending_message = "/compact"
                    await self._runtime.cancel_active(reason="replaced")
                    break
                if steering:
                    try:
                        await self._runtime.steer(steering)
                    except RealtimeTurnClosedError:
                        # The task can close input before its terminal event is
                        # delivered. Keep this submission for the next turn.
                        self._pending_message = steering
                        break
            await consumer
        except asyncio.CancelledError:
            await self._runtime.cancel_active()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            raise
        finally:
            if not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)

    async def _consume_events(self, events: AsyncIterator[RuntimeEvent]) -> None:
        streaming = False
        reasoning = False
        saw_text = False
        async for event in events:
            if isinstance(event, TurnStarted):
                if event.resumed:
                    self._ui.show_notice("Resuming interrupted turn...")
            elif isinstance(event, AssistantReasoningDelta):
                if not reasoning:
                    self._ui.begin_reasoning()
                    reasoning = True
                self._ui.append_reasoning_delta(event.delta)
            elif isinstance(event, AssistantTextDelta):
                if reasoning:
                    self._ui.end_reasoning()
                    reasoning = False
                if not streaming:
                    self._ui.begin_assistant_message()
                    streaming = True
                self._ui.append_assistant_delta(event.delta)
                saw_text = True
            elif isinstance(event, AssistantMessageCompleted):
                if streaming:
                    self._ui.end_assistant_message()
                    streaming = False
            elif isinstance(event, AssistantMessageInterrupted):
                if reasoning:
                    self._ui.end_reasoning()
                    reasoning = False
                if streaming:
                    self._ui.end_assistant_message()
                    streaming = False
                self._ui.show_notice(
                    "Response interrupted; retrying with updated history..."
                    if event.reason == "retry"
                    else "Response interrupted; applying your latest input..."
                )
            elif isinstance(event, RealtimeInputAccepted):
                # The active prompt already leaves submitted text in scrollback,
                # so only acknowledge it instead of echoing it a second time.
                self._ui.show_notice("Steering accepted.")
            elif isinstance(event, ToolCallStarted):
                if reasoning:
                    self._ui.end_reasoning()
                    reasoning = False
                if streaming:
                    self._ui.end_assistant_message()
                    streaming = False
                self._ui.show_tool_started(event.tool_name, event.arguments_preview)
            elif isinstance(event, ToolOutputDelta):
                self._ui.show_tool_output(event.delta)
            elif isinstance(event, ToolCallCompleted):
                self._ui.show_tool_completed(event.tool_name, is_error=event.is_error)
            elif isinstance(event, PlanUpdated):
                self._ui.show_plan(event.plan)
            elif isinstance(event, WarningEvent):
                self._ui.show_notice(f"Warning: {event.message}")
            elif isinstance(event, ModelRetryScheduled):
                limit = event.max_attempts if event.max_attempts is not None else "∞"
                label = "Retrying compaction" if event.purpose == "compaction" else "Reconnecting"
                self._ui.show_notice(
                    f"{label}... {event.attempt}/{limit} in {event.delay_seconds:g}s"
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
                if reasoning:
                    self._ui.end_reasoning()
                    reasoning = False
                if streaming:
                    self._ui.end_assistant_message()
                    streaming = False
                if not saw_text and event.final_answer:
                    self._ui.show_assistant_message(event.final_answer)
            elif isinstance(event, TurnFailed):
                if reasoning:
                    self._ui.end_reasoning()
                    reasoning = False
                if streaming:
                    self._ui.end_assistant_message()
                    streaming = False
                self._ui.show_assistant_message(event.error, is_error=True)
            elif isinstance(event, TurnCancelled):
                if reasoning:
                    self._ui.end_reasoning()
                    reasoning = False
                if streaming:
                    self._ui.end_assistant_message()
                    streaming = False
                self._ui.show_notice("Turn interrupted.")
