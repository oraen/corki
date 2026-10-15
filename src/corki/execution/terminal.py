"""Host-only retained local terminal permissions and once-only stdin approval."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from corki.execution.owned_process import run_owned
from corki.execution.response import decode_helper_response


@dataclass(frozen=True, slots=True)
class TerminalSnapshot:
    profile_json: str
    policy_cwd: Path
    bypassed: bool

    def wire(self):
        return {
            "profile": json.loads(self.profile_json),
            "policy_cwd": str(self.policy_cwd),
            "bypassed": self.bypassed,
        }


def parse_terminal_snapshot(value):
    if "terminal_review_supported" not in value:
        if "terminal_snapshot" in value:
            raise ValueError("terminal snapshot has no capability acknowledgement")
        return None
    raw = value.get("terminal_snapshot")
    if (
        value["terminal_review_supported"] is not True
        or not isinstance(raw, dict)
        or set(raw) != {"profile", "policy_cwd", "bypassed"}
    ):
        raise ValueError("invalid terminal policy snapshot")
    if (
        not isinstance(raw["profile"], dict)
        or type(raw["bypassed"]) is not bool
        or not isinstance(raw["policy_cwd"], str)
        or not Path(raw["policy_cwd"]).is_absolute()
    ):
        raise ValueError("invalid terminal policy evidence")
    return TerminalSnapshot(json.dumps(raw["profile"]), Path(raw["policy_cwd"]), raw["bypassed"])


def unrestricted_terminal(cwd):
    return TerminalSnapshot('{"type":"disabled"}', cwd, False)


async def review_terminal(manager, session, chars, permissions, cwd, call_id):
    # Import at the call boundary: backend consumes this module's snapshot parser.
    from corki.execution.backend import _compile

    launch = session.permissions
    if launch is None:
        raise ValueError(
            "terminal launch permissions are unknown; "
            "start a new terminal with an upgraded compiler"
        )
    current = unrestricted_terminal(cwd)
    policy = '"never"'
    compiler = session.permission_compiler
    if permissions is not None:
        compiled = await _compile(permissions, ["true"], permissions.policy_cwd)
        current, policy = compiled.terminal_snapshot, compiled.effective_approval_policy_json
        compiler = permissions.compiler
        if current is None:
            raise ValueError("sandbox compiler does not support terminal review")
    if compiler is None:
        # Only the unchanged legacy Disabled environment has no native backend.
        if launch != current:
            raise ValueError(
                "terminal policy changed without a review backend; start a new terminal"
            )
        return
    response = await run_owned(
        [str(compiler)],
        (
            json.dumps(
                {
                    "review_terminal": {
                        "launch": launch.wire(),
                        "current": current.wire(),
                        "approval_policy": json.loads(policy),
                    }
                }
            )
            + "\n"
        ).encode(),
        cwd=current.policy_cwd,
        output_limit=16_384,
    )
    try:
        verdict = decode_helper_response(
            response, expected=dict, error_prefix="sandbox compiler failed terminal review"
        )
        if (
            not isinstance(verdict, dict)
            or set(verdict) != {"revision", "review", "reason"}
            or type(verdict["revision"]) is not int
            or verdict["revision"] != 1
            or type(verdict["review"]) is not bool
        ):
            raise ValueError("invalid terminal review acknowledgement")
        reason = verdict["reason"]
        if not verdict["review"]:
            if reason is not None:
                raise ValueError("invalid terminal review reason")
            return
        if not isinstance(reason, str) or not reason:
            raise ValueError("invalid terminal approval reason")
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("sandbox compiler failed terminal review") from exc
    if "\0" in chars:
        raise ValueError("terminal input contains a NUL byte and cannot be reviewed safely")
    info = session.terminal_info
    assert info is not None
    action = {
        "tool": "write_stdin",
        "environment_id": "local",
        "session_id": session.id,
        "chars": chars,
        "cwd": str(info.cwd),
        "sandbox_permissions": "require_escalated",
        "tty": session.tty,
    }
    if (
        len((json.dumps(action, ensure_ascii=False, sort_keys=True, indent=2) + reason).encode())
        > 8000
    ):
        raise ValueError(
            "terminal input and permission details are too large to review safely; "
            "use a smaller input or start a new terminal with fewer grants"
        )
    response = await manager.approvals.router.request_shell_approval(
        {
            "message": reason,
            "mode": "form",
            "requestedSchema": {
                "type": "object",
                "properties": {"scope": {"type": "string", "enum": ["once"], "default": "once"}},
            },
            "_meta": {
                "codex_approval_kind": "write_stdin",
                "tool_params": action,
                "call_id": call_id,
                "parent_call_id": info.item_id,
                "execpolicy_amendment": None,
            },
        }
    )
    if response.get("action") != "accept":
        raise ValueError("write_stdin approval rejected or aborted")
    content = response.get("content")
    if content is None:
        content = {}
    if (
        not isinstance(content, dict)
        or content.get("remember") is True
        or content.get("execpolicy_amendment") is not None
        or content.get("scope", "once") != "once"
    ):
        raise ValueError("terminal input approval must be once-only")


async def owned_terminal_review(manager, session, chars, permissions, cwd, call_id):
    task = asyncio.create_task(
        review_terminal(manager, session, chars, permissions, cwd, call_id),
        name="corki-stdin-review",
    )
    manager._stdin_reviews.add(task)
    try:
        return await asyncio.shield(task)
    finally:
        if not task.done():
            task.cancel()
        joined = asyncio.gather(task, return_exceptions=True)
        cancelled = False
        try:
            while not joined.done():
                try:
                    await asyncio.shield(joined)
                except asyncio.CancelledError:
                    cancelled = True
        finally:
            manager._stdin_reviews.discard(task)
        if cancelled:
            raise asyncio.CancelledError


async def close_terminal_reviews(manager):
    tasks = tuple(manager._stdin_reviews)
    for task in tasks:
        if not task.done() and not task.cancelling():
            task.cancel()
    joined = asyncio.gather(*tasks, return_exceptions=True)
    cancelled = False
    while not joined.done():
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError:
            cancelled = True
    manager._stdin_reviews.difference_update(tasks)
    return cancelled
