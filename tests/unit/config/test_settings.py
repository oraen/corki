from pathlib import Path

import pytest

from corki.config import CorkiPaths, CorkiSettings


def test_cli_live_text_defaults_on_and_preserves_explicit_opt_out(tmp_path):
    assert CorkiSettings(tmp_path).realtime_enabled is True
    paths = CorkiPaths.from_home(tmp_path / "home")
    paths.ensure_exists()
    assert CorkiSettings.for_directory(tmp_path, config_file=paths.config_file).realtime_enabled
    paths.config_file.write_text("[realtime]\nenabled = false\n")
    paths.ensure_exists()
    assert not CorkiSettings.for_directory(tmp_path, config_file=paths.config_file).realtime_enabled


@pytest.mark.parametrize("value", [True, False, "obsolete", None])
def test_removed_remote_compaction_setting_is_inert(tmp_path, value):
    assert CorkiSettings(tmp_path, remote_compaction_v2=value).remote_compaction_v2 is False


@pytest.mark.parametrize("value", ["true", "false", '"obsolete"'])
def test_removed_remote_compaction_toml_is_ignored(tmp_path, value):
    config = tmp_path / "config.toml"
    config.write_text(f"[features]\nremote_compaction_v2={value}\n")
    assert CorkiSettings.for_directory(tmp_path, config_file=config).remote_compaction_v2 is False


@pytest.mark.parametrize("enabled", [False, True])
def test_step_model_switching_loads_explicit_feature_boolean(tmp_path, enabled):
    config = tmp_path / "config.toml"
    config.write_text(f"[features]\nstep_model_switching={str(enabled).lower()}\n")
    assert CorkiSettings.for_directory(tmp_path, config_file=config).step_model_switching is enabled


@pytest.mark.parametrize("value", [0, 1, "true", None, []])
def test_step_model_switching_rejects_non_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="step_model_switching"):
        CorkiSettings(tmp_path, step_model_switching=value)


@pytest.mark.parametrize("servers", [None, [], ["docs"]])
def test_non_prefixed_mcp_configuration_preserves_absent_vs_empty(tmp_path, servers):
    config = tmp_path / "config.toml"
    listing = "" if servers is None else f"non_prefixed_mcp_tool_servers={servers!r}\n"
    config.write_text("[tools]\nnon_prefixed_mcp_tool_names=true\n" + listing)
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.non_prefixed_mcp_tool_names
    assert settings.non_prefixed_mcp_tool_servers == (None if servers is None else tuple(servers))


@pytest.mark.parametrize(
    "values",
    [
        {"non_prefixed_mcp_tool_names": "true"},
        {"non_prefixed_mcp_tool_names": 1},
        {"non_prefixed_mcp_tool_servers": "docs"},
        {"non_prefixed_mcp_tool_servers": [1]},
    ],
)
def test_non_prefixed_mcp_configuration_rejects_invalid_types(tmp_path, values):
    with pytest.raises(ValueError, match="non_prefixed_mcp"):
        CorkiSettings(working_directory=tmp_path, **values)


def test_deferred_world_state_defaults_off_and_loads_boolean(tmp_path):
    assert CorkiSettings(working_directory=tmp_path).deferred_tool_world_state is False
    config = tmp_path / "config.toml"
    config.write_text("[tools]\ndeferred_tool_world_state=true\n")
    assert (
        CorkiSettings.for_directory(tmp_path, config_file=config).deferred_tool_world_state is True
    )


@pytest.mark.parametrize("value", [0, 1, "true", None, []])
def test_deferred_world_state_rejects_non_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="deferred_tool_world_state"):
        CorkiSettings(working_directory=tmp_path, deferred_tool_world_state=value)


@pytest.mark.parametrize("mode", ["compatible", "native", "disabled"])
def test_tool_search_mode_loads_from_configuration(tmp_path: Path, mode: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'[provider]\napi_mode="responses"\n[tools]\nsearch_mode="{mode}"\n')
    assert CorkiSettings.for_directory(tmp_path, config_file=config).tool_search_mode == (
        "compatible" if mode == "native" else mode
    )


@pytest.mark.parametrize("mode", ["unknown", None, []])
def test_invalid_search_modes_fail_with_configuration_error(tmp_path: Path, mode) -> None:
    with pytest.raises(ValueError, match="search_mode"):
        CorkiSettings(working_directory=tmp_path, tool_search_mode=mode)


