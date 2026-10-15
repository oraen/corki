from dataclasses import replace

import pytest

from corki.config import MCPEnvVar, MCPServerSettings
from corki.mcp.environment import CUSTOM_CA_KEYS, NON_INHERITABLE, build_stdio_environment
from corki.mcp.reconciliation import connection_identity


def setting(**changes):
    return replace(MCPServerSettings("docs", "stdio", command="fixture"), **changes)


def test_selected_values_and_literals_do_not_mutate_or_copy_other_inherited_variables():
    inherited = {
        "PATH": "/bin",
        "LANG": "C",
        "SELECTED": "value",
        "EMPTY": "",
        "PRIVATE": "hidden",
        "case": "small",
    }
    original = inherited.copy()
    settings = setting(
        env_vars=("SELECTED", "EMPTY", "MISSING", "case"),
        env=(("SELECTED", "literal"), ("CASE", "large")),
    )
    assert build_stdio_environment(settings, inherited=inherited, windows=False) == {
        "PATH": "/bin",
        "LANG": "C",
        "SELECTED": "literal",
        "EMPTY": "",
        "case": "small",
        "CASE": "large",
    }
    assert inherited == original


@pytest.mark.parametrize("windows", [False, True])
def test_platform_defaults_and_case_sensitive_or_insensitive_overrides(windows):
    settings = setting(env=(("Path", "override"),))
    actual = build_stdio_environment(
        settings,
        inherited={"PATH": "ambient", "COMSPEC": "cmd", "TERM": "term", "PRIVATE": "private"},
        windows=windows,
    )
    assert actual == (
        {"Path": "override", "COMSPEC": "cmd"}
        if windows
        else {
            "PATH": "ambient",
            "Path": "override",
            "TERM": "term",
        }
    )


@pytest.mark.parametrize("name", NON_INHERITABLE)
def test_internal_variables_cannot_be_exported_by_reference_or_literal_override(name):
    lower = name.lower()
    settings = setting(env_vars=(name, lower), env=((lower, "literal"), ("PUBLIC", "ok")))
    assert build_stdio_environment(
        settings, inherited={name: "ambient", lower: "other"}, windows=False
    ) == {"PUBLIC": "ok"}


@pytest.mark.parametrize("name", CUSTOM_CA_KEYS)
@pytest.mark.parametrize("override", [False, True])
def test_inherited_ca_paths_are_absolute_but_literal_ca_overrides_remain_verbatim(
    tmp_path, name, override
):
    lower = name.swapcase()
    settings = setting(env=((lower, "literal/../cert.pem"),) if override else ())
    actual = build_stdio_environment(
        settings, inherited={name: "relative/../cert.pem"}, parent_cwd=tmp_path, windows=False
    )
    assert actual == (
        {lower: "literal/../cert.pem"}
        if override
        else {name: str(tmp_path / "relative/../cert.pem")}
    )


def test_empty_ca_is_omitted_unless_explicitly_requested_or_overridden(tmp_path):
    inherited = {"NODE_EXTRA_CA_CERTS": ""}
    assert build_stdio_environment(setting(), inherited=inherited, parent_cwd=tmp_path) == {}
    assert (
        build_stdio_environment(setting(env_vars=("NODE_EXTRA_CA_CERTS",)), inherited=inherited)
        == inherited
    )
    assert (
        build_stdio_environment(setting(env=(("NODE_EXTRA_CA_CERTS", ""),)), inherited={})
        == inherited
    )


def test_windows_lookup_and_ca_override_eliminate_case_aliases(tmp_path):
    assert build_stdio_environment(
        setting(env_vars=("Custom",), env=(("npm_CONFIG_cafile", "override"),)),
        inherited={
            "Path": "path",
            "CUSTOM": "value",
            "NPM_CONFIG_CAFILE": "relative.pem",
        },
        parent_cwd=tmp_path,
        windows=True,
    ) == {"PATH": "path", "Custom": "value", "npm_CONFIG_cafile": "override"}


def test_local_launch_rejects_remote_reference_even_when_a_host_value_exists():
    with pytest.raises(ValueError, match="requires remote MCP stdio"):
        build_stdio_environment(
            setting(env_vars=(MCPEnvVar("REMOTE", "remote"),)), inherited={"REMOTE": "host-value"}
        )


def test_inherited_non_utf8_values_are_not_lossily_decoded():
    value = "prefix\udcffsuffix"
    assert build_stdio_environment(setting(), inherited={"PATH": value}, windows=False) == {
        "PATH": value
    }


def test_identity_preserves_reference_variant_order_and_config_literals(monkeypatch):
    monkeypatch.setenv("CORKI_MCP_IDENTITY_A", "a")
    monkeypatch.setenv("CORKI_MCP_IDENTITY_B", "b")
    original = setting(env_vars=("CORKI_MCP_IDENTITY_A", "CORKI_MCP_IDENTITY_B"))
    identity = connection_identity(original)
    for changed in (
        replace(original, env_vars=tuple(reversed(original.env_vars))),
        replace(original, env_vars=(MCPEnvVar("CORKI_MCP_IDENTITY_A"), "CORKI_MCP_IDENTITY_B")),
        replace(original, env=(("LITERAL", "new"),)),
    ):
        assert connection_identity(changed) != identity


def test_remote_reference_does_not_bind_host_value_in_identity(monkeypatch):
    remote = setting(env_vars=(MCPEnvVar("CORKI_MCP_REMOTE", "remote"),))
    monkeypatch.setenv("CORKI_MCP_REMOTE", "first")
    identity = connection_identity(remote)
    monkeypatch.setenv("CORKI_MCP_REMOTE", "second")
    assert connection_identity(remote) == identity
