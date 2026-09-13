"""Request budgets count projected media, without rewriting the archive."""

import asyncio
import threading

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.media.images import ImagePreparation
from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.events import ContextCompacted, TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.protocol.tools import AudioAttachment, ImageAttachment
from corki.tools import ToolRegistry

PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
)


@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("with_usage", [False, True])
@pytest.mark.parametrize("media", ["image", "audio"])
def test_unsupported_media_do_not_force_compaction(tmp_path, supported, with_usage, media):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "summary or answer", request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                supports_image_input=supported,
                supports_audio_input=supported,
                context_window_tokens=40_000,
                auto_compact_tokens=20_000,
            ),
            database_path=tmp_path / "media.db",
            registry=ToolRegistry(),
            model=Model(),
        )
        try:
            await runtime._ensure_ready()
            if with_usage:
                anchor = AssistantMessageItem("previous answer", "prior-turn", new_step_id())
                await runtime._repository.commit_model_step(
                    runtime.thread_id,
                    anchor.turn_id,
                    0,
                    ModelCompleted((anchor,), ModelUsage(total_tokens=100)),
                )
            original = (
                UserMessageItem(
                    "old image input", "old-turn", attachments=(ImageAttachment(PNG),) * 16
                )
                if media == "image"
                else UserMessageItem(
                    "old audio input",
                    "old-turn",
                    content_items=(AudioAttachment("data:audio/mp4;base64," + "YWFh" * 30_000),),
                )
            )
            await runtime._repository.append_items(runtime.thread_id, (original,))
            before = await runtime._repository.load_items(runtime.thread_id)
            events = [event async for event in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted)
            assert any(isinstance(event, ContextCompacted) for event in events) is supported
            assert len(requests) == (2 if supported else 1)
            assert (await runtime._repository.load_items(runtime.thread_id))[
                : len(before)
            ] == before
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "repeat_cancel", "close"])
def test_budget_projection_owns_cpu_work_until_runtime_finishes(tmp_path, monkeypatch, action):
    async def scenario():
        reached = asyncio.Event()
        release, exited = threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        project = ImagePreparation._items
        armed = True

        def held(self, items, for_model):
            nonlocal armed
            if for_model and armed:
                armed = False
                loop.call_soon_threadsafe(reached.set)
                try:
                    assert release.wait(5)
                finally:
                    exited.set()
            return project(self, items, for_model)

        monkeypatch.setattr(ImagePreparation, "_items", held)

        class Model:
            closed = False
            requests = []

            async def stream(self, request):
                self.requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                self.closed = True

        model = Model()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
            ),
            database_path=tmp_path / "owned-media.db",
            model=model,
            registry=ToolRegistry(),
        )
        await runtime._ensure_ready()
        original = UserMessageItem("image", "old-turn", attachments=(ImageAttachment(PNG),))
        await runtime._repository.append_items(runtime.thread_id, (original,))

        observed = []

        async def consume():
            async for event in runtime.stream("cancel during budget"):
                observed.append(event)

        running = asyncio.create_task(consume())
        closing = None
        try:
            await asyncio.wait_for(reached.wait(), 3)
            if action == "close":
                closing = asyncio.create_task(runtime.aclose())
            else:
                running.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            if action == "repeat_cancel":
                running.cancel()
                await asyncio.sleep(0)
            assert not running.done()
            assert not exited.is_set()
            assert not model.requests and not model.closed
            if closing is not None:
                assert not closing.done()
            release.set()
            result = (await asyncio.gather(running, return_exceptions=True))[0]
            assert isinstance(result, asyncio.CancelledError)
            terminal = [
                event
                for event in observed
                if isinstance(event, (TurnCancelled, TurnCompleted, TurnFailed))
            ]
            assert len(terminal) == 1 and isinstance(terminal[0], TurnCancelled)
            assert exited.is_set()
            assert not model.requests
            if closing is not None:
                await closing
            else:
                stored = await runtime._repository.load_items(runtime.thread_id)
                assert stored[0] == original
                events = [event async for event in runtime.stream("retry")]
                assert isinstance(events[-1], TurnCompleted)
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            if closing is not None:
                await closing
            await runtime.aclose()

    asyncio.run(scenario())
