import asyncio
import base64
import io

import pytest
from PIL import Image
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.input_owner import ImageInput
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.events import AssistantTextDelta, TurnCompleted
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.protocol.tools import ImageAttachment


def attachment():
    stream = io.BytesIO()
    with Image.new("RGB", (2, 2), "blue") as image:
        image.save(stream, format="PNG")
    return ImageAttachment("data:image/png;base64," + base64.b64encode(stream.getvalue()).decode())


def images(items):
    return [
        part
        for item in items
        if isinstance(item, UserMessageItem)
        for part in (*item.attachments, *item.content_items)
        if isinstance(part, ImageAttachment)
    ]


@pytest.mark.parametrize("caption", ["", "what is this?"])
@pytest.mark.parametrize("positioned", [False, True])
def test_cli_image_reaches_model_and_persisted_history(tmp_path, caption, positioned):
    text = "  " + caption + "[Image #1]后文\n" if positioned else caption
    positions = (2 + len(caption),) if positioned else ()

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        settings = CorkiSettings(
            tmp_path, skills_enabled=False, plugins_enabled=False, realtime_enabled=False
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
        )
        inputs = iter((ImageInput(text, (attachment(),), positions),))

        class UI(TerminalUI):
            keep_composer_during_turn = False

            async def read_message(self):
                try:
                    return next(inputs)
                except StopIteration:
                    history = await runtime.load_display_history()
                    assert len(images(history)) == 1
                    raise EOFError from None

        ui = UI(settings, tmp_path / "history", console=Console(file=io.StringIO()))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "home"), runtime, ui)
        assert await app.run() == 0
        assert len(requests) == 1
        assert len(images(requests[0].items)) == 1
        user = next(item for item in requests[0].items if isinstance(item, UserMessageItem))
        assert user.image_positions == positions
        if positioned:
            from corki.models.openai_compatible import _user_message
            from corki.models.responses import _response_input_content

            for wire in (_response_input_content(user), _user_message(user)):
                assert wire["content"][0]["text"] == "<image name=[Image #1]>"
                assert wire["content"][-1]["text"] == text
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
            thread_id=runtime.thread_id,
        )
        try:
            saved = await cold.load_display_history()
            assert len(images(saved)) == 1
            assert (
                next(i for i in saved if isinstance(i, UserMessageItem)).image_positions
                == positions
            )
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("positioned", [False, True])
def test_image_only_steering_reaches_next_model_step(tmp_path, positioned):
    async def scenario():
        requests = []
        release = asyncio.Event()

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield ModelTextDelta("waiting")
                    await release.wait()
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
        )
        try:
            async with asyncio.timeout(10):
                async for event in runtime.stream("start", realtime=True):
                    if isinstance(event, AssistantTextDelta) and event.delta == "waiting":
                        await runtime.steer(
                            "  前[Image #1]后\n" if positioned else "",
                            attachments=(attachment(),),
                            image_positions=(3,) if positioned else (),
                        )
                        release.set()
                assert isinstance(event, TurnCompleted)
                assert len(requests) == 2
                assert len(images(requests[-1].items)) == 1
                if positioned:
                    user = next(
                        item
                        for item in reversed(requests[-1].items)
                        if isinstance(item, UserMessageItem)
                    )
                    assert user.content == "  前[Image #1]后\n"
                    assert user.image_positions == (3,)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
