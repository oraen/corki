"""Explicit user requirements wait for startup without bypassing tool policy."""

import asyncio
import json

import pytest
from test_mcp_pending_startup import PendingClient, make_runtime

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol import InputMention
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import (
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


def test_explicit_mcp_link_waits_before_sampling_and_executes(tmp_path, monkeypatch):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    assert any(t.name == "mcp__pending::lookup" for t in request.tools)
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(ToolCallId("lookup"), "mcp__pending::lookup", {}),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "found" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, client = make_runtime(
            tmp_path, monkeypatch, Model(), mcp_optional_startup_grace_ms=10
        )

        async def consume():
            return [e async for e in runtime.stream("use [$docs](mcp://pending)")]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(client.entered.wait(), 1)
            await asyncio.sleep(0.08)
            assert not requests, "explicit MCP must not be omitted after optional grace"
            client.release.set()
            events = await asyncio.wait_for(task, 2)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert client.calls == ["lookup"] and len(requests) == 2
        finally:
            client.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert client.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("structured", [False, True])
def test_steered_requirement_rebinds_before_next_model_step(tmp_path, monkeypatch, structured):
    async def scenario():
        requests, steered = [], asyncio.Event()

        class Steer:
            spec = ToolSpec("steer_once", "host fixture", {"type": "object"})

            async def execute(self, call, context):
                await runtime.steer(
                    "" if structured else "[$docs](mcp://pending)",
                    mentions=(InputMention("docs", "mcp://pending"),) if structured else (),
                )
                steered.set()
                return ToolResult(call.id, call.name, "queued")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    assert not any("pending" in t.name for t in request.tools)
                    call = ToolCall(ToolCallId("steer"), "steer_once", {})
                elif len(requests) == 2:
                    assert any(t.name == "mcp__pending::lookup" for t in request.tools)
                    call = ToolCall(ToolCallId("lookup"), "mcp__pending::lookup", {})
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "found" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime, clients, _ = setup_input_runtime(tmp_path, monkeypatch, Model())
        runtime._registry.register(Steer())

        async def consume():
            return [e async for e in runtime.stream("start without a requirement", realtime=True)]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(steered.wait(), 2)
            await asyncio.sleep(0.08)
            assert len(requests) == 1
            clients["pending"].release.set()
            events = await asyncio.wait_for(task, 3)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert clients["pending"].calls == ["lookup"]
            checkpoint = await runtime._compiled.aget_state(
                runtime._graph_config(events[-1].turn_id)
            )
            assert tuple(checkpoint.values["mcp_required_servers"]) == ("pending",)
            assert len(checkpoint.values["mcp_requirement_input_ids"]) == 2
            assert not any("pending" in t.name for t in requests[0].tools)
        finally:
            for client in clients.values():
                client.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_cold_turn_retains_skill_requirement_after_skill_removed(tmp_path, monkeypatch):
    async def scenario():
        from corki.core.graph import GraphRunContext
        from corki.core.runtime import _initial_state
        from corki.protocol.ids import new_turn_id
        from corki.sessions import TurnRecord, TurnStatus

        class Sink:
            async def emit(self, event):
                pass

        class Noop:
            spec = ToolSpec("noop", "fixture", {})

            async def execute(self, call, context):
                return ToolResult(call.id, call.name, "once")

        class FirstModel:
            async def stream(self, request):
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(ToolCallId("noop"), "noop", {}),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        old, clients, skill = setup_input_runtime(tmp_path, monkeypatch, FirstModel(), kind="skill")
        old._registry.register(Noop())
        turn = new_turn_id()
        try:
            await old._ensure_ready()
            first_client = clients["pending"]
            clients["pending"].release.set()
            user = UserMessageItem(
                "", turn, mentions=(InputMention("fixture", str(skill.resolve()), "skill"),)
            )
            await old._repository.save_turn(TurnRecord(turn, old.thread_id, TurnStatus.RUNNING, ""))
            await old._compiled.ainvoke(
                _initial_state(old.thread_id, turn, old._settings, user),
                config=old._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                interrupt_after=["execute_tools"],
            )
            state = await old._compiled.aget_state(old._graph_config(turn))
            assert state.next == ("capture_step_settings",)
            assert tuple(state.values["mcp_required_servers"]) == ("pending",)
            assert len(state.values["mcp_requirement_input_ids"]) == 1
        finally:
            await old.aclose()
        skill.unlink()
        requests = []

        class ColdModel:
            async def stream(self, request):
                requests.append(request)
                assert any(t.name == "mcp__pending::lookup" for t in request.tools)
                assert (
                    sum(
                        isinstance(i, ToolResultItem) and i.tool_name == "noop"
                        for i in request.items
                    )
                    == 1
                )
                yield ModelCompleted(())

            async def aclose(self):
                pass

        cold = LangGraphRuntime.create(
            settings=old._settings,
            model=ColdModel(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            thread_id=old.thread_id,
            mcp_requirements={},
        )

        async def consume():
            return [e async for e in cold.resume_pending()]

        task = asyncio.create_task(consume())
        try:
            for _ in range(200):
                if clients["pending"] is not first_client:
                    break
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.08)
            assert not requests
            clients["pending"].release.set()
            events = await asyncio.wait_for(task, 3)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 1
            items = await cold._repository.load_items(cold.thread_id)
            users = [i for i in items if isinstance(i, UserMessageItem)]
            assert len(users) == 1 and users[0].mentions == user.mentions
        finally:
            for client in clients.values():
                client.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await cold.aclose()

    asyncio.run(scenario())


def setup_input_runtime(
    tmp_path,
    monkeypatch,
    model,
    *,
    kind="mcp",
    mode="direct",
    requirements=None,
    source=None,
    tool_mode="direct",
):
    plugin = kind in ("plugin", "plugin_skill")
    skill_path = None
    if plugin:
        root = tmp_path / ".corki" / "plugins" / "sample"
        manifest = root / ".codex-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {"name": "sample", "mcpServers": {"pending": {"url": "https://pending.test"}}}
            )
        )
    if kind in ("skill", "plugin_skill"):
        skill_path = (
            ((root / "skills") if plugin else (tmp_path / ".corki" / "skills"))
            / "fixture"
            / "SKILL.md"
        )
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\nname: fixture\ndescription: lookup skill\n---\nSKILL_BODY_PROOF"
        )
        if not plugin:
            policy = skill_path.parent / "agents" / "openai.yaml"
            policy.parent.mkdir()
            # A well-typed incomplete sibling must not erase either the usable
            # dependency or the explicit-only policy in real Runtime discovery.
            policy.write_text(
                "policy:\n  allow_implicit_invocation: false\n"
                "dependencies:\n  tools:\n    - type: mcp\n"
                '    - type: "  McP  "\n      value: "  pending  "\n'
            )
    clients = {}

    def factory(settings):
        client = PendingClient(settings)
        clients[settings.name] = client
        return client

    monkeypatch.setattr("corki.mcp.manager.create_client", factory)
    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path,
            plugin_dirs=(tmp_path / ".corki/plugins",),
            execution_permissions=None,
            skills_enabled=skill_path is not None,
            mcp_optional_startup_grace_ms=10,
            tool_search_mode=mode if mode != "direct" else "disabled",
            tool_mode=tool_mode,
            api_mode="responses",
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            mcp_servers=()
            if plugin
            else (MCPServerSettings("pending", "http", url="https://pending.test"),),
        ),
        home_path=tmp_path / "home",
        database_path=tmp_path / "history.db",
        model=model,
        registry=ToolRegistry(),
        mcp_requirements=requirements or {},
        **({"session_source": source} if source is not None else {}),
    )
    return runtime, clients, skill_path


