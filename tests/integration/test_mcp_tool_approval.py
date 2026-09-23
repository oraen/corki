"""Actual tool execution is gated by trusted host decisions, not model consent."""

import asyncio
import sqlite3
from dataclasses import replace

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import MCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


class Client(MCPClient):
    def __init__(self, settings, annotations=None):
        super().__init__(settings)
        self.annotations = annotations
        self.calls = []
        self.closed = False
        self.server_instructions = "needle"

    @property
    def is_closed(self):
        return self.closed

    async def start(self):
        pass

    async def list_tools(self):
        return [
            {
                "name": "write",
                "description": "needle",
                "inputSchema": {"type": "object"},
                "annotations": self.annotations,
            }
        ]

    async def call_tool(self, name, arguments):
        assert not self.closed
        self.calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "remote effect"}]}

    async def aclose(self):
        self.closed = True

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)


def settings_for(tmp_path, *, mode="direct", policy="on-request", approval="prompt"):
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp]\napproval_policy="' + policy + '"\n'
        '[mcp.servers.docs]\ntransport="http"\nurl="https://fixture.invalid"\n'
        'default_tools_approval_mode="' + approval + '"\n'
        "[models.catalog.gpt-5]\nsupports_search_tool=true\n"
    )
    return replace(
        CorkiSettings.for_directory(tmp_path, config_file=config),
        skills_enabled=False,
        tool_mode="code_mode" if mode == "code_mode" else "direct",
        tool_search_mode=mode if mode in ("native", "compatible") else "disabled",
        api_mode="responses",
        memories_enabled=True,
        memories_generate=False,
        memories_background_enabled=False,
        memories_disable_on_external_context=True,
    )


class Model:
    def __init__(self, mode="direct", repeat=1):
        self.mode = mode
        self.repeat = repeat
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        found = any(
            isinstance(i, ToolResultItem) and i.tool_name == "tool_search" for i in request.items
        )
        results = [
            i
            for i in request.items
            if isinstance(i, ToolResultItem) and i.turn_id == turn and i.tool_name != "tool_search"
        ]
        if self.mode in ("native", "compatible") and not found:
            call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
        elif len(results) < self.repeat:
            call = (
                ToolCall(
                    new_tool_call_id(),
                    "exec",
                    None,
                    input_kind="freeform",
                    raw_arguments='text(await tools.mcp__docs__write({"value": 1}));',
                )
                if self.mode == "code_mode"
                else ToolCall(new_tool_call_id(), "mcp__docs::write", {"value": len(results) + 1})
            )
        else:
            yield ModelCompleted((AssistantMessageItem("done", turn, step),))
            return
        yield ModelCompleted((ToolCallItem(call, turn, step),))

    async def aclose(self):
        pass


async def runtime_for(tmp_path, monkeypatch, settings, model, *, annotations=None):
    clients = []

    def factory(config):
        client = Client(config, annotations)
        clients.append(client)
        return client

    monkeypatch.setattr("corki.mcp.manager.create_client", factory)
    runtime = await LangGraphRuntime.acreate(
        settings=settings,
        model=model,
        registry=ToolRegistry(),
        database_path=tmp_path / "history.db",
        home_path=tmp_path / "home",
    )
    return runtime, clients


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_real_runtime_waits_before_remote_effect_and_returns_observation(
    tmp_path, monkeypatch, mode, action
):
    async def scenario():
        model = Model(mode)
        runtime, clients = await runtime_for(
            tmp_path, monkeypatch, settings_for(tmp_path, mode=mode), model
        )
        received, release = asyncio.Event(), asyncio.Event()
        requests = []

        async def host(request):
            requests.append(request)
            received.set()
            await release.wait()
            runtime.respond_mcp_elicitation(request.server_name, request.request_id, action)

        async def consume():
            return [e async for e in runtime.stream("needle")]

        runtime.set_mcp_elicitation_handler(host)
        await runtime.set_thread_memory_mode("enabled")
        task = asyncio.create_task(consume())
        waiting = asyncio.create_task(received.wait())
        try:
            await asyncio.wait_for(
                asyncio.wait((task, waiting), return_when=asyncio.FIRST_COMPLETED), 5
            )
            assert clients[0].calls == [], "tool ran without host approval"
            assert received.is_set() and not task.done()
            # Existing tool_search results independently carry external context.
            # Direct/Code Mode have no earlier search pollution to obscure this gate.
            prior_mode = "polluted" if mode in ("native", "compatible") else "enabled"
            with sqlite3.connect(tmp_path / "history.db") as db:
                assert db.execute("SELECT memory_mode FROM threads").fetchone()[0] == prior_mode
            assert requests[0].kind == "tool_approval"
            assert requests[0].params["_meta"]["tool_params"] == {"value": 1}
            release.set()
            events = await asyncio.wait_for(task, 5)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(clients[0].calls) == (1 if action == "accept" else 0)
            with sqlite3.connect(tmp_path / "history.db") as db:
                assert db.execute("SELECT memory_mode FROM threads").fetchone()[0] == (
                    "polluted" if action == "accept" else prior_mode
                )
            result = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)][-1]
            assert ("remote effect" in result.content) == (action == "accept")
            if action != "accept":
                assert "MCP tool call" in result.content
            with pytest.raises(ValueError):
                runtime.respond_mcp_elicitation("docs", requests[0].request_id, "accept")
        finally:
            release.set()
            task.cancel()
            waiting.cancel()
            await asyncio.gather(task, waiting, return_exceptions=True)
            await runtime.aclose()
        assert all(c.closed for c in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,annotations,asks",
    [
        ("auto", None, True),
        ("auto", {"readOnlyHint": True}, False),
        ("auto", {"readOnlyHint": True, "destructiveHint": True}, True),
        ("auto", {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}, False),
        ("auto", {"destructiveHint": False}, True),
        ("auto", {"openWorldHint": False}, True),
        ("writes", {"readOnlyHint": True, "destructiveHint": True}, False),
        ("writes", {"destructiveHint": False, "openWorldHint": False}, True),
        ("writes", None, True),
        ("prompt", {"readOnlyHint": True}, True),
        ("approve", {"destructiveHint": True}, False),
    ],
)
@pytest.mark.parametrize("policy", ["never", "on-request"])
def test_runtime_native_policy_and_annotation_matrix(
    tmp_path, monkeypatch, mode, annotations, asks, policy
):
    async def scenario():
        runtime, clients = await runtime_for(
            tmp_path,
            monkeypatch,
            settings_for(tmp_path, policy=policy, approval=mode),
            Model(),
            annotations=annotations,
        )
        received = []

        async def host(request):
            received.append(request)
            runtime.respond_mcp_elicitation(request.server_name, request.request_id, "decline")

        runtime.set_mcp_elicitation_handler(host)
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            must_ask = asks and policy != "never"
            assert len(received) == int(must_ask)
            assert len(clients[0].calls) == int(not must_ask)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["auto", "prompt", "writes"])
