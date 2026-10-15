"""Host-only shell consent, separate from MCP authorization and sandbox authority."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from corki.execution.rules import ExecutionRuleUpdates

SandboxPermissions = Literal["use_default", "require_escalated"]


@dataclass(frozen=True, slots=True)
class ExecApprovalRequirement:
    """Native policy evidence for one command requiring user review."""

    reason: str | None
    policy_fingerprint: tuple[str, ...] | None
    canonical_command: tuple[str, ...] | None = None
    proposed_execpolicy_amendment: tuple[str, ...] | None = None


class ExecutionApprovals:
    """Session-owned consent bound to the original command and permission intent."""

    def __init__(self):
        # MCP's package initializer loads tool adapters. Defer this shared host
        # transport import until construction so backend-first imports stay acyclic.
        from corki.mcp.elicitation import ElicitationRouter

        self.router = ElicitationRouter()
        self._session: set[tuple] = set()
        self.rules = ExecutionRuleUpdates()

    async def authorize_patch(
        self, review: dict, *, call_id: str, retry: dict | None = None
    ) -> None:
        """Native patch cache: environment + every source/destination file."""
        keys = {("patch", "local", path) for path in review["files"]}
        if retry is None and keys and keys <= self._session:
            return
        if self.router.handler is None:
            raise ValueError("patch approval required; no host approval handler is available")
        try:
            response = await self.router.request_patch_approval(
                {
                    "message": (
                        "Sandbox denied the patch. Retry the original patch once without sandbox? "
                        "Prior changes were not rolled back; inspect the execution evidence and "
                        "committed delta before approving."
                        if retry is not None
                        else "Approve the proposed file changes?"
                    ),
                    "mode": "form",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {
                            "scope": {
                                "type": "string",
                                "title": "Approval scope",
                                "enum": ["once"] if retry is not None else ["once", "session"],
                                "default": "once",
                            }
                        },
                    },
                    "_meta": {
                        "codex_approval_kind": "apply_patch",
                        "call_id": call_id,
                        "tool_params": review,
                        **({"patch_retry": retry} if retry is not None else {}),
                    },
                }
            )
        except Exception as error:
            raise ValueError(f"patch approval failed: {type(error).__name__}") from error
        if response.get("action") != "accept":
            raise ValueError(
                "patch approval aborted"
                if response.get("action") == "cancel"
                else "patch approval rejected"
            )
        content = response.get("content")
        if isinstance(content, dict):
            if content.get("execpolicy_amendment") is not None:
                raise ValueError("patch approval cannot save an execution rule")
            if content.get("remember") is True:
                if retry is not None:
                    raise ValueError("patch retry requires one-time approval, not session consent")
                self._session.update(keys)

    async def authorize(
        self,
        requirement: ExecApprovalRequirement,
        argv: list[str],
        cwd: Path,
        *,
        call_id: str,
        tty: bool,
        sandbox_permissions: SandboxPermissions = "use_default",
        justification: str | None = None,
    ) -> tuple[str, ...] | None:
        # Keep the requested intent even if denied reads force a sandboxed launch.
        # Justification is presentation, not authority, and is not a cache key.
        key = (
            "local",
            argv[0] if argv else None,
            requirement.canonical_command
            if requirement.canonical_command is not None
            else tuple(argv),
            str(cwd),
            tty,
            sandbox_permissions,
            requirement.policy_fingerprint,
        )
        if key in self._session:
            return
        if self.router.handler is None:
            raise ValueError("execution approval required; no host approval handler is available")
        proposal = requirement.proposed_execpolicy_amendment
        scopes = ["once", "session"]
        if proposal is not None and self.rules.path is not None:
            scopes.append("rule")
        try:
            response = await self.router.request_shell_approval(
                {
                    "message": (
                        requirement.reason
                        if requirement.reason is not None
                        else justification
                        if justification is not None
                        else "Shell command requires approval."
                    ),
                    "mode": "form",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {
                            "scope": {
                                "type": "string",
                                "title": "Approval scope: once, session, or save the proposed rule",
                                "enum": scopes,
                                "default": "once",
                            }
                        },
                    },
                    "_meta": {
                        "codex_approval_kind": "shell",
                        "tool_params": {
                            "argv": list(argv),
                            "cwd": str(cwd),
                            "tty": tty,
                            "sandbox_permissions": sandbox_permissions,
                            "justification": justification,
                            "proposed_execpolicy_amendment": list(proposal)
                            if proposal is not None
                            else None,
                        },
                        "call_id": call_id,
                        "execpolicy_amendment": list(proposal) if proposal is not None else None,
                    },
                }
            )
        except Exception as error:
            raise ValueError(f"execution approval failed: {type(error).__name__}") from error
        action = response.get("action")
        if action != "accept":
            # A bare owner has no Turn authority. Runtime's host response API
            # separately interrupts the Turn for Cancel; internal receiver Abort
            # can still become a command error, as in native unified_exec.
            raise ValueError(
                "execution approval aborted"
                if action == "cancel"
                else "execution approval rejected"
            )
        content = response.get("content")
        if isinstance(content, dict) and content.get("execpolicy_amendment") is not None:
            amendment = content["execpolicy_amendment"]
            if not isinstance(amendment, (list, tuple)) or any(
                not isinstance(p, str) for p in amendment
            ):
                raise ValueError("execpolicy amendment must be a string array")
            if content.get("remember") is True:
                raise ValueError("rule persistence and session approval are distinct decisions")
            return tuple(amendment)
        if isinstance(content, dict) and content.get("remember") is True:
            self._session.add(key)
        return None
