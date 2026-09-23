"""Real native sandbox denial, fresh review, ordered effects and no-follow retry."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_bundled_execution import compiler as compiler
from test_execution_approval_cancel import observe
from test_filesystem_helper_runtime import workspace_policy
from test_patch_approvals import Model, create_runtime, patch

from corki.execution.backend import _compile_native_file_helper, patch_operation
from corki.execution.owned_process import run_owned
from corki.execution.patch_result import PatchResult
from corki.execution.response import decode_helper_response
from corki.protocol.events import ToolCallCompleted, TurnCancelled, TurnCompleted, TurnDiff
from corki.protocol.items import ToolResultItem


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
@pytest.mark.parametrize("approval", ["untrusted", "on-request"])
def test_denied_patch_retries_only_after_fresh_host_review(
    tmp_path, compiler, mode, action, approval
):
    async def scenario():
        root = (tmp_path / "workspace").resolve()
        root.mkdir()
        outside = tmp_path.resolve() / "outside.txt"
        model = Model(mode)
        model.patch = patch(f"*** Add File: first.txt\n+first\n*** Add File: {outside}\n+outside")
        policy = replace(
            workspace_policy(compiler, root), approval_policy_json=json.dumps(approval)
        )
        runtime = await create_runtime(root, compiler, model, policy=policy)
        prompts = []

        async def respond(request):
            meta = request.params["_meta"]
            prompts.append(meta)
            if "patch_retry" not in meta:
                assert not (root / "first.txt").exists() and not outside.exists()
                runtime.respond_execution_approval(request.request_id, "accept", remember=True)
            else:
                assert (root / "first.txt").read_text() == "first\n"
                assert not outside.exists()
                evidence = meta["patch_retry"]
                assert evidence["sandbox"] == "seatbelt"
                assert evidence["execution"]["exit_code"] == 1
                assert evidence["execution"]["sandbox_denied"] is True
                assert "Failed to write file" in evidence["execution"]["stderr"]
                assert evidence["committed_delta"]["exact"] is False
                assert [c["path"] for c in evidence["committed_delta"]["changes"]] == [
                    str(root / "first.txt")
                ]
                assert request.params["requestedSchema"]["properties"]["scope"]["enum"] == ["once"]
                runtime.respond_execution_approval(request.request_id, action)

        runtime.set_execution_approval_handler(respond)
        try:
            events = await asyncio.wait_for(observe(runtime, "apply patch"), 15)
            assert len(prompts) == 2  # Initial session consent must not cover bypass.
            assert isinstance(events[-1], TurnCancelled if action == "cancel" else TurnCompleted)
            assert outside.exists() is (action == "accept")
            assert (root / "first.txt").read_text() == "first\n"
            assert not runtime._process_manager.approvals.router._pending
            if action != "cancel":
                result = next(
                    e
                    for e in events
                    if isinstance(e, ToolCallCompleted) and e.tool_name == "apply_patch"
                )
                assert result.is_error is (action != "accept")
                delta = json.loads(result.patch_delta_json)
                assert delta["exact"] is False  # A successful retry cannot erase uncertainty.
                paths = [c["path"] for c in delta["changes"]]
                assert paths == [str(root / "first.txt")] * (2 if action == "accept" else 1) + (
                    [str(outside)] if action == "accept" else []
                )
                assert not [e for e in events if isinstance(e, TurnDiff) and e.unified_diff]
                output = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)][
                    -1
                ].content
                assert ("Retried once" if action == "accept" else "No retry") in output
                outcomes = await runtime._repository.load_turn_tool_outcomes(
                    result.thread_id, result.turn_id
                )
                assert any(o.patch_delta_json == result.patch_delta_json for o in outcomes)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "approval",
    [
        '"never"',
        '{"granular":{"sandbox_approval":false,"rules":true,"skill_approval":true,"request_permissions":true,"mcp_elicitations":true}}',
    ],
)
def test_policy_disallows_patch_bypass(tmp_path, compiler, approval):
    async def scenario():
        policy = replace(workspace_policy(compiler, tmp_path), approval_policy_json=approval)
        compiled = await _compile_native_file_helper(policy, tmp_path, patch=True)
        assert compiled.patch_retry.command is None

    asyncio.run(scenario())


@pytest.mark.parametrize("location", ["leaf", "ancestor"])
@pytest.mark.parametrize(
    "operation", ["add", "update", "delete", "move_source", "move_destination", "nested_add"]
)
def test_retry_rejects_links_swapped_after_verification(tmp_path, compiler, location, operation):
    async def scenario():
        root = tmp_path.resolve()
        approved, outside = root / "approved", root / "outside"
        approved.mkdir()
        outside.mkdir()
        (approved / "file.txt").write_text("original\n")
        (outside / "file.txt").write_text("original\n")
        (root / "source.txt").write_text("original\n")
        target = "approved/file.txt"
        bodies = {
            "add": f"*** Add File: {target}\n+changed",
            "update": f"*** Update File: {target}\n@@\n-original\n+changed",
            "delete": f"*** Delete File: {target}",
            "move_source": (
                f"*** Update File: {target}\n*** Move to: moved.txt\n@@\n-original\n+changed"
            ),
            "move_destination": (
                f"*** Update File: source.txt\n*** Move to: {target}\n@@\n-original\n+changed"
            ),
            "nested_add": "*** Add File: approved/new/nested.txt\n+changed",
        }
        policy = replace(workspace_policy(compiler, root), approval_policy_json='"on-request"')
        compiled = await _compile_native_file_helper(policy, root, patch=True)
        args = {
            "patch": patch(bodies[operation]),
            "authority": compiled.patch_authority,
            "prepare": True,
        }
        prepared = await run_owned(
            compiled.command,
            json.dumps({"operation": "patch", "arguments": args}).encode(),
            cwd=root,
            output_limit=4_000_000,
        )
        review = json.loads(decode_helper_response(prepared, expected=str, error_prefix="prepare"))
        if location == "leaf" and operation != "nested_add":
            (approved / "file.txt").unlink()
            (approved / "file.txt").symlink_to(outside / "file.txt")
        else:
            approved.rename(root / "original")
            approved.symlink_to(outside, target_is_directory=True)
        # Exercise the trusted helper continuation separately from the admission
        # test above, just as native no_follow_rechecks_paths_after_verification.
        args.update(patch=review["patch"], prepare=False, approved=True, retry=True)
        output = await run_owned(
            list(compiled.patch_retry.command),
            json.dumps({"operation": "patch", "arguments": args}).encode(),
            cwd=root,
            output_limit=4_000_000,
        )
        result = PatchResult.parse(
            decode_helper_response(output, expected=str, error_prefix="retry")
        )
        assert result.success is False and result.execution.sandbox_denied is False
        assert json.loads(result.delta_json)["changes"] == []
        assert (outside / "file.txt").read_text() == "original\n"
        assert (root / "source.txt").read_text() == "original\n"
        assert not (outside / "new").exists() and not (root / "moved.txt").exists()

    asyncio.run(scenario())


def test_unrestricted_default_still_follows_links(tmp_path, compiler):
    async def scenario():
        root = tmp_path.resolve()
        target = root / "file.txt"
        target.write_text("original\n")
        (root / "link.txt").symlink_to(target)
        policy = replace(workspace_policy(compiler, root), profile_json='{"type":"disabled"}')
        result = await patch_operation(
            policy, root, {"patch": patch("*** Update File: link.txt\n@@\n-original\n+changed")}
        )
        assert result.success and target.read_text() == "changed\n"

    asyncio.run(scenario())


def test_denied_reads_cannot_be_bypassed_even_by_trusted_retry_helper(tmp_path, compiler):
    async def scenario():
        root = tmp_path.resolve()
        policy = workspace_policy(compiler, root)
        profile = json.loads(policy.profile_json)
        profile["file_system"]["entries"].append(
            {
                "access": "deny",
                "path": {"type": "path", "path": str(root / "secret")},
            }
        )
        policy = replace(
            policy, profile_json=json.dumps(profile), approval_policy_json='"on-request"'
        )
        compiled = await _compile_native_file_helper(policy, root, patch=True)
        assert compiled.patch_retry.command is None
        output = await run_owned(
            [str(compiler), "--corki-fs-helper"],
            json.dumps(
                {
                    "operation": "patch",
                    "arguments": {
                        "patch": patch("*** Add File: should-not-exist\n+bad"),
                        "authority": compiled.patch_authority,
                        "approved": True,
                        "retry": True,
                    },
                }
            ).encode(),
            cwd=root,
            output_limit=4_000_000,
        )
        with pytest.raises(ValueError, match="eligible authority"):
            decode_helper_response(output, expected=str, error_prefix="retry")
        assert not (root / "should-not-exist").exists()

    asyncio.run(scenario())
