"""Canonical host approval policy JSON shared by configuration and acknowledgements."""

import json

from corki.protocol.wire_json import check_fields, loads_wire


def normalize_approval_policy(value: str) -> str:
    if not isinstance(value, str) or len(value) > 1_000_000:
        raise ValueError("approval policy must be host-owned JSON")
    approval = loads_wire(value)
    if approval == "on-failure":
        approval = "on-request"
    if not isinstance(approval, str) or approval not in {"never", "on-request", "untrusted"}:
        if not isinstance(approval, dict) or set(approval) != {"granular"}:
            raise ValueError("invalid execution approval policy")
        check_fields(approval, {"granular"})
        granular = approval["granular"]
        required = {"sandbox_approval", "rules", "mcp_elicitations"}
        fields = required | {"skill_approval", "request_permissions"}
        check_fields(granular, fields)
        if (
            not isinstance(granular, dict)
            or not required <= granular.keys()
            or any(type(granular[key]) is not bool for key in fields & granular.keys())
        ):
            raise ValueError("invalid granular execution approval policy")
        # Preserve native duplicate-known-field rejection and unknown-field tolerance.
        granular = {key: granular[key] for key in fields if key in granular}
        granular.setdefault("skill_approval", False)
        granular.setdefault("request_permissions", False)
        approval = {"granular": granular}
    return json.dumps(approval, sort_keys=True)