def test_old_native_search_migrates_for_chat_provider(tmp_path: Path) -> None:
    assert (
        CorkiSettings(working_directory=tmp_path, tool_search_mode="native").tool_search_mode
        == "compatible"
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_runtime_durations_are_rejected(tmp_path: Path, value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        CorkiSettings(working_directory=tmp_path, command_timeout_seconds=value)


def test_settings_load_toml_and_environment_override(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """[agent]
model = "from-file"
max_steps = 12
[provider]
base_url = "https://example.test/v1/"
api_mode = "chat_completions"
thinking = false
reasoning_effort = "high"
"""
    )
    monkeypatch.setenv("CORKI_MODEL", "from-env")
    monkeypatch.setenv("CORKI_API_KEY", "secret")

    settings = CorkiSettings.for_directory(tmp_path, config_file=config)

    assert settings.model == "from-env"
    assert settings.api_base == "https://example.test/v1"
    assert settings.api_key == "secret"
    assert settings.api_mode == "chat_completions"
    assert settings.thinking_enabled is False
    assert settings.reasoning_effort == "high"
    assert settings.max_steps == 12


def test_settings_reject_non_positive_limits(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("[agent]\nmax_steps = 0\n")

    with pytest.raises(ValueError, match="max_steps"):
        CorkiSettings.for_directory(tmp_path, config_file=config)


def test_settings_accepts_disabled_retries_and_responses_mode(tmp_path: Path) -> None:
    settings = CorkiSettings(
        working_directory=tmp_path,
        api_mode="responses",
        model_max_retries=0,
    )

    assert settings.api_mode == "responses"
    assert settings.model_max_retries == 0


@pytest.mark.parametrize("value", ["true", "false"])
def test_connection_retry_switch_loads_from_toml(tmp_path, value):
    config = tmp_path / "config.toml"
    config.write_text(f"[provider]\nunbounded_connection_retries = {value}\n")
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.model_unbounded_connection_retries is (value == "true")


@pytest.mark.parametrize("value", ["'false'", "0", "[]"])
def test_connection_retry_switch_rejects_non_booleans(tmp_path, value):
    config = tmp_path / "config.toml"
    config.write_text(f"[provider]\nunbounded_connection_retries = {value}\n")
    with pytest.raises(ValueError, match="unbounded_connection_retries"):
        CorkiSettings.for_directory(tmp_path, config_file=config)


def test_settings_loads_skills_plugins_mcp_and_realtime(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """[skills]
enabled = false
[plugins]
directories = ["./team-plugins"]
disabled = ["legacy"]
[realtime]
enabled = true
max_inputs_per_turn = 3
[memories]
enabled = true
generate = false
use = true
dedicated_tools = true
max_threads_per_startup = 7
min_thread_idle_hours = 0
[mcp.servers.docs]
transport = "http"
url = "https://example.test/mcp"
timeout_seconds = 9
""",
        encoding="utf-8",
    )

    settings = CorkiSettings.for_directory(tmp_path, config_file=config)

    assert settings.skills_enabled is False
    assert settings.plugin_dirs == (tmp_path / "team-plugins",)
    assert settings.disabled_plugins == frozenset({"legacy"})
    assert settings.realtime_enabled is True
    assert settings.max_realtime_inputs == 3
    assert settings.memories_enabled is True
    assert settings.memories_generate is False
    assert settings.memories_use is True
    assert settings.memories_dedicated_tools is True
    assert settings.memories_max_threads_per_startup == 7
    assert settings.memories_min_thread_idle_hours == 0
    assert settings.mcp_servers[0].name == "docs"
    assert settings.mcp_servers[0].timeout_seconds == 9


@pytest.mark.parametrize(
    "document",
    [
        "[plugins]\ndirectories = 'wrong'\n",
        "[plugins]\ndisabled = 'wrong'\n",
        "[mcp.servers.bad]\ntransport = 'stdio'\n",
        "[realtime]\nmax_inputs_per_turn = 0\n",
        "[memories]\nmax_threads_per_startup = 0\n",
        "[memories]\nmax_raw_for_consolidation = 4097\n",
        "[memories]\nmin_thread_idle_hours = -1\n",
    ],
)
def test_optional_capability_configuration_fails_fast(tmp_path: Path, document: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text(document, encoding="utf-8")

    with pytest.raises(ValueError):
        CorkiSettings.for_directory(tmp_path, config_file=config)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ("[agent]\nmax_steps = 1.5\n", "integers"),
        ("[agent]\nmodel = ''\n", "model"),
        ("[provider]\nbase_url = 'not-a-url'\n", "base_url"),
        ("[provider]\nmax_retries = -1\n", "max_retries"),
        ("[runtime]\nevent_queue_size = -1\n", "event_queue_size"),
        ("[runtime]\nevent_queue_size = true\n", "integers"),
        ("[runtime]\nevent_queue_size = 1.5\n", "integers"),
    ],
)
def test_settings_rejects_invalid_known_types_and_values(
    tmp_path: Path, document: str, expected: str
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(document)

    with pytest.raises(ValueError, match=expected):
        CorkiSettings.for_directory(tmp_path, config_file=config)
