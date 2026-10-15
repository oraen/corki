"""Compile explicit host permissions using pinned Codex policy semantics."""

import base64
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from corki.config.approval import normalize_approval_policy
from corki.config.exec_policy import ExecPolicySnapshot, ExecPolicySource
from corki.config.instructions import ProjectInstructionsConfig
from corki.config.permissions import ActivePermissionProfile, ExecutionPermissions
from corki.context.project_instructions import ProjectInstruction, load_project_entries
from corki.execution.approvals import (
    ExecApprovalRequirement,
    ExecutionApprovals,
    SandboxPermissions,
)
from corki.execution.owned_process import run_owned
from corki.execution.patch_retry import PatchRetryPlan, parse_patch_retry
from corki.execution.response import decode_helper_response
from corki.execution.retry import SandboxAdmission, SandboxRetryPlan, parse_retry_plan
from corki.execution.terminal import TerminalSnapshot, parse_terminal_snapshot

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Compiled:
    command: list[str]
    profile: str
    supports_memory: bool
    warnings: tuple[str, ...]
    active_profile: ActivePermissionProfile | None
    profile_workspace_roots: tuple[Path, ...]
    constrained: bool
    full_disk_read_access: bool | None
    exec_policy_sources: tuple[ExecPolicySource, ...] | None
    exec_policy_identity: tuple[str, ...] | None
    approval: ExecApprovalRequirement | None
    effective_approval_policy_json: str
    retry_plan: SandboxRetryPlan | None
    terminal_snapshot: TerminalSnapshot | None
    patch_authority: dict | None = None
    patch_retry: PatchRetryPlan | None = None
    full_disk_write_access: bool | None = None


async def sandbox_command(
    permissions: ExecutionPermissions,
    argv: list[str],
    cwd: Path,
    *,
    file_system_helper: bool = False,
    approvals: ExecutionApprovals | None = None,
    call_id: str = "",
    tty: bool = False,
    sandbox_permissions: SandboxPermissions = "use_default",
    justification: str | None = None,
    prefix_rule: list[str] | None = None,
    honor_allow_prefix_rules: bool = True,
    on_warning: Callable[[str], Awaitable[None]] | None = None,
    admission: SandboxAdmission | None = None,
) -> list[str]:
    """Resolve policy against its host root, never the model-selected workdir."""
    result = await _compile(
        permissions,
        argv,
        cwd,
        file_system_helper=file_system_helper,
        check_exec_policy=not file_system_helper,
        sandbox_permissions=sandbox_permissions,
        prefix_rule=prefix_rule,
        honor_allow_prefix_rules=honor_allow_prefix_rules,
        approved_prefixes=approvals.rules.prefixes if approvals is not None else (),
    )
    for warning in result.warnings:
        _LOG.warning("%s", warning)
    if (
        admission is not None
        and admission.require_terminal_snapshot
        and result.terminal_snapshot is None
    ):
        raise ValueError("sandbox compiler does not support terminal review")
    if result.approval is not None:
        if file_system_helper or permissions.approval_policy_json == '"never"':
            raise ValueError("unexpected execution approval requirement")
        if approvals is None:
            raise ValueError("execution approval required; no host approval owner is available")
        amendment = await approvals.authorize(
            result.approval,
            argv,
            cwd,
            call_id=call_id,
            tty=tty,
            sandbox_permissions=sandbox_permissions,
            justification=justification,
        )
        if amendment is not None:
            await approvals.rules.persist(permissions, amendment, on_warning)
    if admission is not None:
        admission.plan = result.retry_plan
        admission.approved = result.approval is not None
        admission.terminal_snapshot = result.terminal_snapshot
    return result.command


async def mcp_dependency_approval(permissions: ExecutionPermissions) -> tuple[object, bool]:
    """Classify effective local MCP consent with the pinned native filesystem policy."""
    result = await _compile(
        permissions, [sys.executable], permissions.policy_cwd, resolve_requirements=True
    )
    if result.full_disk_write_access is None:
        raise ValueError("sandbox compiler cannot classify MCP installation permissions")
    policy = json.loads(result.effective_approval_policy_json)
    return policy, policy == "never" and result.full_disk_write_access


