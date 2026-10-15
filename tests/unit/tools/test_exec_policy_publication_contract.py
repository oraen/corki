"""Disk acknowledgement alone cannot authorize publication of a live rule."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.config.exec_policy import ExecPolicySnapshot, ExecPolicySource
from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.config.permissions import ExecutionPermissions
from corki.execution import rules
from corki.execution.rules import ExecutionRuleUpdates


@pytest.mark.parametrize(
    "ack",
    [
        {},
        {"execpolicy_amendment_published": None},
        {"execpolicy_amendment_published": 0},
        {"execpolicy_amendment_published": 1},
        {"execpolicy_amendment_published": "false"},
        {"execpolicy_amendment_published": []},
    ],
)
def test_missing_or_untyped_publication_ack_never_publishes_or_retries(tmp_path, monkeypatch, ack):
    async def scenario():
        owner = ExecutionRuleUpdates()
        owner.path = tmp_path / "default.rules"
        calls, warnings = [], []

        async def compile(argv, payload, **kwargs):
            calls.append(json.loads(payload))
            return json.dumps({"ok": {"execpolicy_amendment_written": True, **ack}}).encode()

        async def warn(message):
            warnings.append(message)

        monkeypatch.setattr(rules, "run_owned", compile)
        await owner.persist(
            ExecutionPermissions(tmp_path / "compiler", tmp_path, '{"type":"read-only"}'),
            ("touch",),
            warn,
        )
        assert len(calls) == len(warnings) == 1
        assert "current-policy publication" in warnings[0]
        assert not owner.prefixes

    asyncio.run(scenario())


@pytest.mark.parametrize("matches", [True, False])
def test_current_policy_payload_uses_captured_sources_and_managed_layers(
    tmp_path, monkeypatch, matches
):
    async def scenario():
        owner = ExecutionRuleUpdates()
        owner.path = tmp_path / "default.rules"
        declared = ExecPolicySource(str(tmp_path / "declared.rules"), "DECLARED")
        captured = ExecPolicySource(str(tmp_path / "captured.rules"), "CAPTURED")
        managed = ExecutionRequirementsLayer("managed", '{"rules":{}}', tmp_path)
        permissions = ExecutionPermissions(
            tmp_path / "compiler",
            tmp_path,
            '{"type":"read-only"}',
            exec_policy_sources=(declared,),
            requirements=(managed,),
        )
        permissions = replace(
            permissions,
            exec_policy_snapshot=ExecPolicySnapshot(
                (tmp_path,),
                (declared,) if matches else (),
                (captured,),
                ("opaque native identity",),
            ),
        )
        requests = []

        async def compile(argv, payload, **kwargs):
            requests.append(json.loads(payload)["append_execpolicy"])
            return json.dumps(
                {
                    "ok": {
                        "execpolicy_amendment_written": True,
                        "execpolicy_amendment_published": False,
                    }
                }
            ).encode()

        monkeypatch.setattr(rules, "run_owned", compile)
        await owner.persist(permissions, ("touch",))
        source = captured if matches else declared
        assert requests[0]["current_policy"] == {
            "sources": [{"name": source.name, "contents": source.contents}],
            "approved_prefixes": [],
            "requirements": [
                {"source": "managed", "base_dir": str(tmp_path), "value": {"rules": {}}}
            ],
        }
        assert not owner.prefixes

    asyncio.run(scenario())
