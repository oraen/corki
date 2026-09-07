"""Runtime-owned cells and a turn-bound bridge to the ordinary executor."""

import asyncio
import importlib.util
import json
import logging
from importlib.metadata import PackageNotFoundError, version
from uuid import uuid4

from corki.code_mode.cell import MAX_BUFFER, Cell
from corki.code_mode.observation import CellObservation
from corki.code_mode.specs import nested_specs
from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import (
    EncryptedContent,
    TextContent,
    ToolCall,
    ToolConcurrency,
    ToolExposure,
    ToolStateUpdate,
)
from corki.tools.errors import CodeModeToolError

_LOG = logging.getLogger(__name__)


class CellEventSink:
    """Late background output cannot block on a turn's abandoned UI queue."""

    def __init__(self, sink, inactive):
        self.sink, self.inactive = sink, inactive

    async def emit(self, event):
        if self.inactive.is_set():
            return
        output = asyncio.create_task(self.sink.emit(event))
        ended = asyncio.create_task(self.inactive.wait())
        try:
            await asyncio.wait((output, ended), return_when=asyncio.FIRST_COMPLETED)
            if output.done():
                output.result()
        finally:
            for task in (output, ended):
                if not task.done():
                    task.cancel()
            await asyncio.gather(output, ended, return_exceptions=True)