async def derive_memory_permissions(
    permissions: ExecutionPermissions,
    root: Path,
) -> ExecutionPermissions:
    """Derive the native three-branch worker profile before starting its Runtime."""
    permissions = replace(
        permissions,
        approval_policy_json='"never"',
        requested_approval_policy_json='"never"',
        approval_policy_explicit=True,
        approval_policy_constraint="memory",
    )
    result = await _compile(permissions, [sys.executable], root, memory_root=root)
    if not result.supports_memory:
        raise ValueError("sandbox compiler does not support memory permission derivation")
    return replace(
        permissions,
        policy_cwd=root,
        profile_json=result.profile,
        active_profile=None,
        profile_workspace_roots=(),
        select_from_config=False,
    )


async def resolve_execution_permissions(
    permissions: ExecutionPermissions,
    *,
    exec_policy_config_folders: tuple[Path, ...] | None = None,
) -> tuple[ExecutionPermissions, tuple[str, ...]]:
    """Apply managed configuration fallback before thread/model/extension startup."""
    snapshot = permissions.exec_policy_snapshot
    inherited = (
        snapshot
        if snapshot is not None
        and (
            snapshot.config_folders == exec_policy_config_folders
            and snapshot.declared_sources == permissions.exec_policy_sources
        )
        else None
    )
    result = await _compile(
        permissions,
        [sys.executable],
        permissions.policy_cwd,
        resolve_requirements=True,
        load_exec_policy_folders=exec_policy_config_folders,
        inherited_exec_policy=inherited,
    )
    if exec_policy_config_folders is not None:
        assert result.exec_policy_sources is not None
        snapshot = ExecPolicySnapshot(
            exec_policy_config_folders,
            permissions.exec_policy_sources,
            result.exec_policy_sources,
            result.exec_policy_identity,
        )
    active, roots = result.active_profile, result.profile_workspace_roots
    if (
        not permissions.needs_resolution
        and not permissions.select_from_config
        and not result.constrained
    ):
        active, roots = permissions.active_profile, permissions.profile_workspace_roots
    return replace(
        permissions,
        profile_json=result.profile,
        active_profile=active,
        profile_workspace_roots=roots,
        catalog_json=permissions.profile_json
        if permissions.needs_resolution
        else permissions.catalog_json,
        select_from_config=permissions.needs_resolution or permissions.select_from_config,
        exec_policy_snapshot=snapshot,
        approval_policy_json=result.effective_approval_policy_json,
        requested_approval_policy_json=(
            permissions.requested_approval_policy_json or permissions.approval_policy_json
        ),
    ), result.warnings


