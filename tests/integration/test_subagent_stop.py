"""Host-owned child provenance selects separately approved lifecycle commands."""

import asyncio
import json
import shlex
import sys
from hashlib import sha256

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, TurnCompleted
from corki.protocol.ids import ThreadId
from corki.protocol.items import AssistantMessageItem, ContextItem, new_step_id
from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource


@pytest.mark.parametrize(
    "role,matcher,trust,source_kind,expected",
    [
        ("reviewer", None, "child", "spawn", True),
        (None, "default", "child", "spawn", True),
        ("reviewer", "worker|reviewer", "child", "spawn", True),
        ("reviewer", "review", "child", "spawn", False),
        ("reviewer", "view.*", "child", "spawn", True),
        ("审查", r"\p{Han}+", "child", "spawn", True),
        ("reviewer", "*", "child", "spawn", True),
        ("reviewer", "[", "child", "spawn", False),
        ("reviewer", None, "stop", "spawn", False),
        ("reviewer", "*", "different_matcher", "spawn", False),
        ("reviewer", None, "child", "memory", False),
        ("reviewer", None, "child", "synthetic", False),
        ("reviewer", None, "child", "custom", False),
    ],
)
def test_subagent_stop_runs_only_for_matching_independently_approved_child(
    tmp_path, role, matcher, trust, source_kind, expected
):
    script = (
        "import json,sys; from pathlib import Path; p=json.load(sys.stdin); "
        "f=Path('child-hook-input'); f.open('a').write(json.dumps(p)+'\\n'); "
        "print(json.dumps({} if p['stop_hook_active'] else "
        "{'decision':'block','reason':'CHILD_VERIFY'}))"
    )
    command = shlex.join([sys.executable, "-c", script])
    handler = {"type": "command", "command": command, "timeout": 600, "async": False}
    identity = {"event_name": "stop" if trust == "stop" else "subagent_stop", "hooks": [handler]}
    if matcher is not None:
        identity["matcher"] = "other" if trust == "different_matcher" else matcher
    fingerprint = (
        "sha256:"
        + sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
    )
    file = tmp_path / "config.toml"
    key = f"{file}:subagent_stop:0:0"
    definition = "[[hooks.SubagentStop]]\n"
    if matcher is not None:
        definition += f"matcher={json.dumps(matcher)}\n"
    definition += "[[hooks.SubagentStop.hooks]]\ntype='command'\ncommand=" + json.dumps(command)
    definition += f"\n[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(fingerprint)}\n"
    configuration = LocalConfigState((ConfigLayer(file, "user", contents=definition),))
    source = {
        "spawn": SessionSource.subagent(
            SubAgentSource(
                "thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1, agent_role=role)
            )
        ),
        "memory": SessionSource.internal("memory_consolidation"),
        "synthetic": SessionSource.subagent(SubAgentSource("review")),
        "custom": SessionSource.from_startup_arg("subagent:thread_spawn"),
    }[source_kind]

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert len(requests) <= 2
                if len(requests) == 2:
                    assert any(
                        isinstance(item, ContextItem) and item.content == "CHILD_VERIFY"
                        for item in request.items
                    )
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path, skills_enabled=False, plugins_enabled=False, configuration=configuration
            ),
            model=Model(),
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
            session_source=source,
        )
        try:
            events = [event async for event in runtime.stream("finish")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == (2 if expected else 1)
            completed = [event for event in events if isinstance(event, HookCompleted)]
            assert len(completed) == (2 if expected else 0)
            path = tmp_path / "child-hook-input"
            assert path.exists() == expected
            if expected:
                inputs = [json.loads(line) for line in path.read_text().splitlines()]
                assert [item["stop_hook_active"] for item in inputs] == [False, True]
                assert all(item["agent_id"] == runtime.thread_id for item in inputs)
                assert all(
                    item["agent_type"] == (role if role is not None else "default")
                    for item in inputs
                )
                assert all(item["hook_event_name"] == "SubagentStop" for item in inputs)
                session_id = await runtime._repository.load_thread_session_id(runtime.thread_id)
                assert all(item["session_id"] == session_id for item in inputs)
                assert all(event.run.event_name == "SubagentStop" for event in completed)
                assert all(event.run.id.startswith("subagent_stop:") for event in completed)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
