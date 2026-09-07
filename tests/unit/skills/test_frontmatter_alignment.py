import pytest

from corki.skills import SkillService


def discover(tmp_path, contents, policy=None):
    path = tmp_path / "skills" / "fallback" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    if policy is not None:
        sidecar = path.parent / "agents" / "openai.yaml"
        sidecar.parent.mkdir(exist_ok=True)
        sidecar.write_text(policy, encoding="utf-8")
    return SkillService(home=tmp_path, project_root=tmp_path, bundled_enabled=False).snapshot(
        tmp_path
    )


@pytest.mark.parametrize(
    "header,name,description",
    [
        ("description: Demo", "fallback", "Demo"),
        ("name: '  '\ndescription: Demo", "fallback", "Demo"),
        (
            "name: deploy   service\ndescription: Build for AWS: ECS",
            "deploy service",
            "Build for AWS: ECS",
        ),
        (
            "name: block\ndescription: |-\n  Build for AWS: ECS\n"
            "argument-hint: <duration: e.g. 7d>",
            "block",
            "Build for AWS: ECS",
        ),
        (
            "name: unknown\ndescription: Demo\nargument-hint: <duration: e.g. 7d>\n"
            "tags: [next,@supabase/ssr]",
            "unknown",
            "Demo",
        ),
        ("name: long\ndescription: " + "💡" * 1025, "long", "💡" * 1025),
        ("name: no\ndescription: 2026-09-07", "no", "2026-09-07"),
        ("name: 123\ndescription: false", "123", "false"),
    ],
    ids=[
        "default-name",
        "empty-name",
        "scalar-repair",
        "block-preserved",
        "unknown-fields",
        "long-description",
        "string-no-date",
        "string-number-bool",
    ],
)
def test_codex_frontmatter_acceptance_reaches_discovery(tmp_path, header, name, description):
    snapshot = discover(tmp_path, "---\n" + header + "\n---\nBODY")
    assert snapshot.errors == ()
    assert len(snapshot.skills) == 1
    assert snapshot.skills[0].name == name
    assert snapshot.skills[0].description == description


@pytest.mark.parametrize("spelling", ["no", "off", "NO", "OFF"])
def test_yaml_11_boolean_alias_is_not_a_false_policy(tmp_path, spelling):
    snapshot = discover(
        tmp_path,
        "---\nname: demo\ndescription: Demo\n---\n",
        f"policy:\n  allow_implicit_invocation: {spelling}",
    )
    assert len(snapshot.skills) == 1
    assert snapshot.skills[0].allow_implicit_invocation


@pytest.mark.parametrize(
    "value,allowed",
    [
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("'false'", True),
        ('!!bool "false"', False),
        ("!!str false", True),
        ("null", True),
        ("'null'", True),
    ],
)
def test_policy_boolean_scalar_style_and_explicit_tag(tmp_path, value, allowed):
    snapshot = discover(
        tmp_path, "---\ndescription: Demo\n---\n", f"policy:\n  allow_implicit_invocation: {value}"
    )
    assert snapshot.skills[0].allow_implicit_invocation is allowed


@pytest.mark.parametrize(
    "header",
    [
        "name: a\nname: b\ndescription: Demo",
        "description: [not, scalar]",
        "description: {bad}",
        "description: Demo\nmetadata: null",
        "description: Demo\nmetadata:\n  short-description: a\n  short-description: b",
    ],
)
def test_known_field_shape_and_duplicate_errors_are_not_repaired_away(tmp_path, header):
    snapshot = discover(tmp_path, "---\n" + header + "\n---\n")
    assert not snapshot.skills and len(snapshot.errors) == 1


def test_short_description_and_comments_survive_scalar_repair(tmp_path):
    snapshot = discover(
        tmp_path,
        "---\nname: demo\ndescription: Deploy: safe # comment\n"
        "metadata:\n  short-description: What's included: builds and tests # note\n---\n",
    )
    assert snapshot.skills[0].description == "Deploy: safe"
    assert snapshot.skills[0].short_description == "What's included: builds and tests"


def test_frontmatter_larger_than_old_prefix_limit_and_whole_line_delimiters(tmp_path):
    description = "💡" * 5000
    snapshot = discover(tmp_path, "  ---  \r\ndescription: " + description + "\r\n --- \r\nBODY")
    assert len(snapshot.skills) == 1 and snapshot.skills[0].description == description
    invalid = discover(tmp_path, "---not-a-delimiter\ndescription: Demo\n---\n")
    assert not invalid.skills


def test_explicit_string_null_is_not_an_absent_name(tmp_path):
    snapshot = discover(tmp_path, "---\nname: !!str null\ndescription: Demo\n---\n")
    assert snapshot.skills[0].name == "null"


def test_typed_mapping_error_can_trigger_codex_colon_scalar_repair(tmp_path):
    # The first typed read fails, but the line-oriented repair quotes the whole
    # value because it contains colon + whitespace, even though it is valid YAML.
    snapshot = discover(tmp_path, "---\ndescription: {bad: value}\n---\n")
    assert snapshot.errors == ()
    assert snapshot.skills[0].description == "{bad: value}"