async def _compile(
    permissions: ExecutionPermissions,
    argv: list[str],
    cwd: Path,
    *,
    file_system_helper: bool = False,
    native_file_system_helper: bool = False,
    native_patch: bool = False,
    memory_root: Path | None = None,
    resolve_requirements: bool = False,
    check_exec_policy: bool = False,
    load_exec_policy_folders: tuple[Path, ...] | None = None,
    inherited_exec_policy: ExecPolicySnapshot | None = None,
    sandbox_permissions: SandboxPermissions = "use_default",
    prefix_rule: list[str] | None = None,
    honor_allow_prefix_rules: bool = True,
    approved_prefixes: tuple[tuple[str, ...], ...] = (),
) -> _Compiled:
    if sandbox_permissions not in {"use_default", "require_escalated"}:
        raise ValueError("unsupported sandbox_permissions")
    if sandbox_permissions != "use_default" and (
        not check_exec_policy
        or file_system_helper
        or memory_root is not None
        or resolve_requirements
    ):
        raise ValueError("sandbox override is only valid for model commands")
    advanced_rules = (
        prefix_rule is not None or not honor_allow_prefix_rules or bool(approved_prefixes)
    )
    if advanced_rules and not check_exec_policy:
        raise ValueError("command rule options are only valid for model commands")
    profile_source = (
        permissions.catalog_json
        if resolve_requirements and permissions.select_from_config
        else permissions.profile_json
    )
    assert profile_source is not None
    profile = json.loads(profile_source)
    source = (
        "legacy"
        if profile["type"]
        in {
            "read-only",
            "workspace-write",
            "danger-full-access",
            "external-sandbox",
        }
        else "profile"
    )
    if profile["type"] == "selection":
        source = "selection"
    request = {
        "cwd": str(cwd),
        "policy_cwd": str(permissions.policy_cwd),
        "command": argv,
        "file_system_helper": file_system_helper,
    }
    if native_file_system_helper:
        if not file_system_helper:
            raise ValueError("native filesystem entrypoint requires file_system_helper")
        request["native_file_system_helper"] = True
    if native_patch:
        if not native_file_system_helper:
            raise ValueError("native patch requires native filesystem helper")
        request["native_patch"] = True
        request["native_patch_approval"] = True
        request["native_patch_delta"] = True
        request["native_patch_retry"] = True
    managed_approval = any(
        "allowed_approval_policies" in json.loads(layer.value_json)
        for layer in permissions.requirements
    )
    requested_approval = (
        permissions.requested_approval_policy_json or permissions.approval_policy_json
        if resolve_requirements
        else permissions.approval_policy_json
    )
    if requested_approval != '"never"':
        request["approval_policy"] = json.loads(requested_approval)
    if managed_approval:
        request["approval_policy_explicit"] = permissions.approval_policy_explicit
        request["approval_policy_constraint"] = permissions.approval_policy_constraint
    if sandbox_permissions != "use_default":
        request["sandbox_permissions"] = sandbox_permissions
    if advanced_rules:
        request["exec_policy_options"] = {
            "prefix_rule": prefix_rule,
            "honor_allow_prefix_rules": honor_allow_prefix_rules,
            "approved_prefixes": approved_prefixes,
        }
    if check_exec_policy:
        snapshot = permissions.exec_policy_snapshot
        sources = (
            snapshot.sources
            if snapshot is not None and snapshot.declared_sources == permissions.exec_policy_sources
            else permissions.exec_policy_sources
        )
        request["exec_policy"] = [asdict(source) for source in sources]
    if load_exec_policy_folders is not None:
        request["load_exec_policy"] = {
            "config_folders": [str(folder) for folder in load_exec_policy_folders],
            "sources": [asdict(source) for source in permissions.exec_policy_sources],
            "inherited": (
                {
                    "managed_identity": inherited_exec_policy.managed_identity,
                    "sources": [asdict(source) for source in inherited_exec_policy.sources],
                }
                if inherited_exec_policy is not None
                else None
            ),
        }
    if source != "selection" and permissions.catalog_json is not None:
        request["catalog"] = json.loads(permissions.catalog_json)
    if memory_root is not None:
        request["memory_root"] = str(memory_root)
    if permissions.requirements or resolve_requirements:
        request["requirements"] = [
            {
                "source": layer.source,
                "base_dir": str(layer.base_dir) if layer.base_dir is not None else None,
                "value": json.loads(layer.value_json),
            }
            for layer in permissions.requirements
        ]
        request["resolve_requirements"] = resolve_requirements
    # Older native helpers deny unknown fields, so an explicit host override
    # cannot silently retain the pre-product-metadata semantics.
    request["corki_metadata"] = True
    output = await run_owned(
        [str(permissions.compiler)],
        # Preserve duplicate fields/numeric spelling for the native deserializer.
        # A Python dict round-trip could silently replace a policy declaration.
        (
            "{" + json.dumps(source) + ":" + profile_source + "," + json.dumps(request)[1:] + "\n"
        ).encode(),
        cwd=permissions.policy_cwd,
        output_limit=4_000_000,
    )
    try:
        value = decode_helper_response(
            output, expected=dict, error_prefix="sandbox policy rejected"
        )
        if native_file_system_helper and (
            type(value.get("native_file_system_helper")) is not int
            or value["native_file_system_helper"] != 1
        ):
            raise ValueError("sandbox compiler does not support native filesystem helper v1")
        if native_patch and value.get("native_patch") is not True:
            raise ValueError("sandbox compiler does not support native patch execution")
        if native_patch and value.get("native_patch_approval") is not True:
            raise ValueError("sandbox compiler does not support native patch approval")
        if native_patch and value.get("native_patch_delta") is not True:
            raise ValueError("sandbox compiler does not support committed patch deltas")
        if native_patch and not isinstance(value.get("patch_authority"), dict):
            raise ValueError("sandbox compiler returned no patch authority")
        effective_approval = requested_approval
        if managed_approval:
            if value.get("managed_approval") is not True:
                raise ValueError("sandbox compiler does not support managed approval")
            effective_approval = normalize_approval_policy(
                json.dumps(value["effective_approval_policy"])
            )
            if (
                not resolve_requirements or permissions.approval_policy_constraint != "configured"
            ) and effective_approval != requested_approval:
                raise ValueError(
                    "sandbox compiler changed approval outside configuration admission"
                )
        supports_amendments = value.get("exec_policy_amendments") is True
        if advanced_rules and not supports_amendments:
            raise ValueError("sandbox compiler does not support execution rule amendments")
        if sandbox_permissions != "use_default" and value.get("model_escalation") is not True:
            raise ValueError("sandbox compiler does not support model escalation")
        if effective_approval != '"never"' and (
            value.get("shell_approval") is not True or "exec_approval" not in value
        ):
            raise ValueError("sandbox compiler does not support shell approval")
        approval = None
        if (raw_approval := value.get("exec_approval")) is not None:
            approval_fields = {"reason", "policy_fingerprint"}
            if supports_amendments:
                approval_fields |= {"canonical_command", "proposed_execpolicy_amendment"}
            if (
                not isinstance(raw_approval, dict)
                or set(raw_approval) != approval_fields
                or (
                    raw_approval["reason"] is not None
                    and not isinstance(raw_approval["reason"], str)
                )
            ):
                raise KeyError("invalid execution approval requirement")
            fingerprint = raw_approval["policy_fingerprint"]
            if fingerprint is not None and (
                not isinstance(fingerprint, list)
                or any(not isinstance(part, str) for part in fingerprint)
            ):
                raise KeyError("invalid execution approval fingerprint")
            if not check_exec_policy:
                raise KeyError("unexpected execution approval outside command admission")
            canonical, proposal = None, None
            if supports_amendments:
                canonical = raw_approval["canonical_command"]
                proposal = raw_approval["proposed_execpolicy_amendment"]
                for words in (canonical, proposal):
                    if words is not None and (
                        not isinstance(words, list)
                        or not words
                        or any(not isinstance(w, str) for w in words)
                    ):
                        raise KeyError("invalid execution approval command or proposal")
                if canonical is None:
                    raise KeyError("missing canonical approval command")
            approval = ExecApprovalRequirement(
                raw_approval["reason"],
                None if fingerprint is None else tuple(fingerprint),
                None if canonical is None else tuple(canonical),
                None if proposal is None else tuple(proposal),
            )
        if check_exec_policy and value.get("exec_policy_checked") is not True:
            raise ValueError("sandbox compiler does not support exec policy admission")
        managed_rules = any(
            "rules" in json.loads(layer.value_json) for layer in permissions.requirements
        )
        if managed_rules and value.get("managed_exec_policy") is not True:
            raise ValueError("sandbox compiler does not support managed exec policy")
        identity = None
        loaded_sources = None
        if load_exec_policy_folders is not None:
            if value.get("exec_policy_loaded") is not True:
                raise ValueError("sandbox compiler does not support exec policy loading")
            rows = value.get("exec_policy_sources")
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) or set(row) != {"name", "contents"} for row in rows
            ):
                raise KeyError("invalid exec policy sources")
            loaded_sources = tuple(ExecPolicySource(**row) for row in rows)
        if load_exec_policy_folders is not None or managed_rules:
            identity = value["exec_policy_identity"]
            if identity is not None and (
                not isinstance(identity, list)
                or any(not isinstance(entry, str) for entry in identity)
            ):
                raise KeyError("invalid managed exec policy identity")
            if managed_rules and (identity is None or len(identity) < 2):
                raise KeyError("missing managed exec policy identity")
            identity = tuple(identity) if identity is not None else None
        command = value["command"]
        if type(value["revision"]) is not int or value["revision"] != 1:
            raise KeyError("invalid compiler revision")
        if not isinstance(command, list) or not command:
            raise KeyError("invalid compiler contract")
        if not all(isinstance(arg, str) and "\0" not in arg for arg in command):
            raise KeyError("invalid sandbox argv")
        profile = value["profile"]
        if not isinstance(profile, dict) or profile.get("type") not in {
            "managed",
            "disabled",
            "external",
        }:
            raise KeyError("invalid canonical permission profile")
        if (permissions.requirements or resolve_requirements) and value.get(
            "managed_requirements"
        ) is not True:
            raise ValueError("sandbox compiler does not support managed requirements")
        if (source == "selection" or permissions.active_profile is not None) and value.get(
            "named_selection"
        ) is not True:
            raise ValueError("sandbox compiler does not support named permission selection")
        if (
            source == "selection"
            or permissions.catalog_json is not None
            or permissions.requirements
        ) and value.get("managed_catalog") is not True:
            raise ValueError("sandbox compiler does not support managed permission catalogs")
        warnings = value.get("warnings", [])
        if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings):
            raise KeyError("invalid compiler warnings")
        active = value.get("active_permission_profile")
        if active is not None:
            if not isinstance(active, dict) or active.keys() - {"id", "extends"}:
                raise KeyError("invalid active permission profile")
            active = ActivePermissionProfile(active["id"], active.get("extends"))
        roots = value.get("profile_workspace_roots", [])
        if not isinstance(roots, list) or any(
            not isinstance(root, str) or not Path(root).is_absolute() for root in roots
        ):
            raise KeyError("invalid profile workspace roots")
        return _Compiled(
            command,
            json.dumps(profile),
            value.get("memory_derivation") is True,
            tuple(warnings),
            active,
            tuple(Path(root) for root in roots),
            value.get("permission_constrained") is True,
            value.get("full_disk_read_access")
            if type(value.get("full_disk_read_access")) is bool
            else None,
            loaded_sources,
            identity,
            approval,
            effective_approval,
            parse_retry_plan(
                value,
                argv,
                model_command=check_exec_policy,
                approval_policy_json=effective_approval,
            ),
            parse_terminal_snapshot(value),
            value.get("patch_authority") if native_patch else None,
            parse_patch_retry(value, permissions.compiler, effective_approval)
            if native_patch
            else None,
            value.get("full_disk_write_access")
            if type(value.get("full_disk_write_access")) is bool
            else None,
        )
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("sandbox compiler returned an invalid response") from None


