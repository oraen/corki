"""Core question broker cleanup is independent of event consumer progress."""

import asyncio

import pytest

from corki.core.user_input import UserInputBroker
from corki.protocol.user_input import parse_questions


def test_cancel_during_blocked_event_delivery_releases_waiter():
    async def scenario():
        entered = asyncio.Event()
        never = asyncio.Event()
        broker = UserInputBroker()

        async def emit(event):
            entered.set()
            await never.wait()

        task = asyncio.create_task(broker.request("thread", "turn", "call", (), True, emit))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert broker._pending is None
        assert not broker.respond("call", {"answers": {}})

    asyncio.run(scenario())


def test_question_normalization_keeps_native_permissive_count_and_other_flags():
    assert parse_questions({"questions": []}) == ()
    questions = parse_questions(
        {
            "questions": [
                {
                    "id": "",
                    "header": "",
                    "question": "",
                    "isOther": False,
                    "isSecret": True,
                    "options": [{"label": "", "description": ""}],
                }
            ]
            * 4
        }
    )
    assert len(questions) == 4
    assert all(q.is_other and q.is_secret and len(q.options) == 1 for q in questions)


def test_user_input_configuration_is_ordinary_host_configuration(tmp_path):
    from corki.config import CorkiSettings

    config = tmp_path / "config.toml"
    config.write_text(
        "[tools.experimental_request_user_input]\nenabled = false\n"
        "[features]\ndefault_mode_request_user_input = true\n"
    )
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert not settings.request_user_input_enabled
    assert settings.default_mode_request_user_input
    with pytest.raises(ValueError, match="must be a boolean"):
        CorkiSettings(working_directory=tmp_path, default_mode_request_user_input="yes")
