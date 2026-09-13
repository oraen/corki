"""Effective filesystem authority must reach the user-role environment snapshot."""

import asyncio
from dataclasses import replace
from xml.etree import ElementTree

import pytest
from test_bundled_execution import compiler as compiler
from test_filesystem_helper_runtime import workspace_policy

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("profile", ["managed", "disabled"])
@pytest.mark.parametrize("change_on_reopen", [False, True])
def test_effective_filesystem_is_present_in_runtime_environment(
    tmp_path, compiler, profile, change_on_reopen
):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        permissions = workspace_policy(compiler, tmp_path)
        if profile == "disabled":
            permissions = replace(permissions, profile_json='{"type":"disabled"}')

        async def create(thread_id=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path, skills_enabled=False, execution_permissions=permissions
                ),
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                thread_id=thread_id,
            )

        runtime = await create()
        try:
            events = [event async for event in runtime.stream("inspect effective environment")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            environment = [
                item
                for item in requests[0].items
                if isinstance(item, ContextItem) and item.key == "environment.primary"
            ]
            assert len(environment) == 1 and environment[0].role == "user"
            root = ElementTree.fromstring(environment[0].content)
            filesystem = root.find("filesystem")
            assert filesystem is not None, "effective filesystem omitted from environment"
            authority = filesystem.find("permission_profile")
            assert authority is not None and authority.attrib["type"] == profile
            access = authority.find("file_system")
            assert access is not None
            assert access.attrib["type"] == (
                "restricted" if profile == "managed" else "unrestricted"
            )
            if profile == "managed":
                assert any(
                    entry.attrib["access"] == "write" and entry.findtext("path") == str(tmp_path)
                    for entry in access.findall("entry")
                )
            assert root.find("network") is None, "no domain requirements were configured"
            thread_id = runtime.thread_id
        finally:
            await runtime.aclose()
        if change_on_reopen:
            permissions = (
                replace(permissions, profile_json='{"type":"disabled"}')
                if profile == "managed"
                else workspace_policy(compiler, tmp_path)
            )
        cold = await create(thread_id)
        try:
            events = [event async for event in cold.stream("inspect again after restart")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            environments = [
                item
                for item in requests[-1].items
                if isinstance(item, ContextItem) and item.key == "environment.primary"
            ]
            assert len(environments) == (2 if change_on_reopen else 1)
            assert environments[0].snapshot_state == environment[0].snapshot_state
            assert environments[0].content == environment[0].content
            if change_on_reopen:
                delta = ElementTree.fromstring(environments[1].content)
                assert delta.find("cwd") is None, "only effective authority changed"
                authority = delta.find("filesystem/permission_profile")
                assert authority is not None
                assert authority.attrib["type"] == (
                    "disabled" if profile == "managed" else "managed"
                )
        finally:
            await cold.aclose()

    asyncio.run(scenario())
