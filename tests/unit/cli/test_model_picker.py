import asyncio
from pathlib import Path

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.data_structures import Size
from prompt_toolkit.formatted_text import fragment_list_to_text, to_formatted_text
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.cells import cell_len

from corki.cli.model_picker import choose_model


@pytest.mark.parametrize("width,height", [(40, 12), (80, 20)])
def test_long_model_menu_fits_and_scrolls_selected_item(width, height):
    class Output(DummyOutput):
        def get_size(self):
            return Size(rows=height, columns=width)

    models = tuple(f"供应商/模型-{i}-" + "long-name-" * 5 for i in range(20))
    with create_pipe_input() as pipe:
        session = PromptSession(input=pipe, output=Output(), history=DummyHistory())

        async def scenario():
            task = asyncio.create_task(choose_model(session, models[0], models))
            try:
                async with asyncio.timeout(3):
                    while not session.app.is_running:
                        await asyncio.sleep(0)
                    for index in (0, 10):
                        if index:
                            pipe.send_text("\x1b[B" * index)
                            await asyncio.sleep(0.05)
                        text = fragment_list_to_text(to_formatted_text(session.message))
                        assert all(cell_len(line) <= width for line in text.splitlines())
                        assert len(text.splitlines()) <= height - 1
                        assert f"› 供应商/模型-{index}-" in text
                        assert "Enter confirm" in text and "Esc cancel" in text
                        if width == 40 and index == 0:
                            snapshot = Path(__file__).with_name("snapshots") / "model_picker_40.txt"
                            assert text.rstrip() == snapshot.read_text().rstrip()
                    pipe.send_text("\r")
                    assert await task == models[10]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


@pytest.mark.parametrize(
    "keys,expected",
    [("\x1b[B\r", "Next"), ("\x1b", None), ("\x03", None), ("Custom/Case\r", "Custom/Case")],
)
def test_model_picker_requires_confirmation_and_keeps_history_empty(keys, expected):
    with create_pipe_input() as pipe:
        session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())

        async def scenario():
            task = asyncio.create_task(choose_model(session, "Current", ("Current", "Next")))
            try:
                async with asyncio.timeout(3):
                    while not session.app.is_running:
                        await asyncio.sleep(0)
                    assert not task.done()
                    pipe.send_text(keys)
                    assert await task == expected
                    assert list(session.history.get_strings()) == []
                    assert session.default_buffer.text == ""
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


def test_invalid_custom_model_shows_error_and_can_be_corrected():
    from prompt_toolkit.formatted_text import fragment_list_to_text, to_formatted_text

    with create_pipe_input() as pipe:
        session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())

        def rendered():
            return fragment_list_to_text(to_formatted_text(session.message))

        async def scenario():
            task = asyncio.create_task(choose_model(session, "Current", ("Next",)))
            try:
                async with asyncio.timeout(3):
                    while not session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("Invalid Model\r")
                    await asyncio.sleep(0.05)
                    assert not task.done()
                    assert (
                        "Model names cannot contain whitespace or control characters." in rendered()
                    )
                    assert "Enter to use this custom model name." not in rendered()
                    pipe.send_text("\x15Valid/Model")
                    await asyncio.sleep(0.05)
                    assert "cannot contain" not in rendered()
                    pipe.send_text("\r")
                    assert await task == "Valid/Model"
                    assert list(session.history.get_strings()) == []
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
