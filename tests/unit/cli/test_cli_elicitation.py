import asyncio
from io import StringIO

import pytest
from rich.console import Console

from corki.mcp.elicitation import ElicitationRequest


@pytest.mark.parametrize("kind", ["server", "shell_approval", "patch_approval"])
def test_scope_selector_is_only_used_for_host_execution_authority(kind):
    from corki.cli.elicitation import collect_elicitation

    async def scenario():
        selected, prompts = [], []
        answers = iter(("session", "accept"))

        async def read(label):
            prompts.append(label)
            return next(answers)

        async def choose_scope(scopes):
            selected.append(scopes)
            return "accept", {"scope": "session"}

        result = await collect_elicitation(
            ElicitationRequest(
                "fixture",
                "request",
                {
                    "message": "review",
                    "_meta": {"tool_params": {"command": "echo fixture"}},
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"scope": {"type": "string", "enum": ["once", "session"]}},
                    },
                },
                kind,
            ),
            read,
            lambda _: None,
            choose_scope=choose_scope,
        )
        assert result == ("accept", {"scope": "session"})
        assert selected == ([] if kind == "server" else [("once", "session")])
        assert len(prompts) == (2 if kind == "server" else 0)

    asyncio.run(scenario())


def test_patch_retry_displays_uncertainty_before_large_details():
    from corki.cli.elicitation import collect_elicitation

    async def scenario():
        notices = []

        async def read(label):
            return "decline"

        await collect_elicitation(
            ElicitationRequest(
                "local-shell",
                "id",
                {
                    "message": "Retry?",
                    "_meta": {
                        "tool_params": {"patch": "p" * 13000},
                        "patch_retry": {
                            "sandbox": "seatbelt",
                            "output": "original failed; no rollback",
                            "execution": {"stdout": "", "stderr": "permission denied"},
                            "committed_delta": {
                                "exact": False,
                                "changes": [
                                    {"path": "/first", "change": {"content": "c" * 14000}},
                                ],
                            },
                        },
                    },
                    "requestedSchema": {"type": "object", "properties": {}},
                },
                "patch_approval",
            ),
            read,
            notices.append,
        )
        joined = "\n".join(notices)
        assert "delta exact: False; committed changes: 1" in joined
        assert joined.index("delta exact") < joined.index("Tool arguments")
        assert "permission denied" in joined and "original failed; no rollback" in joined
        assert "Patch retry evidence exceeds the display limit" in joined

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["server", "tool_approval", "shell_approval", "patch_approval"])
def test_only_host_owned_approval_displays_execution_arguments(kind):
    from corki.cli.elicitation import collect_elicitation

    async def scenario():
        notices = []
        answers = iter(["true", "accept"])

        async def read(label):
            return next(answers)

        result = await collect_elicitation(
            ElicitationRequest(
                "docs",
                "host",
                {
                    "message": "Allow write?",
                    "_meta": {
                        "codex_approval_kind": "mcp_tool_call",
                        "tool_params": {"value": "HOST_ARGUMENT"},
                    },
                    "requestedSchema": {
                        "type": "object",
                        "properties": {
                            "remember": {"type": "boolean", "default": False},
                        },
                    },
                },
                kind,
            ),
            read,
            notices.append,
        )
        assert result == ("accept", {"remember": True})
        assert ("HOST_ARGUMENT" in repr(notices)) == (kind != "server")
        assert ("Shell execution approval" in notices[0]) == (kind == "shell_approval")
        assert ("Patch approval" in notices[0]) == (kind == "patch_approval")

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_form_typed_fields_and_explicit_decisions(action):
    from corki.cli.elicitation import collect_elicitation

    async def scenario():
        notices = []
        values = iter(["", "Ada", "true", "21", "false", '["blue"]', "", "", action])
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "age": {"type": "integer", "minimum": 18},
                "enabled": {"type": "boolean"},
                "colors": {"type": "array", "items": {"type": "string", "enum": ["blue"]}},
                "optional": {"type": "string"},
            },
            "required": ["name", "age"],
        }

        async def read(label):
            return next(values)

        result = await collect_elicitation(
            ElicitationRequest(
                "server", "host-id", {"message": "Input", "requestedSchema": schema}
            ),
            read,
            notices.append,
        )
        assert result == (
            action,
            {"name": "Ada", "age": 21, "enabled": False, "colors": ["blue"]}
            if action == "accept"
            else None,
        )
        assert any("required" in n for n in notices)
        assert any("Invalid" in n for n in notices)
        assert any("explicitly" in n for n in notices)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["url", "form"])
