//! Pinned core parse/verify/apply, executed only in the fixed OS-sandboxed helper.

use codex_apply_patch::{
    AppliedPatchDelta, AppliedPatchFileChange, ApplyPatchFileChange, ApplyPatchOptions,
    MaybeApplyPatchVerified,
};
use codex_exec_server::LOCAL_FS;
use codex_protocol::config_types::WindowsSandboxLevel;
use codex_protocol::exec_output::{ExecToolCallOutput, StreamOutput};
use codex_protocol::models::PermissionProfile;
use codex_protocol::permissions::FileSystemSandboxPolicyContext;
use codex_protocol::protocol::AskForApproval;
use codex_sandboxing::{SandboxType, is_likely_sandbox_denied};
use codex_utils_absolute_path::AbsolutePathBuf;
use codex_utils_path_uri::PathUri;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::path::{Path, PathBuf};

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Authority {
    profile: PermissionProfile,
    approval_policy: AskForApproval,
    cwd: PathUri,
    workspace_roots: Vec<PathUri>,
    user_home_dir: Option<PathUri>,
    temporary_directories: Vec<PathUri>,
    sandbox: String,
}

impl Authority {
    // Capture in the unsandboxed host compiler, before filtering helper env.
    // Local home/tmp resolution follows protocol/permissions.rs local context.
    pub fn capture(
        profile: &PermissionProfile,
        approval_policy: AskForApproval,
        cwd: &Path,
        roots: &[AbsolutePathBuf],
        sandbox: SandboxType,
    ) -> Result<Self, String> {
        let cwd = PathUri::from_host_native_path(cwd).map_err(|e| e.to_string())?;
        let temporary_directories = std::env::var_os("TMPDIR")
            .filter(|value| !value.is_empty())
            .and_then(|value| AbsolutePathBuf::from_absolute_path(PathBuf::from(value)).ok())
            .map(PathUri::from)
            .into_iter()
            .collect();
        Ok(Self {
            profile: profile.clone(),
            approval_policy,
            workspace_roots: if roots.is_empty() {
                vec![cwd.clone()]
            } else {
                roots.iter().map(PathUri::from_abs_path).collect()
            },
            cwd,
            user_home_dir: PathUri::from_host_native_path("~").ok(),
            temporary_directories,
            sandbox: sandbox.as_metric_tag().into(),
        })
    }

    fn retry_allowed(&self) -> bool {
        self.sandbox != "none"
            && match self.approval_policy {
                AskForApproval::Never => false,
                AskForApproval::OnRequest | AskForApproval::UnlessTrusted => true,
                AskForApproval::Granular(config) => config.allows_sandbox_approval(),
            }
            && !self
                .profile
                .file_system_sandbox_policy()
                .has_denied_read_restrictions()
    }

    pub fn retry_plan(&self, command: &[String]) -> serde_json::Value {
        json!({"sandbox": self.sandbox, "command": self.retry_allowed().then_some(command)})
    }

    fn sandbox_type(&self) -> Result<SandboxType, String> {
        match self.sandbox.as_str() {
            "none" => Ok(SandboxType::None),
            "seatbelt" => Ok(SandboxType::MacosSeatbelt),
            "seccomp" => Ok(SandboxType::LinuxSeccomp),
            "windows_sandbox" => Ok(SandboxType::WindowsRestrictedToken),
            _ => Err("invalid patch sandbox backend".into()),
        }
    }
}

