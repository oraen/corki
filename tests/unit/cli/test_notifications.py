import asyncio
from types import SimpleNamespace

import pytest
from prompt_toolkit.input.vt100_parser import Vt100Parser

from corki.cli.notifications import TerminalNotifications
from corki.cli.terminal_responses import TerminalResponseParser
from corki.config import CorkiSettings


def test_notification_settings_load_from_tui_table(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[tui]\nnotifications = ["approval-requested"]\n'
        'notification_method = "bel"\nnotification_condition = "always"\n'
    )
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.notifications == ("approval-requested",)
    assert settings.notification_method == "bel"
    assert settings.notification_condition == "always"


def test_focus_framing_and_cleanup(tmp_path):
    output, keys = [], []
    writer = SimpleNamespace(write_raw=output.append, flush=lambda: None)
    settings = CorkiSettings(tmp_path)
    notices = TerminalNotifications(
        writer, settings, interactive=True, environ={"TERM_PROGRAM": "iTerm.app"}
    )
    parser = TerminalResponseParser(Vt100Parser(keys.append))
    with notices.reporting((parser,)):
        notices.notify("agent-turn-complete", "focused")
        assert output == ["\x1b[?1004h"]
        for ch in "\x1b[O":
            parser.feed(ch)
        notices.notify("agent-turn-complete", "new")
        notices.notify("agent-turn-complete", "new")
        assert output[-1] == "\x1b]9;Corki task complete\a"
        parser.feed("\x1b[200~\x1b[I\x1b[201~")
        assert not notices.focused  # pasted focus-looking bytes are user text
        assert keys[-1].data == "\x1b[I"
        parser.feed("\x1b[I")
        assert notices.focused
    assert output[-1] == "\x1b[?1004l"
    assert not parser.focus_listeners and not notices.seen


def test_coalescing_privacy_and_tmux(tmp_path):
    async def scenario():
        output = []
        writer = SimpleNamespace(write_raw=output.append, flush=lambda: None)
        settings = CorkiSettings(
            tmp_path, notification_condition="always", notification_method="osc9"
        )
        notices = TerminalNotifications(writer, settings, interactive=True, environ={"TMUX": "yes"})
        notices.notify("agent-turn-complete", "secret response")
        notices.notify("approval-requested", "secret command")
        notices.notify("agent-turn-complete", "other")
        await asyncio.sleep(0)
        assert output == ["\x1bPtmux;\x1b\x1b]9;Corki needs your input\a\x1b\\"]
        assert notices.handle is None and notices.pending is None

    asyncio.run(scenario())


@pytest.mark.parametrize("interactive,enabled", [(False, True), (True, False)])
def test_no_output_when_disabled_or_redirected(tmp_path, interactive, enabled):
    output = []
    writer = SimpleNamespace(write_raw=output.append, flush=lambda: None)
    notices = TerminalNotifications(
        writer,
        CorkiSettings(tmp_path, notifications=enabled, notification_condition="always"),
        interactive=interactive,
        environ={},
    )
    notices.notify("agent-turn-complete", "turn")
    assert not output


def test_write_failure_does_not_escape(tmp_path):
    def fail(_):
        raise OSError("closed")

    notices = TerminalNotifications(
        SimpleNamespace(write_raw=fail, flush=lambda: None),
        CorkiSettings(tmp_path, notification_condition="always"),
        interactive=True,
        environ={},
    )
    notices.notify("approval-requested", "request")
    assert notices.failed
    notices.notify("agent-turn-complete", "next")


@pytest.mark.parametrize("environment,enabled", [({"TERM": "dumb"}, True), ({}, ())])
def test_unsupported_or_empty_filter_does_not_enable_focus_protocol(tmp_path, environment, enabled):
    output = []
    parser = TerminalResponseParser(Vt100Parser(lambda key: None))
    notices = TerminalNotifications(
        SimpleNamespace(write_raw=output.append, flush=lambda: None),
        CorkiSettings(tmp_path, notifications=enabled, notification_condition="always"),
        interactive=True,
        environ=environment,
    )
    with notices.reporting((parser,)):
        notices.notify("agent-turn-complete", "id")
        assert not parser.focus_listeners
    assert output == []


def test_pending_notification_cancelled_on_exit_and_focus_return(tmp_path):
    async def scenario():
        output = []
        notices = TerminalNotifications(
            SimpleNamespace(write_raw=output.append, flush=lambda: None),
            CorkiSettings(tmp_path),
            interactive=True,
            environ={},
        )
        with notices.reporting(()):
            notices.set_focus(False)
            notices.notify("agent-turn-complete", "id")
            notices.set_focus(True)
            await asyncio.sleep(0)
            assert output == []
            notices.set_focus(False)
            notices.notify("approval-requested", "approval")
            assert notices.handle is not None
        await asyncio.sleep(0)
        assert output == [] and notices.handle is None

    asyncio.run(scenario())


def test_unimplemented_output_backend_does_not_raise_from_scheduled_callback(tmp_path):
    async def scenario():
        def fail(_):
            raise NotImplementedError("optional backend")

        notices = TerminalNotifications(
            SimpleNamespace(write_raw=fail, flush=lambda: None),
            CorkiSettings(tmp_path, notification_condition="always"),
            interactive=True,
            environ={},
        )
        notices.notify("agent-turn-complete", "id")
        await asyncio.sleep(0)
        assert notices.failed and notices.handle is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "environment,method",
    [
        ({"TERM_PROGRAM": name}, "osc9")
        for name in ("iTerm.app", "Ghostty", "WezTerm", "WarpTerminal", "kitty")
    ]
    + [({}, "bel"), ({"TERM_PROGRAM": "Apple_Terminal"}, "bel")],
)
def test_auto_backend_matches_supported_terminal_family(tmp_path, environment, method):
    notices = TerminalNotifications(
        SimpleNamespace(write_raw=lambda _: None, flush=lambda: None),
        CorkiSettings(tmp_path),
        interactive=True,
        environ=environment,
    )
    assert notices.method == method


def test_notification_deduplication_storage_is_bounded(tmp_path):
    notices = TerminalNotifications(
        SimpleNamespace(write_raw=lambda _: None, flush=lambda: None),
        CorkiSettings(tmp_path),
        interactive=True,
        environ={},
    )
    for index in range(1000):
        notices.notify("agent-turn-complete", index)
    assert len(notices.seen) == 256
    assert notices.seen[-1] == ("agent-turn-complete", "999")
    assert notices.handle is None  # Focused notifications schedule no callbacks.
