"""Explicit plugin context uses current trusted bindings and bounded UTF-8 text."""

import pytest

from corki.config import MCPServerSettings
from corki.mcp.tools import MCPTool
from corki.plugins.context import PluginContextContributor
from corki.plugins.models import LoadedPlugin, PluginManifest
from corki.prompting import PromptRole
from corki.protocol import InputMention
from corki.protocol.tools import ToolExposure, ToolSpec
from corki.tools import ToolRegistry


def contributor(tmp_path, names, *, skills=False):
    return PluginContextContributor(
        (
            LoadedPlugin(
                PluginManifest(
                    "selected",
                    None,
                    None,
                    tmp_path,
                    skills_path=tmp_path / "skills" if skills else None,
                    mcp_servers=tuple(
                        MCPServerSettings(n, "http", url="https://unused.test") for n in names
                    ),
                )
            ),
        )
    )


def render(contributor, tmp_path, registry):
    return contributor.input_snapshot_contributions(
        cwd=tmp_path,
        user_input="",
        mentions=(InputMention("not the identity", "plugin://selected?app=desktop"),),
        tool_snapshot=registry.snapshot(),
    )


@pytest.mark.parametrize("exposure", list(ToolExposure))
def test_only_available_nonhidden_bindings_are_advertised(tmp_path, exposure):
    registry = ToolRegistry()
    registry.register(
        MCPTool("ready", {"name": "lookup"}, object(), exposure=exposure, plugin_id="selected")
    )
    fragments = render(contributor(tmp_path, ("pending", "ready")), tmp_path, registry)
    if exposure is ToolExposure.HIDDEN:
        assert fragments == ()
        return
    assert len(fragments) == 1
    fragment = fragments[0]
    assert fragment.role is PromptRole.DEVELOPER
    assert fragment.content_kind == "plugins.instructions"
    assert fragment.input_scoped and fragment.separate_message
    assert "`ready`" in fragment.variables["contents"]
    assert "pending" not in fragment.variables["contents"]


def test_untrusted_spec_source_cannot_claim_available_plugin_server(tmp_path):
    class Untrusted:
        spec = ToolSpec("ordinary", "not an MCP binding", {}, source="pending")

    registry = ToolRegistry()
    registry.register(Untrusted())
    assert render(contributor(tmp_path, ("pending",)), tmp_path, registry) == ()


def test_explicit_hint_is_utf8_bounded_and_has_truncation_suffix(tmp_path):
    names = tuple(f"{i:03}-" + "界" * 60 for i in range(40))
    registry = ToolRegistry()
    for name in names:
        registry.register(MCPTool(name, {"name": "lookup"}, object(), plugin_id="selected"))
    (fragment,) = render(contributor(tmp_path, names), tmp_path, registry)
    contents = fragment.variables["contents"]
    assert 4000 < len(contents.encode("utf-8")) <= 4096
    assert contents.endswith("Additional plugin capabilities omitted to fit the context limit.")
    assert "\ufffd" not in contents


def test_skills_alone_are_a_capability_and_unselected_plugins_are_not_injected(tmp_path):
    source = contributor(tmp_path, (), skills=True)
    (fragment,) = render(source, tmp_path, ToolRegistry())
    assert "`selected:`" in fragment.variables["contents"]
    assert source.input_contributions(cwd=tmp_path, user_input="selected") == ()