@pytest.mark.parametrize("response", ["form", "native_meta"])
def test_remember_is_session_local_and_only_auto_across_arguments_and_turns(
    tmp_path, monkeypatch, mode, response
):
    async def scenario():
        settings = settings_for(tmp_path, approval=mode)
        runtime, clients = await runtime_for(tmp_path, monkeypatch, settings, Model(repeat=2))
        received = []

        async def host(request):
            received.append(request)
            # Mutating the host view must not change the actual execution arguments.
            request.params["_meta"]["tool_params"]["value"] = "changed host view"
            runtime.respond_mcp_elicitation(
                request.server_name,
                request.request_id,
                "accept",
                content={"remember": True} if response == "form" else None,
                meta={"persist": "session"} if response == "native_meta" else None,
            )

        runtime.set_mcp_elicitation_handler(host)
        try:
            for _ in range(2):
                events = [e async for e in runtime.stream("needle")]
                assert isinstance(events[-1], TurnCompleted)
            assert clients[0].calls == [("write", {"value": n}) for n in (1, 2, 1, 2)]
            assert len(received) == (1 if mode == "auto" else 4)
        finally:
            await runtime.aclose()
        cold, cold_clients = await runtime_for(tmp_path, monkeypatch, settings, Model())
        try:
            # A new host session must ask again; missing handler declines, not consents.
            events = [e async for e in cold.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert cold_clients[0].calls == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["missing", "failure", "cancel_active", "close"])