@pytest.mark.parametrize("decision", ["accept", "/decline", "/cancel", "eof", "interrupt"])
def test_url_or_empty_form_does_not_assume_consent(kind, decision):
    from corki.cli.elicitation import collect_elicitation

    async def scenario():
        notices = []

        async def read(label):
            if decision == "eof":
                raise EOFError
            if decision == "interrupt":
                raise KeyboardInterrupt
            return decision

        result = await collect_elicitation(
            ElicitationRequest(
                "server",
                "id",
                {
                    "mode": kind,
                    "message": "\x1b[31mrequest\x9b",
                    "url": "https://fixture.invalid",
                    "requestedSchema": {"type": "object", "properties": {}},
                },
            ),
            read,
            notices.append,
        )
        action = "cancel" if decision in ("eof", "interrupt") else decision.lstrip("/")
        assert result == (action, {} if action == "accept" else None)
        assert all("\x1b" not in n and "\x9b" not in n for n in notices)
        assert any("open manually" in n for n in notices) == (kind == "url")

    asyncio.run(scenario())


@pytest.mark.parametrize("control", ["complete", "cancel", "queued_cancel"])
def test_modal_preempts_and_joins_reader_before_sequential_forms(control):
    from corki.cli.input_owner import InputOwner

    async def scenario():
        entered, cleaning, release_reader = asyncio.Event(), asyncio.Event(), asyncio.Event()
        modal, release_form, ordinary_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        active, maximum, calls = 0, 0, []

        class UI:
            async def read_message(self):
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                calls.append("ordinary")
                entered.set()
                try:
                    await ordinary_done.wait()
                    return "ordinary input"
                finally:
                    cleaning.set()
                    await release_reader.wait()
                    active -= 1

            async def read_elicitation(self, request):
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                calls.append(request)
                modal.set()
                try:
                    await release_form.wait()
                    return "accept", {}
                finally:
                    active -= 1

        owner = InputOwner(UI())
        reader = asyncio.create_task(owner.read_message())
        a = b = None
        try:
            await entered.wait()
            a = asyncio.create_task(owner.elicit("first"))
            await cleaning.wait()
            assert not modal.is_set()
            b = asyncio.create_task(owner.elicit("second"))
            if control == "queued_cancel":
                await asyncio.sleep(0)
                b.cancel()
                await asyncio.gather(b, return_exceptions=True)
            release_reader.set()
            await modal.wait()
            assert not reader.done()
            if control == "cancel":
                a.cancel()
            release_form.set()
            await asyncio.gather(a, b, return_exceptions=True)
            ordinary_done.set()
            assert await asyncio.wait_for(reader, 1) == "ordinary input"
            assert active == 0 and maximum == 1 and owner._modals == 0
            assert calls.count("first") == 1
            assert calls.count("second") == (control != "queued_cancel")
        finally:
            release_reader.set()
            release_form.set()
            for task in (reader, a, b):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(t for t in (reader, a, b) if t is not None), return_exceptions=True
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_terminal_form_uses_real_prompt_session_without_persistent_history(
    tmp_path, monkeypatch, cancel
):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from corki.cli.terminal import TerminalUI
    from corki.config import CorkiSettings

    async def scenario(pipe):
        monkeypatch.setattr(
            "corki.cli.terminal.PromptSession",
            lambda **kw: PromptSession(input=pipe, output=DummyOutput(), **kw),
        )
        history = tmp_path / "history"
        ui = TerminalUI(
            CorkiSettings(working_directory=tmp_path), history, console=Console(file=StringIO())
        )
        original = ui._form_session.prompt_async
        answers = iter(["HOST_ONLY_VALUE\x03\x03"] if cancel else ["HOST_ONLY_VALUE", "accept"])

        async def prompt(*args, **kwargs):
            answer = next(answers)
            return await original(*args, **kwargs, pre_run=lambda: pipe.send_text(answer + "\r"))

        ui._form_session.prompt_async = prompt
        result = await asyncio.wait_for(
            ui.read_elicitation(
                ElicitationRequest(
                    "server",
                    "id",
                    {
                        "message": "Input",
                        "requestedSchema": {
                            "type": "object",
                            "properties": {"label": {"type": "string"}},
                            "required": ["label"],
                        },
                    },
                )
            ),
            3,
        )
        assert result == (("cancel", None) if cancel else ("accept", {"label": "HOST_ONLY_VALUE"}))
        assert not history.exists() or "HOST_ONLY_VALUE" not in history.read_text()
        assert not ui._form_session.history.get_strings()
        assert ui._form_session.default_buffer.text == ""

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