async def file_operation(
    permissions: ExecutionPermissions,
    cwd: Path,
    operation: str,
    arguments: dict[str, object],
    *,
    output_limit: int = 4_000_000,
    approvals: ExecutionApprovals | None = None,
    call_id: str = "",
) -> str:
    """Compatibility string API; failures never masquerade as successful output."""
    from corki.execution.patch_result import PatchResult

    result = await _file_operation(
        permissions,
        cwd,
        operation,
        arguments,
        output_limit=output_limit,
        approvals=approvals,
        call_id=call_id,
    )
    if isinstance(result, PatchResult):
        if not result.success:
            raise ValueError(result.output)
        return result.output
    return result


async def patch_operation(
    permissions: ExecutionPermissions,
    cwd: Path,
    arguments: dict[str, object],
    *,
    output_limit: int = 4_000_000,
    approvals: ExecutionApprovals | None = None,
    call_id: str = "",
    on_started=None,
):
    """Return the committed evidence even when a native patch partially failed."""
    return await _file_operation(
        permissions,
        cwd,
        "patch",
        arguments,
        output_limit=output_limit,
        approvals=approvals,
        call_id=call_id,
        on_patch_started=on_started,
    )


async def _file_operation(
    permissions: ExecutionPermissions,
    cwd: Path,
    operation: str,
    arguments: dict[str, object],
    *,
    output_limit: int = 4_000_000,
    approvals: ExecutionApprovals | None = None,
    call_id: str = "",
    on_patch_started=None,
):
    """Execute file access under the same policy, including OS path/symlink checks."""
    if operation not in {"image", "patch"}:
        raise ValueError("unsupported filesystem operation")
    compiled = await _compile_native_file_helper(permissions, cwd, patch=operation == "patch")
    helper_env = _native_helper_env()
    if operation == "patch":
        from corki.execution.patch_review import parse_patch_review

        arguments = {
            **arguments,
            "authority": compiled.patch_authority,
            "prepare": True,
            "approved": False,
            "retry": False,
        }
        prepared = await run_owned(
            compiled.command,
            json.dumps({"operation": operation, "arguments": arguments}).encode(),
            cwd=cwd,
            output_limit=output_limit,
            env=helper_env,
        )
        review = parse_patch_review(
            decode_helper_response(
                prepared, expected=str, error_prefix="sandbox filesystem operation failed"
            )
        )
        if review["requires_approval"]:
            if approvals is None:
                raise ValueError("patch approval required; no host approval owner is available")
            await approvals.authorize_patch(review, call_id=call_id)
        arguments = {
            **arguments,
            "patch": review["patch"],
            "prepare": False,
            "approved": review["requires_approval"],
        }
    from corki.execution.patch_result import PatchResult

    payload = json.dumps({"operation": operation, "arguments": arguments}).encode()
    if operation == "patch" and on_patch_started is not None:
        on_patch_started()
    try:
        output = await run_owned(
            compiled.command,
            payload,
            cwd=cwd,
            output_limit=output_limit,
            env=helper_env,
        )
        if operation == "patch":
            result = PatchResult.parse(
                decode_helper_response(
                    output, expected=str, error_prefix="sandbox filesystem operation failed"
                )
            )
    except Exception as error:
        if operation == "patch":
            # Never infer an empty/exact delta from lost, malformed or oversized
            # post-start output. Cancellation remains control flow (BaseException).
            return PatchResult.unknown(error)
        raise
    if operation == "patch":
        # Lost transport is handled above and never reaches this branch. Only
        # the pinned predicate over original execution streams can admit retry.
        plan = compiled.patch_retry
        if (
            result.success
            or result.execution is None
            or not result.execution.sandbox_denied
            or plan is None
            or plan.command is None
        ):
            return result
        if approvals is None:
            return replace(result, output=result.output + "\nNo retry: no host approval owner.")
        try:
            await approvals.authorize_patch(
                review,
                call_id=call_id,
                retry={
                    "sandbox": plan.sandbox,
                    "execution": asdict(result.execution),
                    "committed_delta": json.loads(result.delta_json),
                    "output": result.output,
                },
            )
        except Exception as error:
            return replace(result, output=result.output + f"\nNo retry: {error}")
        try:
            retry_output = await run_owned(
                list(plan.command),
                json.dumps(
                    {
                        "operation": "patch",
                        "arguments": {
                            **arguments,
                            "approved": True,
                            "retry": True,
                        },
                    }
                ).encode(),
                cwd=cwd,
                output_limit=output_limit,
                env=helper_env,
            )
            later = PatchResult.parse(
                decode_helper_response(
                    retry_output, expected=str, error_prefix="sandbox filesystem operation failed"
                )
            )
        except Exception as error:
            later = PatchResult.unknown(error)
        return result.append_attempt(later)
    try:
        value = decode_helper_response(
            output, expected=str, error_prefix="sandbox filesystem operation failed"
        )
        if not isinstance(value, str):
            raise KeyError("invalid filesystem result")
        if operation == "image":
            # Validation handles only returned bytes; no host filesystem access.
            from corki.media.images import validate_image_bytes

            data = base64.b64decode(value, validate=True)
            if len(data) > arguments["max_bytes"]:
                raise ValueError("sandbox image result exceeds its byte limit")
            try:
                validate_image_bytes(data)
            except Exception:
                raise ValueError(
                    "unable to process image: invalid or unsupported image data"
                ) from None
        return value
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("sandbox filesystem helper returned an invalid response") from None


