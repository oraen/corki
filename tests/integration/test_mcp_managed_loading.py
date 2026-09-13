"""File authority reaches actual CLI allocation, Runtime search and reconciliation."""

import asyncio
import importlib
from dataclasses import replace

import pytest
from test_mcp_server_requirements import make_runtime
from test_mcp_tool_approval import Client, Model

from corki.cli.main import build_application
from corki.config import CorkiPaths, CorkiSettings, managed_mcp
from corki.config.features import MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolCallItem, ToolResultItem


def system_file(tmp_path, monkeypatch, contents):
    path = tmp_path / "requirements.toml"
    path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(managed_mcp, "system_requirements_path", lambda: path)
    return path


@pytest.mark.parametrize("entry", ["cli", "runtime"])
@pytest.mark.parametrize(
    "contents",
    [
        "[broken",
        "mcp_servers=7",
        "allowed_approval_policies=[]",
        'allowed_approval_policies=["unknown"]',
        "allowed_approval_policies=[true]",
        "allowed_approval_policies=[{granular={rules=true}}]",
        'allowed_approval_policies=[{granular={rules=true,sandbox_approval="true",mcp_elicitations=false}}]',
    ],
)
def test_invalid_managed_policy_fails_before_allocating_resources(
    tmp_path, monkeypatch, entry, contents
):
    system_file(tmp_path, monkeypatch, contents)
    home = tmp_path / "user-home"
    monkeypatch.setenv("CORKI_HOME", str(home))
    constructed = []

    def forbidden(*args, **kwargs):
        constructed.append(True)
        raise AssertionError("managed policy must fail before any resource allocation")

    monkeypatch.setattr(
        importlib.import_module("corki.cli.main"), "SQLiteSessionRepository", forbidden
    )
    monkeypatch.setattr("corki.core.runtime.ProcessManager", forbidden)
    with pytest.raises(ValueError, match="requirements.toml"):
        if entry == "cli":
            build_application()
        else:
            LangGraphRuntime.create(
                settings=CorkiSettings(working_directory=tmp_path),
                database_path=home / "db.sqlite",
            )
    assert constructed == []
    assert not home.exists()


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("change", [False, True])
def test_managed_file_policy_reaches_search_execution_and_replacement_denial(
    tmp_path, monkeypatch, mode, change
):
    path = system_file(
        tmp_path, monkeypatch, '[mcp_servers.docs.identity]\nurl="https://allowed.invalid"'
    )

    async def scenario():
        original = MCPServerSettings("docs", "http", url="https://allowed.invalid")

        class FilePolicyModel(Model):
            async def stream(self, request):
                async for event in super().stream(request):
                    if change and any(
                        isinstance(item, ToolCallItem) and item.call.name != "tool_search"
                        for item in event.items
                    ):
                        # A later disk change cannot silently replace captured authority.
                        path.write_text("", encoding="utf-8")
                        runtime.request_mcp_reconcile(
                            (replace(original, url="https://denied.invalid"),)
                        )
                    yield event

        runtime, clients, model = make_runtime(
            tmp_path,
            monkeypatch,
            requirements=None,
            servers=(original,),
            model=FilePolicyModel(mode),
            mode=mode,
        )
        try:
            events = [event async for event in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(clients) == 1
            assert clients[0].calls == ([] if change else [("write", {"value": 1})])
            results = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ]
            assert ("disabled by managed requirements" if change else "remote effect") in repr(
                results
            )
            assert runtime.mcp_requirements_snapshot.sources == (("mcp_servers", (str(path),)),)
        finally:
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


def test_cli_captures_file_once_before_directories_and_runs_real_tool_loop(tmp_path, monkeypatch):
    main_module = importlib.import_module("corki.cli.main")
    path = system_file(
        tmp_path, monkeypatch, '[mcp_servers.docs.identity]\nurl="https://allowed.invalid"'
    )
    home = tmp_path / "cli-home"
    monkeypatch.setenv("CORKI_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    ensure_exists = CorkiPaths.ensure_exists
    reads = []
    read_text = type(path).read_text

    def read(self, *args, **kwargs):
        if self == path:
            reads.append(self)
        return read_text(self, *args, **kwargs)

    def ensure(self):
        assert reads == [path], "managed policy must precede user-directory creation"
        ensure_exists(self)
        self.config_file.write_text(
            '[provider]\napi_mode="responses"\n[tools]\nsearch_mode="compatible"\n'
            "[skills]\nenabled=false\n[mcp.servers.docs]\n"
            'transport="http"\nurl="https://allowed.invalid"',
            encoding="utf-8",
        )
        path.write_text("[broken", encoding="utf-8")

    clients = []
    model = Model("compatible")

    def client(settings):
        instance = Client(settings)
        clients.append(instance)
        return instance

    monkeypatch.setattr(type(path), "read_text", read)
    monkeypatch.setattr(CorkiPaths, "ensure_exists", ensure)
    monkeypatch.setattr(main_module, "TerminalUI", lambda *args: object())
    monkeypatch.setattr("corki.core.runtime._create_model", lambda *args: model)
    monkeypatch.setattr("corki.mcp.manager.create_client", client)

    async def scenario():
        app = build_application()
        try:
            events = [event async for event in app._runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(clients) == 1
            assert clients[0].calls == [("write", {"value": 1})]
            assert "remote effect" in repr(model.requests[-1].items)
            assert reads == [path]
        finally:
            await app._runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["mapping", "policy", "snapshot"])
def test_explicit_host_policy_is_a_complete_snapshot_not_reread(tmp_path, monkeypatch, kind):
    system_file(tmp_path, monkeypatch, "[broken")
    policy = (
        {"mcp_servers": {}}
        if kind == "mapping"
        else MCPRequirements.from_mapping({"mcp_servers": {}})
    )
    if kind == "snapshot":
        policy = managed_mcp.MCPRequirementsSnapshot(policy, (("mcp_servers", ("host",)),))

    async def scenario():
        runtime, clients, model = make_runtime(
            tmp_path,
            monkeypatch,
            requirements=policy,
            servers=(MCPServerSettings("docs", "http", url="https://allowed.invalid"),),
        )
        try:
            events = [event async for event in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            assert clients == []
            assert not any(t.name.startswith("mcp__docs") for t in model.requests[0].tools)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_cli_reports_invalid_managed_file_as_startup_error(tmp_path, monkeypatch, capsys):
    main_module = importlib.import_module("corki.cli.main")
    system_file(tmp_path, monkeypatch, "[broken")
    home = tmp_path / "unused-home"
    monkeypatch.setenv("CORKI_HOME", str(home))
    with pytest.raises(SystemExit) as error:
        main_module.main([])
    assert error.value.code == 2
    assert "requirements.toml" in capsys.readouterr().err
    assert not home.exists()