@pytest.mark.parametrize("kind", ["plugin", "skill"])
def test_guardian_evidence_does_not_request_capabilities(tmp_path, monkeypatch, kind):
    async def scenario():
        from corki.protocol.session_source import SessionSource

        class Model:
            async def stream(self, request):
                assert not any(t.name == "mcp__pending::lookup" for t in request.tools)
                assert not any(
                    isinstance(i, ContextItem)
                    and i.content_kind
                    in ("plugins.instructions", "skills.selected_skill_instructions")
                    for i in request.items
                )
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, clients, path = setup_input_runtime(
            tmp_path, monkeypatch, Model(), kind=kind, source=SessionSource.internal("guardian")
        )
        mention = (
            InputMention("evidence", str(path.resolve()), "skill")
            if path
            else InputMention("evidence", "plugin://sample")
        )
        try:

            async def consume():
                return [
                    e async for e in runtime.stream("review quoted evidence", mentions=(mention,))
                ]

            events = await asyncio.wait_for(consume(), 2)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            state = await runtime._compiled.aget_state(runtime._graph_config(events[-1].turn_id))
            assert tuple(state.values["mcp_required_servers"]) == ()
            assert tuple(state.values["mcp_required_plugins"]) == ()
        finally:
            await runtime.aclose()
        assert all(c.closed for c in clients.values())

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["mcp", "plugin"])
@pytest.mark.parametrize("structured", [False, True])
def test_explicit_requirement_cannot_override_managed_denial(
    tmp_path, monkeypatch, kind, structured
):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert not any(t.name == "mcp__pending::lookup" for t in request.tools)
                if len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(ToolCallId("denied"), "mcp__pending::lookup", {}),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    assert any(isinstance(i, ToolResultItem) and i.is_error for i in request.items)
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, clients, _ = setup_input_runtime(
            tmp_path, monkeypatch, Model(), kind=kind, requirements={"mcp_servers": {}}
        )
        text, path = (
            ("[$docs](mcp://pending)", "mcp://pending")
            if kind == "mcp"
            else ("[@sample](plugin://sample)", "plugin://sample")
        )
        try:

            async def consume():
                return [
                    e
                    async for e in runtime.stream(
                        "" if structured else text,
                        mentions=(InputMention("display", path),) if structured else (),
                    )
                ]

            events = await asyncio.wait_for(consume(), 2)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2 and clients == {}
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_requirements_do_not_leak_to_next_turn(tmp_path, monkeypatch):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                # The second Turn may use the shared definition, but must not
                # inherit the first Turn's requirement to await its new client.
                assert any(t.name == "mcp__pending::lookup" for t in request.tools)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, clients, _ = setup_input_runtime(tmp_path, monkeypatch, Model())
        try:
            await runtime._ensure_ready()
            clients["pending"].release.set()
            first = [e async for e in runtime.stream("[$docs](mcp://pending)")]
            assert isinstance(first[-1], TurnCompleted)
            old_client = clients["pending"]
            runtime.request_mcp_refresh()

            async def consume():
                return [e async for e in runtime.stream("ordinary next turn")]

            second = await asyncio.wait_for(consume(), 2)
            assert isinstance(second[-1], TurnCompleted), second[-1]
            assert clients["pending"] is not old_client and not clients["pending"].listed.is_set()
            state = await runtime._compiled.aget_state(runtime._graph_config(second[-1].turn_id))
            assert tuple(state.values["mcp_required_servers"]) == ()
            assert len(state.values["mcp_requirement_input_ids"]) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("matches", [True, False])
