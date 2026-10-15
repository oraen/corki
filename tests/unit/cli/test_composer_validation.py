import asyncio
import io
from functools import partial

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.validation import ValidationError
from rich.console import Console

from corki.cli import terminal
from corki.cli.composer_validation import MAX_USER_INPUT_TEXT_CHARS, ComposerValidator
from corki.cli.inline_images import ImageDraft
from corki.config import CorkiSettings


@pytest.mark.parametrize("folded", [False, True])
@pytest.mark.parametrize("extra", [0, 1])
def test_limit_counts_expanded_unicode_characters(folded, extra):
    draft = ImageDraft()
    text = "中" * (MAX_USER_INPUT_TEXT_CHARS + extra)
    if folded:
        draft.paste(text, 0, 0)
    else:
        draft.clear(text)
    validator = ComposerValidator(lambda: draft)
    if extra:
        with pytest.raises(ValidationError, match="1048577 provided"):
            validator.validate(Document(draft.text))
    else:
        validator.validate(Document(draft.text))


@pytest.mark.parametrize("key", ["\r", "\t"])
def test_rejected_submission_preserves_draft_and_does_not_enter_history(tmp_path, monkeypatch, key):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(5):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    # Seed a real folded draft without making the pipe carry a MB.
                    draft = ui._inline_images
                    draft.paste("字" * (MAX_USER_INPUT_TEXT_CHARS + 1), 0, 0)
                    ui._set_image_document(len(draft.text))
                    original = draft.text
                    pipe.send_text(key)
                    while ui._session.default_buffer.validation_error is None:
                        await asyncio.sleep(0)
                    assert not task.done()
                    assert draft.text == original and len(draft.pastes) == 1
                    assert ui._session.history.get_strings() == []
                    assert not (tmp_path / "history").exists()
                    # Correction must allow a subsequent normal submission.
                    ui._session.default_buffer.document = Document("fixed", 5)
                    pipe.send_text("\r")
                    result = await task
                    assert result == "fixed"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
