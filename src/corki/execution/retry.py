"""Host-owned native startup retry evidence; never model-provided permission flags."""

import json
from dataclasses import dataclass

from corki.config.permissions import ExecutionPermissions
from corki.execution.approvals import ExecApprovalRequirement
from corki.execution.owned_process import run_owned
from corki.execution.response import decode_helper_response
from corki.execution.terminal import TerminalSnapshot


@dataclass(frozen=True, slots=True)
class SandboxRetryPlan:
    sandbox: str
    command: tuple[str, ...] | None
    approval: ExecApprovalRequirement | None


@dataclass(slots=True)
class SandboxAdmission:
    plan: SandboxRetryPlan | None = None
    approved: bool = False
    terminal_snapshot: TerminalSnapshot | None = None
    require_terminal_snapshot: bool = False


def parse_retry_plan(
    value: dict, argv: list[str], *, model_command: bool, approval_policy_json: str
) -> SandboxRetryPlan | None:
    if "sandbox_retry_supported" not in value:
        if "sandbox_retry" in value:
            raise ValueError("sandbox retry plan has no capability acknowledgement")
        return None  # Older compiler: retain the existing no-automatic-retry behavior.
    if value["sandbox_retry_supported"] is not True:
        raise ValueError("invalid sandbox retry capability")
    raw = value["sandbox_retry"]
    if not model_command:
        if raw is not None:
            raise ValueError("unexpected sandbox retry outside model command admission")
        return None
    if not isinstance(raw, dict) or set(raw) != {"sandbox", "command", "approval"}:
        raise ValueError("invalid sandbox retry plan")
    sandbox, command, approval = raw["sandbox"], raw["command"], raw["approval"]
    if (
        sandbox not in ("none", "seatbelt", "seccomp", "windows_sandbox")
        or sandbox != value["sandbox"]
    ):
        raise ValueError("invalid actual sandbox in retry plan")
    if command is None:
        if approval is not None:
            raise ValueError("unexpected approval without sandbox retry command")
        return SandboxRetryPlan(sandbox, None, None)
    if command != argv or sandbox == "none":
        raise ValueError("sandbox retry must retain the original sandboxed command")
    policy = json.loads(approval_policy_json)
    if policy != "untrusted" and not (
        isinstance(policy, dict) and policy["granular"]["sandbox_approval"] is True
    ):
        raise ValueError("sandbox retry contradicts the admitted approval policy")
    if not isinstance(approval, dict) or set(approval) != {
        "canonical_command",
        "policy_fingerprint",
    }:
        raise ValueError("invalid sandbox retry approval")
    canonical, fingerprint = approval["canonical_command"], approval["policy_fingerprint"]
    if (
        not isinstance(canonical, list)
        or not canonical
        or any(not isinstance(word, str) or "\0" in word for word in canonical)
    ):
        raise ValueError("invalid canonical retry command")
    if fingerprint is not None and (
        not isinstance(fingerprint, list) or any(not isinstance(word, str) for word in fingerprint)
    ):
        raise ValueError("invalid retry policy fingerprint")
    return SandboxRetryPlan(
        sandbox,
        tuple(command),
        ExecApprovalRequirement(
            "command failed; retry without sandbox?",
            None if fingerprint is None else tuple(fingerprint),
            tuple(canonical),
        ),
    )


async def is_sandbox_denial(
    permissions: ExecutionPermissions, sandbox: str, exit_code: int, output: str
) -> bool:
    """Ask the pinned predicate, without launching or replaying any model command."""
    response = await run_owned(
        [str(permissions.compiler)],
        (
            json.dumps(
                {
                    "classify_sandbox_denial": {
                        "sandbox": sandbox,
                        "exit_code": exit_code,
                        "output": output,
                    }
                }
            )
            + "\n"
        ).encode(),
        cwd=permissions.policy_cwd,
        output_limit=16_384,
    )
    try:
        value = decode_helper_response(
            response,
            expected=dict,
            error_prefix="sandbox compiler failed to classify command denial",
        )
        if set(value) != {"revision", "sandbox_denial"}:
            raise ValueError("invalid sandbox denial classification fields")
        if type(value["revision"]) is not int or value["revision"] != 1:
            raise ValueError("invalid sandbox denial revision")
        if type(value["sandbox_denial"]) is not bool:
            raise ValueError("invalid sandbox denial classification")
        return value["sandbox_denial"]
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("sandbox compiler failed to classify command denial") from exc
