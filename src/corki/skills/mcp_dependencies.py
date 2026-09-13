"""Host-compatible skill dependency installation, independent of tool-call consent."""

import asyncio
import logging
from contextlib import suppress
from pathlib import Path

from corki.config import MCPServerSettings
from corki.config.mcp_headers import RUST_WHITESPACE
from corki.config.toml_edits import add_missing_mcp_servers
from corki.execution.backend import mcp_dependency_approval
from corki.mcp.oauth_discovery import discover_oauth
from corki.mcp.oauth_login import login_oauth
from corki.protocol.items import UserMessageItem
from corki.skills.io import run_skill_io

_LOG = logging.getLogger(__name__)


def _key(server):
    value = server.command if server.transport == "stdio" else server.url
    return server.transport, (value or "").strip(RUST_WHITESPACE) or server.name


def _selected_dependencies(items, state, service, cwd):
    seen = set(state.get("mcp_requirement_input_ids", ()))
    dependencies = []
    for item in items:
        if (
            not isinstance(item, UserMessageItem)
            or item.turn_id != state["turn_id"]
            or item.retained_from_id is not None
            or item.id in seen
        ):
            continue
        seen.add(item.id)
        for skill in service.explicit_mentions(
            item.content,
            cwd,
            mentions=item.mentions,
        ):
            dependencies.extend(skill.dependencies)
    return tuple(dependencies)


class SkillMCPDependencies:
    """Session-owned prompted identities; neither model input nor remote metadata grants access."""

    def __init__(self):
        self._prompted = set()

    async def install(self, runtime, state, items):
        settings = runtime._graph._turn_settings
        if (
            not settings.skill_mcp_dependency_install
            or not settings.orchestrator_mcp_enabled
            or not settings.skills_enabled
            or runtime._skill_service is None
            or runtime._session_source.is_basic_guardian
        ):
            return
        try:
            dependencies = await run_skill_io(
                _selected_dependencies,
                tuple(items),
                dict(state),
                runtime._skill_service,
                Path(state["cwd"]),
            )
            await self._install(runtime, settings, dependencies)
        except Exception as error:
            # Optional dependency setup must not discard the selected skill body.
            # Cancellation is BaseException and must reach the Turn owner.
            _LOG.warning("Skill MCP dependency installation failed: %s", type(error).__name__)

    async def _install(self, runtime, settings, dependencies):
        manager = runtime._mcp_manager
        installed = {_key(entry.settings) for entry in manager.skill_dependency_catalog().servers}
        seen = set(installed)
        candidates, documents = {}, {}
        for dependency in dependencies:
            if dependency.type.lower() != "mcp":
                continue
            transport = (dependency.transport or "streamable_http").lower()
            if transport not in {"stdio", "streamable_http"}:
                _LOG.warning("Ignoring unsupported skill MCP dependency transport")
                continue
            field = "command" if transport == "stdio" else "url"
            value = getattr(dependency, field)
            if value is None:
                _LOG.warning("Ignoring skill MCP dependency without %s", field)
                continue
            document = {"transport": "stdio" if transport == "stdio" else "http", field: value}
            if transport == "streamable_http" and dependency.oauth_callback_port is not None:
                document["oauth"] = {"callback_port": dependency.oauth_callback_port}
            try:
                candidate = MCPServerSettings.from_mapping(dependency.value, document)
            except ValueError:
                _LOG.warning("Ignoring invalid skill MCP dependency")
                continue
            identity = _key(candidate)
            if identity in seen or identity in self._prompted:
                continue
            seen.add(identity)
            candidates[candidate.name] = candidate
            documents[candidate.name] = document
        candidates = self._admit(manager, candidates)
        if not candidates:
            return
        permissions = settings.execution_permissions
        if permissions is None:
            policy = settings.mcp_approval_policy
            automatic = policy == "never"
        else:
            policy, automatic = await mcp_dependency_approval(permissions)
        if not automatic:
            if policy == "never":
                return
            response = await manager.elicitations.request_skill_dependency_install(
                tuple(candidates)
            )
            self._prompted.update(_key(server) for server in candidates.values())
            if response.get("action") != "accept":
                return
        # No await between the second admission and starting the owned persistence task.
        candidates = self._admit(manager, candidates)
        if not candidates:
            return
        persistence = manager._approval_persistence
        if persistence is None:
            return
        path = next(
            layer.file
            for layer in reversed(persistence.configuration.layers)
            if layer.kind == "user"
        )
        writer = asyncio.create_task(
            asyncio.to_thread(
                add_missing_mcp_servers,
                path,
                {name: documents[name] for name in candidates},
            ),
            name="skill-mcp-dependency-write",
        )
        cancelled = False
        while not writer.done():
            try:
                await asyncio.shield(writer)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            # Retrieve even a late failure; cancellation remains the owner's outcome.
            with suppress(Exception):
                writer.result()
            raise asyncio.CancelledError
        installed = writer.result()
        if not installed.added:
            return
        # Proactive discovery belongs to installation, not just the MCP 401 path.
        for name in installed.added:
            admitted = self._admit(manager, candidates)
            if name not in admitted:
                continue
            try:
                selected = admitted[name]
                context = manager._runtime_context
                home = manager._oauth_file_home or manager._oauth_home
                mode = manager._oauth_store_mode
                discovery = await discover_oauth(selected, context)
                if discovery is not None and home is not None:

                    def is_admitted(
                        context=context, home=home, name=name, selected=selected, mode=mode
                    ):
                        return (
                            not manager._closed
                            and manager._runtime_context == context
                            and (manager._oauth_file_home or manager._oauth_home) == home
                            and manager._oauth_store_mode == mode
                            and self._admit(manager, candidates).get(name) == selected
                        )

                    await login_oauth(
                        selected,
                        context,
                        home,
                        manager.elicitations,
                        discovery,
                        is_admitted=is_admitted,
                        store_mode=mode,
                    )
            except Exception as error:
                _LOG.warning("Skill MCP OAuth discovery failed: %s", type(error).__name__)
            else:
                if discovery is not None and home is None:
                    _LOG.warning(
                        "Skill MCP dependency supports OAuth, "
                        "but the login flow is not yet available"
                    )
        # Revalidate after the blocking write before publishing executable authority.
        admitted = self._admit(manager, candidates)
        # Native installation merges the saved global snapshot, not just newly
        # installed candidates. Existing Runtime names still win in the manager;
        # all declarations pass its current policy/environment checks at capture.
        additions = manager.publish_skill_dependencies(
            tuple(
                server
                for server in installed.servers
                if server.name not in installed.added or server.name in admitted
            )
        )
        if runtime._plugin_catalog_base is not None:
            runtime._plugin_catalog_base = runtime._plugin_catalog_base.extend(*additions)

    @staticmethod
    def _admit(manager, candidates):
        view = manager.skill_dependency_catalog(tuple(candidates.values()))
        return {
            entry.name: candidates[entry.name]
            for entry in view.servers
            if entry.name in candidates
            and entry.settings.enabled
            and entry.settings == candidates[entry.name]
        }