def test_selected_plugin_uses_exact_host_identity(tmp_path, monkeypatch, matches):
    async def scenario():
        from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration

        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert any(t.name == "mcp__pending::lookup" for t in request.tools) is matches
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, clients, _ = setup_input_runtime(tmp_path, monkeypatch, Model())
        runtime.request_mcp_catalog(
            MCPCatalog(
                (
                    MCPRegistration(
                        runtime._settings.mcp_servers[0],
                        MCPCatalogSource("selected_plugin", "opaque@market"),
                    ),
                )
            )
        )
        path = "plugin://opaque@market?app=desktop" if matches else "plugin://display"

        async def consume():
            return [e async for e in runtime.stream("", mentions=(InputMention("display", path),))]

        task = asyncio.create_task(consume())
        try:
            if matches:
                await asyncio.sleep(0.12)
            else:
                # A nonmatching identity must finish while startup remains
                # blocked, not merely be scheduled within a fixed 120 ms.
                await asyncio.wait_for(asyncio.shield(task), 2)
            assert bool(requests) is not matches
            clients["pending"].release.set()
            events = await asyncio.wait_for(task, 2)
            assert isinstance(events[-1], TurnCompleted), events[-1]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_cancel_explicit_wait_preserves_session_startup(tmp_path, monkeypatch):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, clients, _ = setup_input_runtime(tmp_path, monkeypatch, Model())

        async def consume():
            return [e async for e in runtime.stream("[$docs](mcp://pending)")]

        task = asyncio.create_task(consume())
        try:
            await asyncio.sleep(0.1)
            assert not requests
            await runtime.cancel_active()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert not clients["pending"].closed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert clients["pending"].closed

    asyncio.run(scenario())