@pytest.mark.parametrize("repeat_cancel", [False, True])
def test_external_host_resolution_dismisses_and_joins_prompt(repeat_cancel):
    from corki.mcp.elicitation import ElicitationRouter

    async def scenario():
        queue = asyncio.Queue()
        cleaning, release = asyncio.Event(), asyncio.Event()

        async def host(request):
            await queue.put(request)
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()

        router = ElicitationRouter(host)
        task = asyncio.create_task(router.request("server", {}))
        try:
            request = await queue.get()
            router.respond("server", request.request_id, "decline")
            await asyncio.wait_for(cleaning.wait(), 0.2)
            assert not task.done() and router._pending
            if repeat_cancel:
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0)
                assert not task.done()
            release.set()
            if repeat_cancel:
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert await task == {"action": "decline"}
            assert not router._pending
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "schema,value,valid",
    [
        ({"type": "number", "minimum": 1}, True, False),
        ({"type": "number"}, float("inf"), False),
        ({"type": "integer", "maximum": 9}, 10, False),
        ({"type": "string", "minLength": 2}, "短", False),
        ({"type": "string", "maxLength": 2}, "很长啦", False),
        ({"type": "string", "format": "date"}, "2026-02-30", False),
        ({"type": "string", "format": "date-time"}, "2026-01-01T10:00:00", False),
        ({"type": "string", "format": "email"}, "@", False),
        ({"type": "string", "format": "uri"}, "/relative", False),
        ({"type": "string", "oneOf": [{"const": "red", "title": "红色"}]}, "red", True),
        ({"type": "array", "items": {"anyOf": [{"const": "red", "title": "红色"}]}}, ["red"], True),
        ({"type": "array", "minItems": 1, "items": {"type": "string"}}, [], False),
    ],
)
def test_standard_field_constraints(schema, value, valid):
    from corki.cli.elicitation import validate_field

    if valid:
        validate_field(value, schema)
    else:
        with pytest.raises(ValueError):
            validate_field(value, schema)


def test_terminal_ordinary_draft_survives_preemption():
    from types import SimpleNamespace

    from corki.cli.command_completion import CommandCompleter
    from corki.cli.terminal import TerminalUI
    from corki.cli.transcript import Transcript

    async def scenario():
        entered = asyncio.Event()
        defaults = []

        class Session:
            app = SimpleNamespace(output=None)
            default_buffer = SimpleNamespace(text="unfinished ordinary draft")
            completer = CommandCompleter()

            async def prompt_async(self, *args, **kwargs):
                defaults.append(kwargs["default"])
                if len(defaults) == 1:
                    entered.set()
                    await asyncio.Future()
                return "submitted"

        ui = TerminalUI.__new__(TerminalUI)
        from corki.cli.draft_history import DraftHistory
        from corki.cli.inline_images import ImageDraft

        ui._draft_history = DraftHistory()
        ui._draft_history.loaded = True
        ui._inline_images = ImageDraft()
        ui._transcript = Transcript(ui)
        ui._session, ui._draft, ui._bindings = Session(), "", None
        closed_views = []
        ui._history_view = SimpleNamespace(close=lambda: closed_views.append(True))
        task = asyncio.create_task(ui.read_message())
        await asyncio.wait_for(entered.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await ui.read_message() == "submitted"
        assert defaults == ["", "unfinished ordinary draft"] and ui._draft == ""
        assert len(closed_views) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("raises", [False, True])
def test_malformed_host_delivery_does_not_leak_registration(raises):
    from corki.mcp.elicitation import ElicitationRouter

    async def scenario():
        def handler(request):
            if raises:
                raise OSError("host failed before returning an awaitable")
            return None

        router = ElicitationRouter(handler)
        with pytest.raises(OSError if raises else TypeError):
            await router.request("server", {})
        assert not router._pending
        await asyncio.wait_for(router.wait_until_clear(), 0.2)

    asyncio.run(scenario())