class CodeModeService:
    def __init__(self, registry, *, memory_bytes=64 * 1024 * 1024, max_cells=32, max_calls=64):
        self.registry = registry
        self.step_registry = registry
        self.memory_bytes, self.max_cells, self.max_calls = memory_bytes, max_cells, max_calls
        self.cells = {}
        self.stored = {}
        self.active = asyncio.Event()
        self.inactive = asyncio.Event()
        self.dispatch = None
        self.notifier = None
        self.turn_id = None
        self.failure = None
        self.calls = []
        self.step_calls = []
        self.barrier = ()
        self.closed = False
        self.pending_plan = None
        self.cleanup_error = None

    @staticmethod
    def available():
        try:
            version("quickjs-ng")
        except PackageNotFoundError:
            return False
        return importlib.util.find_spec("quickjs") is not None

    def activate(self, turn_id, dispatch, notifier, failure=None, *, registry=None):
        same_worker = (
            self.active.is_set()
            and self.turn_id == turn_id
            and registry is not None
            and self.step_registry is registry
        )
        self.step_registry = self.registry if registry is None else registry
        if self.turn_id != turn_id:
            self.turn_id = turn_id
            self.calls = []
            self.barrier = ()
            self.failure = asyncio.get_running_loop().create_future()
            self.inactive = asyncio.Event()
        self.dispatch, self.notifier = dispatch, notifier
        # Retries stay within one sampling Step, including its execution gate.
        # A genuinely new Step must not inherit an old yielded cell's gate.
        if not same_worker:
            self.step_calls = []
            self.barrier = ()
        self.active.set()

        if failure is None:
            return None

        def propagate(done):
            if not failure.done():
                failure.set_result(done.result())

        self.failure.add_done_callback(propagate)
        return propagate

    def pause(self):
        """Stop new admission without cancelling calls already owned by a worker."""
        self.active.clear()
        self.dispatch = self.notifier = None
        self.step_registry = self.registry
        self.step_calls = []
        self.barrier = ()

    async def _wait_for_worker(self):
        # Event.wait can resume after another task has already cleared it.
        # Recheck admission before touching a dispatcher or capturing settings.
        while not self.active.is_set():
            if self.closed:
                raise asyncio.CancelledError
            await self.active.wait()
        if self.closed:
            raise asyncio.CancelledError

    def fail(self, error):
        if self.failure is not None and not self.failure.done():
            self.failure.set_result(error)

    def record_cleanup_error(self, error):
        # Retain diagnostics even after the originating Turn/observer is gone.
        self.cleanup_error = self.cleanup_error or error
        _LOG.warning("Code Mode resource cleanup failed", exc_info=error)

    async def invoke(self, spec, value):
        await self._wait_for_worker()
        if len(self.calls) >= self.max_calls:
            raise CodeModeToolError("Code Mode nested tool call budget exhausted")
        if spec.input_kind == "freeform":
            if not isinstance(value, str):
                raise CodeModeToolError("tool expects a string input")
            call = ToolCall(
                new_tool_call_id(), spec.name, None, raw_arguments=value, input_kind="freeform"
            )
        else:
            if not isinstance(value, dict):
                raise CodeModeToolError("tool expects a JSON object for arguments")
            call = ToolCall(new_tool_call_id(), spec.name, value, raw_arguments=json.dumps(value))
        # The module retains its original real name and payload kind, not its
        # originating worker's execution settings. Capture the current worker
        # before awaiting its gate; later steps must not rebind admitted calls.
        current = self.step_registry.spec(spec.name)
        dispatch_spec = current if current is not None else spec
        parallel = (
            current is not None
            and current.exposure != ToolExposure.HIDDEN
            and current.concurrency == ToolConcurrency.PARALLEL
        )
        dependencies = self.barrier if parallel else tuple(self.step_calls)
        dispatch = self.dispatch

        async def run():
            await asyncio.gather(
                *(asyncio.shield(task) for task in dependencies), return_exceptions=True
            )
            return await dispatch(call, dispatch_spec)

        task = asyncio.create_task(run(), name=f"corki-cell-tool-{call.id}")
        self.calls.append(task)
        self.step_calls.append(task)
        if not parallel:
            self.barrier = (task,)
        try:
            result = await task
            if result.dispatch_error:
                raise CodeModeToolError(result.content)
            if result.state_update.plan is not None:
                self.pending_plan = result.state_update.plan
            if result.code_mode_output is not None:
                return result.code_mode_output.value
            if result.content_items:
                return "\n".join(
                    text
                    for part in result.content_items
                    if not isinstance(part, EncryptedContent)
                    if (
                        text := part.text if isinstance(part, TextContent) else part.data_url
                    ).strip()
                )
            # Codex's default FunctionToolOutput exposes its text body directly.
            if not result.attachments:
                return result.content
            return "\n".join(
                part
                for part in [result.content, *(item.data_url for item in result.attachments)]
                if part.strip()
            )
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def notify(self, call_id, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("notify expects non-empty text")
        await self._wait_for_worker()
        notifier = self.notifier
        if notifier is not None:
            await notifier(call_id, text[:MAX_BUFFER])

    def consume_state_update(self):
        update = ToolStateUpdate(plan=self.pending_plan)
        self.pending_plan = None
        return update

    def commit(self, writes):
        values = {**self.stored, **writes}
        if len(json.dumps(values, ensure_ascii=True)) > MAX_BUFFER:
            raise ValueError("Code Mode session store limit exceeded")
        self.stored = values

    async def execute(self, call_id, source, delay_ms, max_tokens):
        if self.closed:
            raise ValueError("Code Mode session is closed")
        if len(self.cells) >= self.max_cells:
            raise ValueError("Code Mode active cell limit exceeded; terminate or observe old cells")
        cell_id = str(uuid4())
        cell = Cell(self, cell_id, call_id, source, nested_specs(self.step_registry))
        self.cells[cell_id] = cell
        try:
            return await cell.observe(delay_ms, max_tokens)
        except BaseException:
            await cell.terminate()
            self.cells.pop(cell_id, None)
            raise

    async def wait(self, cell_id, delay_ms, max_tokens, terminate=False):
        cell = self.cells.get(cell_id)
        if cell is None:
            return CellObservation(
                (
                    TextContent(
                        "Script failed\nNo live cell with this ID. "
                        "It may have finished or the session restarted. "
                        "Do not replay side effects blindly."
                    ),
                ),
                True,
            )
        if terminate:
            await cell.terminate()
        return await cell.observe(delay_ms, max_tokens)

    async def deactivate(self, *, interrupt):
        self.pause()
        self.inactive.set()
        if interrupt:
            outcomes = await asyncio.gather(
                *(cell.terminate() for cell in tuple(self.cells.values())), return_exceptions=True
            )
            self.cells.clear()
            for task in self.calls:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self.calls, return_exceptions=True)
            self.dispatch = self.notifier = None
            self.step_registry = self.registry
            errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
            for error in errors:
                self.record_cleanup_error(error)
            if errors:
                raise errors[0]

    async def aclose(self):
        self.closed = True
        await self.deactivate(interrupt=True)
        if self.cleanup_error is not None:
            raise self.cleanup_error