def _native_helper_env() -> dict[str, str]:
    """Pinned exec-server/fs_sandbox.rs release allowlist, captured per call."""
    allowed = {"PATH", "TMPDIR", "TMP", "TEMP"}
    if sys.platform == "darwin":
        allowed.add("__CF_USER_TEXT_ENCODING")
    return {
        key: value
        for key, value in os.environ.items()
        if key in allowed or os.name == "nt" and key.upper() == "PATH"
    }


async def _compile_native_file_helper(
    permissions: ExecutionPermissions, cwd: Path, *, patch: bool = False
) -> _Compiled:
    # The native side replaces argv with its own fixed entrypoint. No supplied
    # program can borrow the helper-only runtime permissions.
    return await _compile(
        permissions,
        [str(permissions.compiler), "--corki-fs-helper"],
        cwd,
        file_system_helper=True,
        native_file_system_helper=True,
        native_patch=patch,
    )


async def read_project_instructions(
    permissions: ExecutionPermissions, cwd: Path, config: ProjectInstructionsConfig
) -> tuple[tuple[ProjectInstruction, ...], tuple[str, ...]]:
    """Discover/read in the OS sandbox whenever native policy restricts reads."""
    from corki.context.user_instructions import owned_read

    # Preserve duplicate fields: a dict decoder would collapse malformed
    # profiles into one of the fast-path shapes and bypass native validation.
    profile = json.loads(permissions.profile_json, object_pairs_hook=list)
    # These exact legacy/default shapes have unrestricted reads. All other
    # shapes must be classified by the native policy, not approximated here.
    simple_full_read = not permissions.requirements and profile in (
        [("type", "read-only")],
        [("type", "workspace-write")],
        [("type", "danger-full-access")],
        [("type", "disabled")],
    )
    compiled = None
    if not simple_full_read:
        compiled = await _compile_native_file_helper(permissions, cwd)
        if compiled.full_disk_read_access is None:
            raise ValueError("sandbox compiler cannot classify AGENTS.md read permissions")
    if simple_full_read or (compiled is not None and compiled.full_disk_read_access):
        try:
            return await owned_read(load_project_entries, cwd, config), ()
        except OSError as exc:
            return (), (f"error trying to find AGENTS.md docs: {exc}",)
    assert compiled is not None
    arguments = {"cwd": str(cwd), "config": asdict(config)}
    # Account for two JSON encodings and all possible root-to-cwd source paths,
    # not just the instruction text budget; never silently truncate provenance.
    longest_name = max(map(len, ("AGENTS.override.md", *config.fallback_filenames)))
    limit = 16 * (config.max_bytes + len(cwd.parts) * (len(str(cwd)) + longest_name + 100)) + 4096
    output = await run_owned(
        compiled.command,
        json.dumps({"operation": "project_instructions", "arguments": arguments}).encode(),
        cwd=cwd,
        output_limit=limit,
        env=_native_helper_env(),
    )
    try:
        payload = decode_helper_response(
            output, expected=str, error_prefix="restricted AGENTS.md discovery failed"
        )
        rows = json.loads(payload)
        if not isinstance(rows, list):
            raise TypeError("invalid instruction entries")
        entries = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"text", "source"}:
                raise TypeError("invalid instruction entry")
            if not isinstance(row["text"], str) or not isinstance(row["source"], str):
                raise TypeError("invalid instruction entry types")
            source = Path(row["source"])
            if not source.is_absolute():
                raise ValueError("invalid AGENTS.md source path")
            entries.append(ProjectInstruction(row["text"], source))
        return tuple(entries), ()
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("sandbox returned an invalid AGENTS.md discovery result") from None
