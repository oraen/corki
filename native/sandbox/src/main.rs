use std::collections::HashMap;
use std::io::{self, BufRead};
use std::path::PathBuf;

use codex_protocol::config_types::WindowsSandboxLevel;
use codex_protocol::models::{PermissionProfile, SandboxPermissions};
use codex_protocol::protocol::{AskForApproval, SandboxPolicy};
use codex_sandboxing::{
    SandboxCommand, SandboxManager, SandboxTransformRequest, SandboxType, SandboxablePreference,
};
use codex_utils_path_uri::PathUri;
use serde::Deserialize;
use serde_json::{Value, json};

mod approval;
mod catalog;
#[path = "../reference/codex-rs/core/src/context/environment_context.rs"]
mod environment_context;
// Compile the pinned implementation itself; Python tokenization is not equivalent.
#[path = "../reference/codex-rs/core/src/command_canonicalization.rs"]
mod command_canonicalization;
mod exec_amendments;
mod exec_policy;
mod exec_policy_loading;
mod fs_helper;
mod fs_patch;
mod metadata;
mod patch_diff;
#[allow(dead_code)]
#[path = "../reference/codex-rs/core/src/safety.rs"]
mod patch_safety;
mod permission_context;
mod requirements;
mod retry;
mod selection;
mod terminal;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    #[serde(default)]
    corki_metadata: bool,
    profile: Option<PermissionProfile>,
    legacy: Option<SandboxPolicy>,
    selection: Option<selection::Selection>,
    catalog: Option<selection::Selection>,
    cwd: PathBuf,
    policy_cwd: PathBuf,
    memory_root: Option<PathBuf>,
    command: Vec<String>,
    #[serde(default)]
    file_system_helper: bool,
    #[serde(default)]
    native_file_system_helper: bool,
    #[serde(default)]
    native_patch: bool,
    #[serde(default)]
    native_patch_approval: bool,
    #[serde(default)]
    native_patch_delta: bool,
    #[serde(default)]
    native_patch_retry: bool,
    #[serde(default)]
    requirements: Vec<requirements::Layer>,
    #[serde(default)]
    resolve_requirements: bool,
    exec_policy: Option<Vec<exec_policy::Source>>,
    load_exec_policy: Option<exec_policy_loading::Request>,
    approval_policy: Option<AskForApproval>,
    approval_policy_explicit: Option<bool>,
    #[serde(default)]
    approval_policy_constraint: approval::Constraint,
    #[serde(default)]
    sandbox_permissions: SandboxPermissions,
    #[serde(default)]
    exec_policy_options: exec_amendments::Options,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AmendmentRequest {
    append_execpolicy: exec_amendments::Append,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ContextRequest {
    permission_context: permission_context::Request,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DenialRequest {
    classify_sandbox_denial: retry::DetectionRequest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TerminalRequest {
    review_terminal: terminal::Request,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PatchDiffRequest {
    render_patch_diff: patch_diff::RenderRequest,
}

fn dispatch(line: &str) -> Result<Value, String> {
    let hint: Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
    if hint.get("render_patch_diff").is_some() {
        let request: PatchDiffRequest = serde_json::from_str(line).map_err(|e| e.to_string())?;
        Ok(patch_diff::render(request.render_patch_diff))
    } else if hint.get("append_execpolicy").is_some() {
        let request: AmendmentRequest = serde_json::from_str(line).map_err(|e| e.to_string())?;
        exec_amendments::append(request.append_execpolicy)
    } else if hint.get("permission_context").is_some() {
        let request: ContextRequest = serde_json::from_str(line).map_err(|e| e.to_string())?;
        permission_context::render(request.permission_context)
    } else if hint.get("review_terminal").is_some() {
        let request: TerminalRequest = serde_json::from_str(line).map_err(|e| e.to_string())?;
        terminal::review(request.review_terminal)
    } else if hint.get("classify_sandbox_denial").is_some() {
        let request: DenialRequest = serde_json::from_str(line).map_err(|e| e.to_string())?;
        retry::classify(request.classify_sandbox_denial)
    } else {
        // Deserialize the original bytes so duplicate known fields remain errors.
        transform(serde_json::from_str(line).map_err(|e| e.to_string())?)
    }
}

fn transform(mut req: Request) -> Result<Value, String> {
    metadata::require_corki_metadata(req.corki_metadata)?;
    if req.native_file_system_helper && !req.file_system_helper {
        return Err("native filesystem entrypoint requires file_system_helper".into());
    }
    if req.native_patch && !req.native_file_system_helper {
        return Err("native patch requires native filesystem helper".into());
    }
    if req.native_patch_approval && !req.native_patch {
        return Err("patch approval requires native patch".into());
    }
    if req.native_patch_delta && !req.native_patch {
        return Err("native_patch_delta requires native_patch".into());
    }
    if req.native_patch_retry && !req.native_patch {
        return Err("native_patch_retry requires native_patch".into());
    }
    if req.sandbox_permissions.uses_additional_permissions() {
        return Err("additional permission approvals are not enabled".into());
    }
    if req.sandbox_permissions.requests_sandbox_override()
        && (req.exec_policy.is_none()
            || req.file_system_helper
            || req.memory_root.is_some()
            || req.resolve_requirements)
    {
        return Err("sandbox override is only valid for model commands".into());
    }
    let approval_policy = if req.memory_root.is_some() {
        AskForApproval::Never
    } else {
        req.approval_policy.unwrap_or(AskForApproval::Never)
    };
    if !req.cwd.is_absolute() || !req.policy_cwd.is_absolute() || req.command.is_empty() {
        return Err("absolute cwd and nonempty command required".into());
    }
    if req.exec_policy.is_some()
        && (req.memory_root.is_some() || req.file_system_helper || req.resolve_requirements)
    {
        return Err("exec policy admission is only valid for model commands".into());
    }
    if req.load_exec_policy.is_some()
        && (req.exec_policy.is_some() || req.memory_root.is_some() || req.file_system_helper)
    {
        return Err("exec policy loading is only valid for startup resolution".into());
    }
    if req.selection.is_some() && req.catalog.is_some() {
        return Err("selection and retained catalog cannot both be supplied".into());
    }
    if req
        .catalog
        .as_ref()
        .is_some_and(|catalog| catalog.kind != "selection")
    {
        return Err("invalid retained permission catalog".into());
    }
    let composed = requirements::compose(req.requirements)?;
    let requirements_toml = composed.clone().into_toml();
    let constraints =
        codex_config::ConfigRequirements::try_from(composed).map_err(|e| e.to_string())?;
    if req.selection.is_none() {
        catalog::assemble(
            req.catalog.as_ref().and_then(|c| c.permissions.as_ref()),
            &requirements_toml,
        )?;
    }
    let mut active = None;
    let mut roots = Vec::new();
    let mut warnings = Vec::new();
    let mut profile = match (req.profile, req.legacy, req.selection) {
        (Some(profile), None, None) => profile,
        (None, Some(legacy), None) => {
            PermissionProfile::from_legacy_sandbox_policy_for_cwd(&legacy, &req.policy_cwd)
        }
        (None, None, Some(selection)) => {
            let resolved = selection::resolve(selection, &requirements_toml, &req.policy_cwd)?;
            active = Some(resolved.active);
            roots = resolved.roots;
            warnings = resolved.warnings;
            resolved.profile
        }
        _ => return Err("exactly one permission source required".into()),
    };
    let (approval_policy, approval_warnings) = approval::resolve(
        approval_policy,
        req.approval_policy_explicit.unwrap_or(true),
        req.approval_policy_constraint,
        &constraints,
        req.resolve_requirements,
        req.memory_root.is_some(),
    )?;
    warnings.extend(approval_warnings);
    if let Some(root) = req.memory_root {
        active = None;
        roots.clear();
        if !root.is_absolute() || root != req.cwd {
            return Err("memory root must be the absolute child command cwd".into());
        }
        // Mirrors memories/write/src/phase2.rs::agent::get_config. The concrete
        // file/network policy is compiled by the same upstream implementation.
        profile = match profile {
            PermissionProfile::Disabled => PermissionProfile::Disabled,
            PermissionProfile::External { network } => PermissionProfile::External { network },
            PermissionProfile::Managed { .. } => {
                let writable_root = root
                    .clone()
                    .try_into()
                    .map_err(|e: std::io::Error| e.to_string())?;
                PermissionProfile::from_legacy_sandbox_policy_for_cwd(
                    &SandboxPolicy::WorkspaceWrite {
                        writable_roots: vec![writable_root],
                        network_access: false,
                        exclude_tmpdir_env_var: true,
                        exclude_slash_tmp: true,
                    },
                    &root,
                )
            }
        };
        req.policy_cwd = root;
    }
    let (profile, constraint_warnings) = requirements::apply(
        profile,
        &constraints,
        &req.policy_cwd,
        req.resolve_requirements,
        approval_policy,
    )?;
    let permission_constrained = !constraint_warnings.is_empty();
    if permission_constrained {
        active = None;
        roots.clear();
    }
    warnings.extend(constraint_warnings);
    let exec_policy_identity = constraints.exec_policy.as_ref().map(|managed| {
        // RequirementsExecPolicy equality is order-independent; source equality
        // is separate in Sourced. Preserve both using the pinned native format,
        // never a Python approximation of raw TOML or rule ordering.
        let mut identity = managed.value.fingerprint();
        identity.insert(0, format!("{:?}", managed.source));
        identity
    });
    let loaded_sources = if let Some(request) = req.load_exec_policy {
        let loaded = exec_policy_loading::load(request, &exec_policy_identity)?;
        warnings.extend(loaded.warnings);
        Some(loaded.sources)
    } else {
        None
    };
    let mut bypass_sandbox = false;
    let mut exec_approval = None;
    let exec_policy_checked = req.exec_policy.is_some();
    if let Some(sources) = req.exec_policy {
        let checked = exec_policy::check(
            &req.command,
            &sources,
            &profile,
            constraints
                .exec_policy
                .as_ref()
                .map(|managed| managed.value.as_ref()),
            approval_policy,
            req.sandbox_permissions,
            &req.exec_policy_options,
        )?;
        bypass_sandbox = checked.bypass_sandbox;
        exec_approval = checked.approval;
        warnings.extend(checked.warnings);
    }
    // Host approval must finish before this argv executes. This is per-command:
    // the stored profile stays unchanged, including independently denied reads.
    if req.sandbox_permissions.requires_escalated_permissions()
        && !profile
            .file_system_sandbox_policy()
            .has_denied_read_restrictions()
    {
        bypass_sandbox = true;
    }
    // Runtime reads belong only to a fixed native helper invocation. They must
    // never become retained user permissions or authorize an arbitrary argv.
    let helper_profile = if req.native_file_system_helper {
        let executable = std::env::current_exe().map_err(|e| e.to_string())?;
        req.command = vec![
            executable.to_string_lossy().into_owned(),
            fs_helper::ARG.into(),
        ];
        Some(fs_helper::runtime_profile(
            &profile,
            &req.policy_cwd,
            &executable,
        )?)
    } else {
        None
    };
    let execution_profile = helper_profile.as_ref().unwrap_or(&profile);
    let manager = if req.file_system_helper {
        SandboxManager::for_file_system_helpers()
    } else {
        SandboxManager::new()
    };
    let required = !bypass_sandbox
        && manager.should_sandbox(execution_profile, SandboxablePreference::Auto, false);
    let sandbox = if bypass_sandbox {
        SandboxType::None
    } else {
        manager.select_initial(
            execution_profile,
            SandboxablePreference::Auto,
            WindowsSandboxLevel::Disabled,
            false,
        )
    };
    if required && sandbox == SandboxType::None {
        return Err("requested sandbox backend unavailable".into());
    }
    let policy_cwd = PathUri::from_host_native_path(&req.policy_cwd).map_err(|e| e.to_string())?;
    let terminal_snapshot = terminal::snapshot(
        &profile,
        req.policy_cwd.clone(),
        bypass_sandbox && manager.should_sandbox(&profile, SandboxablePreference::Auto, false),
    );
    let cwd = PathUri::from_host_native_path(&req.cwd).map_err(|e| e.to_string())?;
    let command = SandboxCommand {
        program: req.command[0].clone().into(),
        args: req.command[1..].to_vec(),
        cwd: cwd.clone(),
        env: HashMap::new(),
        managed_network: None,
        additional_permissions: None,
    };
    let transformed = manager
        .transform(SandboxTransformRequest {
            command,
            permissions: execution_profile,
            sandbox,
            enforce_managed_network: false,
            environment_id: None,
            network: None,
            sandbox_policy_cwd: &policy_cwd,
            codex_linux_sandbox_exe: None,
            use_legacy_landlock: false,
            windows_sandbox_level: WindowsSandboxLevel::Disabled,
            windows_sandbox_private_desktop: true,
        })
        .map_err(|e| e.to_string())?;
    let mut response = json!({"command": transformed.command, "exec_policy_checked": exec_policy_checked, "exec_policy_loaded": loaded_sources.is_some(), "exec_policy_sources": loaded_sources, "exec_policy_identity": exec_policy_identity, "exec_approval": exec_approval, "shell_approval": true, "exec_policy_amendments": true, "model_escalation": true, "managed_exec_policy": true, "full_disk_read_access": profile.file_system_sandbox_policy().has_full_disk_read_access(), "profile": profile, "sandbox": sandbox.as_metric_tag(), "revision": 1, "memory_derivation": true, "managed_requirements": true, "named_selection": true, "managed_catalog": true, "permission_constrained": permission_constrained, "active_permission_profile": active, "profile_workspace_roots": roots, "warnings": warnings});
    response["managed_approval"] = json!(true);
    response["full_disk_write_access"] = json!(
        profile
            .file_system_sandbox_policy()
            .has_full_disk_write_access()
    );
    response["native_file_system_helper"] = json!(1);
    response["native_patch"] = json!(true);
    response["native_patch_approval"] = json!(true);
    response["native_patch_delta"] = json!(true);
    response["native_patch_retry"] = json!(1);
    if req.native_patch {
        let authority = fs_patch::Authority::capture(
            &profile,
            approval_policy,
            &req.policy_cwd,
            &roots,
            sandbox,
        )?;
        response["patch_retry"] = authority.retry_plan(&req.command);
        response["patch_authority"] = serde_json::to_value(authority).map_err(|e| e.to_string())?;
    }
    response["terminal_review_supported"] = json!(true);
    response["terminal_snapshot"] = json!(terminal_snapshot);
    response["effective_approval_policy"] = json!(approval_policy);
    response["sandbox_retry_supported"] = json!(true);
    response["sandbox_retry"] = if exec_policy_checked {
        retry::plan(
            sandbox,
            &req.command,
            approval_policy,
            &profile,
            &constraints,
        )
    } else {
        Value::Null
    };
    Ok(response)
}

fn main() {
    if std::env::args_os()
        .nth(1)
        .is_some_and(|arg| arg == fs_helper::ARG)
    {
        fs_helper::main();
        return;
    }
    for line in io::stdin().lock().lines() {
        let result = line
            .map_err(|e| e.to_string())
            .and_then(|line| dispatch(&line));
        println!(
            "{}",
            match result {
                Ok(value) => json!({"ok": value}),
                Err(error) => json!({"error": error}),
            }
        );
    }
}
