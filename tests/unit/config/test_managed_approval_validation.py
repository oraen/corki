"""Early allocation guards must preserve native layer-vs-effective validation."""

import json

import pytest

from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.config.managed_mcp import (
    MCPRequirementsLayer,
    MCPRequirementsSnapshot,
    compose_mcp_requirements,
)
from corki.config.mcp_requirements import MCPRequirements


@pytest.mark.parametrize("empty_wins", [False, True])
@pytest.mark.parametrize("entry", ["toml", "snapshot"])
def test_only_effective_empty_list_is_rejected_with_winning_source(empty_wins, entry):
    values = (["never"], []) if empty_wins else ([], ["never"])

    def compose():
        if entry == "toml":
            return compose_mcp_requirements(
                [
                    MCPRequirementsLayer(
                        source, "allowed_approval_policies=" + json.dumps(policies)
                    )
                    for source, policies in zip(("low", "high"), values, strict=True)
                ]
            )
        return MCPRequirementsSnapshot(
            MCPRequirements(),
            execution=tuple(
                ExecutionRequirementsLayer(
                    source, json.dumps({"allowed_approval_policies": policies})
                )
                for source, policies in zip(("low", "high"), values, strict=True)
            ),
        )

    if empty_wins:
        with pytest.raises(ValueError, match="high.*allowed_approval_policies.*empty"):
            compose()
    else:
        result = compose()
        assert [
            json.loads(layer.value_json)["allowed_approval_policies"] for layer in result.execution
        ] == [[], ["never"]]


@pytest.mark.parametrize("bad", ['["unknown"]', "[false]", "[{granular={rules=true}}]"])
def test_invalid_layer_type_cannot_be_repaired_by_later_policy(bad):
    with pytest.raises(ValueError, match="bad-low.*allowed_approval_policies"):
        compose_mcp_requirements(
            [
                MCPRequirementsLayer("bad-low", "allowed_approval_policies=" + bad),
                MCPRequirementsLayer("valid-high", 'allowed_approval_policies=["never"]'),
            ]
        )


def test_early_shape_check_does_not_rewrite_native_policy_input():
    contents = (
        'allowed_approval_policies=["on-failure",'
        '{granular={sandbox_approval=true,rules=false,mcp_elicitations=true,future_field="ignored"}}]'
    )
    snapshot = compose_mcp_requirements([MCPRequirementsLayer("host", contents)])
    original = json.loads(snapshot.execution[0].value_json)["allowed_approval_policies"]
    assert original == [
        "on-failure",
        {
            "granular": {
                "sandbox_approval": True,
                "rules": False,
                "mcp_elicitations": True,
                "future_field": "ignored",
            }
        },
    ]