@pytest.mark.parametrize("mode", ["direct", "code_mode"])
def test_approval_failure_and_teardown_never_reach_effect(tmp_path, monkeypatch, case, mode):
    async def scenario():
        model = Model(mode)
        runtime, clients = await runtime_for(
            tmp_path, monkeypatch, settings_for(tmp_path, mode=mode), model
        )
        entered, cleaned = asyncio.Event(), asyncio.Event()
        requests = []

        async def host(request):
            requests.append(request)
            entered.set()
            try:
                if case == "failure":
                    raise RuntimeError("HOST_PRIVATE_FAILURE")
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        async def consume():
            return [e async for e in runtime.stream("needle")]

        if case != "missing":
            runtime.set_mcp_elicitation_handler(host)
        task = asyncio.create_task(consume())
        try:
            if case != "missing":
                await asyncio.wait_for(entered.wait(), 5)
            if case == "cancel_active":
                await runtime.cancel_active()
            elif case == "close":
                await runtime.aclose()
            if case in ("cancel_active", "close"):
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            else:
                await asyncio.wait_for(task, 5)
            assert clients[0].calls == []
            assert case == "missing" or cleaned.is_set()
            await asyncio.wait_for(runtime._mcp_manager.elicitations.wait_until_clear(), 1)
            if case in ("failure", "missing"):
                result = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)][-1]
                assert "HOST_PRIVATE_FAILURE" not in result.content
                assert "MCP tool" in result.content
            for request in requests:
                with pytest.raises(ValueError):
                    runtime.respond_mcp_elicitation("docs", request.request_id, "accept")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert all(c.closed for c in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("refresh", [False, True])
def test_waiting_approval_keeps_exact_connection_but_later_calls_use_new_policy(
    tmp_path, monkeypatch, refresh
):
    async def scenario():
        settings = settings_for(tmp_path)
        runtime, clients = await runtime_for(tmp_path, monkeypatch, settings, Model(repeat=2))
        received = []

        async def host(request):
            received.append(request)
            assert clients[0].calls == []
            changed = replace(settings.mcp_servers[0], default_tools_approval_mode="approve")
            operation = runtime.request_mcp_refresh if refresh else runtime.request_mcp_reconcile
            operation((changed,))
            await runtime._mcp_manager.refresh_if_dirty()
            assert not clients[0].closed, "old prepared call must retain its transport"
            runtime.respond_mcp_elicitation("docs", request.request_id, "accept")

        runtime.set_mcp_elicitation_handler(host)
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(received) == 1
            if refresh:
                assert [c.calls for c in clients] == [
                    [("write", {"value": 1})],
                    [("write", {"value": 2})],
                ]
            else:
                assert len(clients) == 1
                assert clients[0].calls == [("write", {"value": 1}), ("write", {"value": 2})]
        finally:
            await runtime.aclose()
        assert all(c.closed for c in clients)

    asyncio.run(scenario())


def test_latest_per_tool_override_wins_at_admission(tmp_path, monkeypatch):
    async def scenario():
        settings = settings_for(tmp_path, approval="approve")
        base = Model()

        class UpdatingModel(Model):
            async def stream(self, request):
                async for event in base.stream(request):
                    if len(base.requests) == 1:
                        runtime.request_mcp_reconcile(
                            (
                                replace(
                                    settings.mcp_servers[0],
                                    tool_approval_modes=(("write", "prompt"),),
                                ),
                            )
                        )
                    yield event

        runtime, clients = await runtime_for(tmp_path, monkeypatch, settings, UpdatingModel())
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert clients[0].calls == [], "latest per-tool prompt must override older approve"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["default_tools_approval_mode", "approval_mode"])
@pytest.mark.parametrize("value", ["unknown", "PROMPT", " prompt ", False, 1, {}, []])
def test_invalid_policy_values_are_not_silently_ignored(key, value):
    data = (
        {key: value} if key == "default_tools_approval_mode" else {"tools": {"write": {key: value}}}
    )
    with pytest.raises(ValueError, match="approval_mode"):
        MCPServerSettings.from_mapping("docs", {"url": "https://fixture.invalid", **data})


def test_native_plugin_mapping_and_toml_preserve_overrides(tmp_path):
    data = {
        "url": "https://fixture.invalid",
        "default_tools_approval_mode": "prompt",
        "tools": {"write": {"approval_mode": "approve", "output_token_limit": 123}},
    }
    server = MCPServerSettings.from_mapping("docs", data)
    assert server.default_tools_approval_mode == "prompt"
    assert server.tool_approval_modes == (("write", "approve"),)
    assert server.tool_output_token_limits == (("write", 123),)
    assert settings_for(tmp_path).mcp_approval_policy == "on-request"
    data["tools"]["write"]["approval_mode"] = "auto"
    assert server.tool_approval_modes == (("write", "approve"),)


@pytest.mark.parametrize("value", [None, "auto", "on_request", "ON-REQUEST", False, {}, []])
def test_mcp_scoped_approval_policy_rejects_unimplemented_or_invalid_values(tmp_path, value):
    with pytest.raises(ValueError, match="approval_policy"):
        CorkiSettings(working_directory=tmp_path, mcp_approval_policy=value)


@pytest.mark.parametrize("value", ["true", 1, [], {}])
def test_malformed_remote_hint_cannot_publish_an_unreviewed_tool(tmp_path, monkeypatch, value):
    async def scenario():
        runtime, clients = await runtime_for(
            tmp_path,
            monkeypatch,
            settings_for(tmp_path, approval="auto"),
            Model(),
            annotations={"readOnlyHint": value},
        )
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert clients[0].calls == []
            assert "booleans" in repr(runtime._mcp_manager.warnings)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["accept", "decline", "cancel", "missing", "failure"])
def test_approval_outcome_keeps_prepared_tool_output_budget(tmp_path, monkeypatch, action):
    async def scenario():
        settings = settings_for(tmp_path)
        settings = replace(
            settings,
            mcp_servers=(
                replace(
                    settings.mcp_servers[0],
                    tool_output_token_limits=(("write", 20),),
                ),
            ),
        )
        model = Model()
        runtime, clients = await runtime_for(tmp_path, monkeypatch, settings, model)

        async def host(request):
            if action == "failure":
                raise RuntimeError("host failed")
            runtime.respond_mcp_elicitation("docs", request.request_id, action)

        if action != "missing":
            runtime.set_mcp_elicitation_handler(host)
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            result = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)][-1]
            assert result.fallback_token_limit_override == 24
            assert result.is_error == (action != "accept")
            assert len(clients[0].calls) == int(action == "accept")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
