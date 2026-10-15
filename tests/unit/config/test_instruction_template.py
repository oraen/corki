import pytest

from corki.config.model_context import parse_model_contexts
from corki.protocol.instruction_template import ModelInstructionTemplate


@pytest.mark.parametrize(
    "selected, expected", [(None, "D"), ("none", ""), ("friendly", "F"), ("pragmatic", "P")]
)
def test_template_selection_preserves_default_vs_disabled(selected, expected):
    template = ModelInstructionTemplate("{{ personality }}", True, "D", "F", "P")
    assert template.supports_personality
    assert template.render(selected) == expected


@pytest.mark.parametrize(
    "messages",
    [
        {"instructions_template": False},
        {"instructions_variables": []},
        {"instructions_variables": {"personality_default": 1}},
        {"instructions_template": "界" * 10_001},
        {
            "instructions_template": "{{ personality }}" * 3,
            "instructions_variables": {"personality_friendly": "界" * 4_000},
        },
    ],
)
def test_invalid_or_expanding_template_rejected_before_dispatch(messages):
    with pytest.raises(ValueError):
        parse_model_contexts({"local": {"model_messages": messages}})


def test_catalog_fixed_base_override_and_permission_only_compatibility():
    info = parse_model_contexts(
        {
            "local": {
                "base_instructions": "FIXED",
                "model_messages": {"instructions_template": "TEMPLATE"},
            }
        }
    )[0]
    assert info.get_model_instructions() == "FIXED"
    assert (
        parse_model_contexts({"local": {"model_messages": {}}})[0].get_model_instructions() is None
    )


@pytest.mark.parametrize("personality", ["none", "friendly", "pragmatic"])
def test_personality_toml_selection(tmp_path, personality):
    from corki.config import CorkiSettings

    config = tmp_path / "config.toml"
    config.write_text(f'[agent]\npersonality = "{personality}"\n', encoding="utf-8")
    configured = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert configured.personality == personality


@pytest.mark.parametrize("invalid", ["", "unknown", False, 1, {}])
def test_personality_rejects_invalid_host_and_restored_selections(tmp_path, invalid):
    from corki.config import CorkiSettings
    from corki.core.model_settings import capture_model_settings
    from corki.protocol.settings import ModelSettingsSnapshot

    with pytest.raises(ValueError, match="personality"):
        CorkiSettings(working_directory=tmp_path, personality=invalid)
    snapshot = capture_model_settings(CorkiSettings(working_directory=tmp_path))
    payload = snapshot.to_payload()
    payload["personality"] = invalid
    with pytest.raises(ValueError, match="personality"):
        ModelSettingsSnapshot.from_payload(payload)
    del payload["personality"]
    assert ModelSettingsSnapshot.from_payload(payload) == snapshot


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_explicit_none_removes_only_first_personality_h1_section(ending):
    text = ending.join(
        [
            "BASE",
            "# Personality",
            "{{ personality }}",
            "## Subheading",
            "DETAIL",
            "#\tRules",
            "KEEP",
        ]
    )
    template = ModelInstructionTemplate(text, True, "D", "F", "P")
    assert template.render("none") == ending.join(["BASE", "#\tRules", "KEEP"])
    assert template.render("none", enabled=False) == text.replace("{{ personality }}", "D")


@pytest.mark.parametrize("enabled", [False, True])
def test_feature_personality_toml(tmp_path, enabled):
    from corki.config import CorkiSettings

    config = tmp_path / "config.toml"
    config.write_text(f"[features]\npersonality = {str(enabled).lower()}\n", encoding="utf-8")
    assert CorkiSettings.for_directory(tmp_path, config_file=config).personality_enabled is enabled