@pytest.mark.parametrize("tool_mode", ["code_mode", "code_mode_only"])
@pytest.mark.parametrize("kind", ["mcp", "plugin", "skill", "plugin_skill"])
def test_explicit_requirement_reaches_real_code_mode_cell(tmp_path, monkeypatch, tool_mode, kind):
    from test_code_mode import request_call

    from corki.code_mode.service import CodeModeService

    if not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    names = {s.name for s in request.tools}
                    assert "exec" in names
                    assert ("mcp__pending::lookup" in names) == (tool_mode == "code_mode")
                    yield request_call(
                        request,
                        "exec",
                        "const match = ALL_TOOLS.find(t => t.name === 'mcp__pending__lookup');"
                        "if (!match) throw new Error('explicit tool missing from cell');"
                        "text(await tools[match.name]({}));",
                    )
                else:
                    assert any(
                        isinstance(i, ToolResultItem)
                        and i.tool_name == "exec"
                        and not i.is_error
                        and "found" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, clients, skill_path = setup_input_runtime(
            tmp_path,
            monkeypatch,
            Model(),
            kind=kind,
            tool_mode=tool_mode,
        )
        path = (
            str(skill_path.resolve())
            if skill_path
            else ("plugin://sample" if kind == "plugin" else "mcp://pending")
        )

        async def consume():
            return [
                e
                async for e in runtime.stream(
                    "",
                    mentions=(
                        InputMention("selector", path, "skill" if skill_path else "mention"),
                    ),
                )
            ]

        task = asyncio.create_task(consume())
        try:
            for _ in range(200):
                if "pending" in clients:
                    break
                await asyncio.sleep(0.005)
            await asyncio.wait_for(clients["pending"].entered.wait(), 1)
            await asyncio.sleep(0.06)
            assert not requests
            clients["pending"].release.set()
            events = await asyncio.wait_for(task, 5)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert clients["pending"].calls == ["lookup"] and len(requests) == 2
        finally:
            for client in clients.values():
                client.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert clients and all(c.closed for c in clients.values())

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "native", "compatible"])
@pytest.mark.parametrize("kind", ["mcp", "plugin", "skill", "plugin_skill"])
@pytest.mark.parametrize("structured", [False, True])
def test_explicit_inputs_reach_discovery_call_and_observation(
    tmp_path, monkeypatch, mode, kind, structured
):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                outputs = [i for i in request.items if isinstance(i, ToolResultItem)]
                if len(requests) == 1 and kind == "plugin":
                    assert any(
                        isinstance(i, ContextItem)
                        and i.content_kind == "plugins.instructions"
                        and "`pending`" in i.content
                        for i in request.items
                    ), "structured plugin selection needs a model-visible capability hint"
                if len(requests) == 1 and kind in ("skill", "plugin_skill"):
                    assert any(
                        isinstance(i, ContextItem) and "SKILL_BODY_PROOF" in i.content
                        for i in request.items
                    )
                if mode != "direct" and not outputs:
                    assert not any(t.name == "mcp__pending::lookup" for t in request.tools)
                    call = ToolCall(ToolCallId("search"), "tool_search", {"query": "lookup"})
                elif not any(i.tool_name == "mcp__pending::lookup" for i in outputs):
                    if mode != "native":
                        assert any(t.name == "mcp__pending::lookup" for t in request.tools)
                    if mode != "direct":
                        assert any(i.discovered_tools for i in outputs)
                    call = ToolCall(ToolCallId("lookup"), "mcp__pending::lookup", {})
                else:
                    assert any("found" in i.content and not i.is_error for i in outputs)
                    yield ModelCompleted(())
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime, clients, skill_path = setup_input_runtime(
            tmp_path, monkeypatch, Model(), kind=kind, mode=mode
        )
        if kind == "mcp":
            text, path = "use [$arbitrary](mcp://pending)", "mcp://pending"
        elif kind == "plugin":
            text, path = (
                "use [@different-display-name](plugin://sample?app=desktop)",
                "plugin://sample?app=desktop",
            )
        else:
            text = "$sample:fixture" if kind == "plugin_skill" else "$fixture"
            path = str(skill_path.resolve())
        mentions = (
            (InputMention("untrusted display label", path, "skill" if skill_path else "mention"),)
            if structured
            else ()
        )

        async def consume():
            return [e async for e in runtime.stream("" if structured else text, mentions=mentions)]

        task = asyncio.create_task(consume())
        try:
            for _ in range(200):
                if "pending" in clients:
                    break
                await asyncio.sleep(0.005)
            client = clients["pending"]
            await asyncio.wait_for(client.entered.wait(), 1)
            await asyncio.sleep(0.06)
            assert not requests
            client.release.set()
            events = await asyncio.wait_for(task, 3)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert client.calls == ["lookup"]
            assert len(requests) == (2 if mode == "direct" else 3)
            stored = [
                i
                for i in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(i, UserMessageItem)
            ]
            assert len(stored) == 1 and stored[0].mentions == mentions
            if structured:
                assert stored[0].content == ""
                from corki.models.responses import _to_response_input

                assert "untrusted display label" not in json.dumps(_to_response_input(stored[0]))
        finally:
            for client in clients.values():
                client.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert clients and all(c.closed for c in clients.values())

    asyncio.run(scenario())