pub fn apply(
    patch: &str,
    authority: &Authority,
    prepare: bool,
    approved: bool,
    retry: bool,
) -> Result<String, String> {
    let sandbox = authority.sandbox_type()?;
    if retry && (prepare || !approved || !authority.retry_allowed()) {
        return Err("patch retry requires eligible authority and fresh host approval".into());
    }
    let cwd = std::env::current_dir().map_err(|e| e.to_string())?;
    let cwd = PathUri::from_host_native_path(&cwd).map_err(|e| e.to_string())?;
    let args = codex_apply_patch::parse_patch(patch)
        .map_err(|e| format!("apply_patch verification failed: {e}"))?;
    if args.environment_id.is_some() {
        return Err("apply_patch environment selection is unavailable for this turn".into());
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| e.to_string())?;
    runtime.block_on(async {
        if retry {
            // The host retains the verified, normalized patch and original cwd.
            // Do not verify again here: verification follows links, while the
            // otherwise-required sandbox is now bypassed. Every filesystem call
            // in apply is guarded by the pinned no-follow implementation.
            return execute(patch, &cwd, ApplyPatchOptions {
                follow_symlinks: false,
                ..Default::default()
            }, SandboxType::None).await;
        }
        let action = match codex_apply_patch::verify_apply_patch_args(
            args,
            &cwd,
            LOCAL_FS.as_ref(),
            None,
        )
        .await
        {
            MaybeApplyPatchVerified::Body(action) => action,
            MaybeApplyPatchVerified::CorrectnessError(error) => {
                return Err(format!("apply_patch verification failed: {error}"));
            }
            _ => return Err("apply_patch handler received invalid patch input".into()),
        };
        let context = FileSystemSandboxPolicyContext {
            cwd: &authority.cwd,
            workspace_roots: &authority.workspace_roots,
            user_home_dir: authority.user_home_dir.as_ref(),
            temporary_directories: Some(&authority.temporary_directories),
        };
        let requires_approval = match crate::patch_safety::assess_patch_safety(
            &action,
            authority.approval_policy,
            &authority.profile,
            &authority.profile.file_system_sandbox_policy(),
            &context,
            crate::patch_safety::PatchSandboxRoute::Platform(WindowsSandboxLevel::Disabled),
        ) {
            crate::patch_safety::SafetyCheck::AutoApprove => false,
            crate::patch_safety::SafetyCheck::Reject { reason } => return Err(format!("patch rejected: {reason}")),
            crate::patch_safety::SafetyCheck::AskUser => true,
        };
        if prepare {
            let mut files = Vec::new();
            let mut changes = Vec::new();
            for (path, change) in action.changes() {
                files.push(path.inferred_native_path_string());
                let detail = match change {
                    ApplyPatchFileChange::Add { content } => json!({"kind": "add", "content": content}),
                    ApplyPatchFileChange::Delete { content } => json!({"kind": "delete", "content": content}),
                    ApplyPatchFileChange::Update { unified_diff, move_path, .. } => {
                        if let Some(destination) = move_path {
                            files.push(destination.inferred_native_path_string());
                        }
                        json!({"kind": "update", "diff": unified_diff, "move_path": move_path.as_ref().map(|path| path.inferred_native_path_string())})
                    }
                };
                changes.push(json!({"path": path.inferred_native_path_string(), "change": detail}));
            }
            files.sort();
            files.dedup();
            changes.sort_by_key(|value| value["path"].as_str().unwrap_or_default().to_owned());
            return Ok(json!({"version": 1, "requires_approval": requires_approval, "patch": action.patch, "cwd": action.cwd.inferred_native_path_string(), "files": files, "changes": changes}).to_string());
        }
        if requires_approval && !approved {
            return Err("patch requires host approval".into());
        }
        execute(
            &action.patch,
            &action.cwd,
            ApplyPatchOptions {
                update_file_mode: action.update_file_mode(),
                follow_symlinks: true,
            },
            sandbox,
        ).await
    })
}

async fn execute(
    patch: &str,
    cwd: &PathUri,
    options: ApplyPatchOptions,
    sandbox: SandboxType,
) -> Result<String, String> {
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let result = codex_apply_patch::apply_patch_with_options(
        patch,
        options,
        cwd,
        &mut stdout,
        &mut stderr,
        LOCAL_FS.as_ref(),
        None,
    )
    .await;
    let stdout = String::from_utf8_lossy(&stdout).into_owned();
    let stderr = String::from_utf8_lossy(&stderr).into_owned();
    let execution = ExecToolCallOutput {
        exit_code: if result.is_ok() { 0 } else { 1 },
        stdout: StreamOutput::new(stdout.clone()),
        stderr: StreamOutput::new(stderr.clone()),
        aggregated_output: StreamOutput::new(format!("{stdout}{stderr}")),
        ..Default::default()
    };
    let denied = result.is_err() && is_likely_sandbox_denied(sandbox, &execution);
    match result {
        Ok(delta) => Ok(outcome(true, stdout.clone(), &delta, &execution, denied)),
        Err(failure) => {
            let (error, delta) = failure.into_parts();
            let paths = delta
                .changes()
                .iter()
                .take(8)
                .map(|change| change.path.inferred_native_path_string())
                .collect::<Vec<_>>();
            // Put mutation uncertainty first so a bounded error result never
            // implies that failure rolled back the patch. Do not replay it.
            let output = format!(
                "apply_patch failed; committed changes: {}; delta exact: {}; no rollback performed. Changed paths (up to 8): {paths:?}. {error}",
                delta.changes().len(),
                delta.is_exact(),
            );
            Ok(outcome(false, output, &delta, &execution, denied))
        }
    }
}

fn outcome(
    success: bool,
    output: String,
    delta: &AppliedPatchDelta,
    execution: &ExecToolCallOutput,
    denied: bool,
) -> String {
    // Preserve execution order. A sorted planned-change map is not a committed delta.
    let changes = delta.changes().iter().map(|entry| {
        let change = match &entry.change {
            AppliedPatchFileChange::Add { content, overwritten_content } =>
                json!({"kind": "add", "content": content, "overwritten_content": overwritten_content}),
            AppliedPatchFileChange::Delete { content } =>
                json!({"kind": "delete", "content": content}),
            AppliedPatchFileChange::Update { move_path, old_content, overwritten_move_content, new_content } =>
                json!({"kind": "update", "move_path": move_path.as_ref().map(|path| path.inferred_native_path_string()), "old_content": old_content, "overwritten_move_content": overwritten_move_content, "new_content": new_content}),
        };
        json!({"path": entry.path.inferred_native_path_string(), "change": change})
    }).collect::<Vec<_>>();
    json!({"version": 2, "success": success, "output": output,
        "execution": {"exit_code": execution.exit_code, "stdout": execution.stdout.text, "stderr": execution.stderr.text, "sandbox_denied": denied},
        "delta": {"version": 1, "exact": delta.is_exact(), "changes": changes}})
    .to_string()
}
