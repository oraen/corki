"""Complete phase-two policy and lazy evidence through the actual child Runtime."""

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import (
    ModelCompleted,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("output", ["files", "json", "noop"])
@pytest.mark.parametrize("extensions", [False, True])
def test_full_policy_is_bound_to_shared_workspace_and_evidence_is_tool_read(
    tmp_path, output, extensions, memory_worker_state_dirs
):
    async def scenario():
        root = tmp_path / "memories"
        root.mkdir()
        (root / "MEMORY.md").write_text("# Task Group: existing\nscope: fixture\n")
        (root / "memory_summary.md").write_text("v1\n\n## User Profile\nExisting index\n")
        if extensions:
            source = root / "extensions/team/resource.md"
            source.parent.mkdir(parents=True)
            source.write_text("LAZY_SOURCE_MARKER\n")
            (source.parent / "instructions.md").write_text("TEAM_INTERPRETATION_MARKER\n")
        requests, worker_roots = [], []

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory:
            async def stream(self, request):
                requests.append(request)
                user = next(i for i in request.items if isinstance(i, UserMessageItem))
                assert user.content.startswith("## Memory Writing Agent: Phase 2")
                for fragment in (
                    "Priority guidance:",
                    "applies_to:",
                    "Wording-preservation rule:",
                    "Recent Active Memory Window behavior",
                    "Minimize churn in incremental mode",
                    "Housekeeping (optional):",
                ):
                    assert fragment in user.content
                assert len(user.content) > 40000
                environment = next(
                    i for i in request.items if getattr(i, "key", None) == "environment.primary"
                )
                cwd = Path(re.search(r"<cwd>(.*?)</cwd>", environment.content, re.S)[1])
                assert cwd == root and cwd.is_dir() and str(cwd) in user.content
                assert (cwd / "phase2_workspace_diff.md").is_file()
                # Startup seeds the built-in ad-hoc extension even without custom sources.
                assert "Memory extensions (under " in user.content
                assert (cwd / "extensions/ad_hoc/instructions.md").is_file()
                assert "rollout_path=" not in user.content
                assert not any(
                    getattr(i, "key", None) == "memory.consolidation.inputs" for i in request.items
                )
                worker_roots.append(cwd)
                turn, step = user.turn_id, new_step_id()
                if len(requests) == 1:
                    assert all("LAZY_SOURCE_MARKER" not in i.content for i in request.items)
                    assert all("TEAM_INTERPRETATION_MARKER" not in i.content for i in request.items)
                    command = "cat phase2_workspace_diff.md raw_memories.md"
                    if extensions:
                        command += " extensions/team/instructions.md extensions/team/resource.md"
                    call = ToolCall(
                        new_tool_call_id(), "exec_command", {"cmd": command, "login": False}
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                    return
                result = next(i for i in reversed(request.items) if isinstance(i, ToolResultItem))
                assert not result.is_error, result.content
                if len(requests) == 2:
                    assert "Memory Workspace Diff" in result.content
                    if extensions:
                        assert "LAZY_SOURCE_MARKER" in result.content
                        assert "TEAM_INTERPRETATION_MARKER" in result.content
                    if output == "files":
                        patch = (
                            "*** Begin Patch\n*** Update File: MEMORY.md\n@@\n"
                            "-# Task Group: existing\n+# Task Group: verified\n"
                            "*** Update File: memory_summary.md\n@@\n"
                            "-Existing index\n+Verified index\n*** End Patch"
                        )
                        call = ToolCall(new_tool_call_id(), "apply_patch", {"patch": patch})
                        yield ModelCompleted((ToolCallItem(call, turn, step),))
                        return
                text = "done"
                if output == "json":
                    text = json.dumps(
                        {
                            "memory": "# Task Group: verified",
                            "memory_summary": "index",
                            "skills": [],
                        }
                    )
                yield ModelCompleted((AssistantMessageItem(text, turn, step),))

            async def aclose(self):
                raise AssertionError("borrowed model must not be closed by child")

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_consolidation_model="fixture",
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=root,
            model=Main(),
            memory_model=Memory(),
        )
        try:
            assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed, runtime._memory_service.warnings
            assert len(requests) == (3 if output == "files" else 2)
            assert worker_roots and all(path == root and path.exists() for path in worker_roots)
            assert memory_worker_state_dirs and all(
                not path.exists() for path in memory_worker_state_dirs
            )
            expected = "existing" if output == "noop" else "verified"
            assert "# Task Group: " + expected in (root / "MEMORY.md").read_text()
            if output != "json":
                assert not (root / "MEMORY.md").read_text().startswith("v1")
            assert (root / "memory_summary.md").read_text().startswith("v1\n")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
def test_full_consolidation_policy_and_lazy_notes_on_actual_http(tmp_path, api_mode):
    async def scenario():
        root = tmp_path / "memories"
        note = root / "extensions/ad_hoc/notes/request.md"
        note.parent.mkdir(parents=True)
        note.write_text("UNIQUE_LAZY_NOTE_BODY\n")
        bodies = []
        operations = [
            (
                "exec_command",
                {
                    "cmd": (
                        "cat extensions/ad_hoc/instructions.md extensions/ad_hoc/notes/request.md"
                    ),
                    "login": False,
                },
            ),
            (
                "apply_patch",
                {
                    "patch": (
                        "*** Begin Patch\n*** Add File: MEMORY.md\n+# Task Group: note\n"
                        "+scope: fixture\n*** Add File: memory_summary.md\n+v1\n"
                        "+noted [ad-hoc note]\n*** End Patch"
                    )
                },
            ),
        ]

        def handle(request):
            body = json.loads(request.content)
            bodies.append(body)
            index = len(bodies) - 1
            messages = body["messages" if api_mode == "chat_completions" else "input"]
            user_text = json.dumps([m for m in messages if m.get("role") == "user"])
            assert "Memory Writing Agent: Phase 2" in user_text
            assert "Recent Active Memory Window behavior" in user_text
            assert "Housekeeping (optional)" in user_text
            assert "UNIQUE_LAZY_NOTE_BODY" not in user_text
            if index == 1:
                assert "UNIQUE_LAZY_NOTE_BODY" in json.dumps(body)
                assert "Never delete a note file" in json.dumps(body)
            if api_mode == "chat_completions":
                delta = {"content": "done"}
                if index < len(operations):
                    name, arguments = operations[index]
                    delta = {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"call-{index}",
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments)},
                            }
                        ]
                    }
                packet = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": "tool_calls" if index < len(operations) else "stop",
                        }
                    ]
                }
            else:
                if index < len(operations):
                    name, arguments = operations[index]
                    item = {
                        "type": "function_call",
                        "call_id": f"call-{index}",
                        "name": name,
                        "arguments": json.dumps(arguments),
                    }
                else:
                    item = {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                packet = {
                    "type": "response.completed",
                    "response": {"id": f"response-{index}", "output": [item]},
                }
            return httpx.Response(200, text="data: " + json.dumps(packet) + "\n\n")

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        adapter = OpenAICompatibleModel if api_mode == "chat_completions" else OpenAIResponsesModel
        memory = adapter(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode=api_mode
            ),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                api_mode=api_mode,
                memories_consolidation_model="fixture",
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=root,
            model=Main(),
            memory_model=memory,
        )
        try:
            assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed, runtime._memory_service.warnings
            assert len(bodies) == 3 and "[ad-hoc note]" in (root / "memory_summary.md").read_text()
            assert note.read_text() == "UNIQUE_LAZY_NOTE_BODY\n"
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_seed_failure_warns_without_disabling_consolidation(tmp_path, monkeypatch):
    async def scenario():
        def fail(root):
            raise OSError("seed failure fixture")

        monkeypatch.setattr("corki.memory.pipeline.seed_extension_instructions", fail)

        class Model:
            async def stream(self, request):
                text = json.dumps({"memory": "fact", "memory_summary": "index", "skills": []})
                yield ModelCompleted(
                    (AssistantMessageItem(text, request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, memories_enabled=True),
            database_path=tmp_path / "sessions.db",
            memory_root=tmp_path / "memories",
            model=Model(),
            memory_model=Model(),
        )
        try:
            assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed
            assert any("seed failure fixture" in w for w in runtime._memory_service.warnings)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
