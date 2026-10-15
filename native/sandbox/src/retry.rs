//! Local startup-denial classification and a host-only, one-attempt retry plan.

use codex_config::ConfigRequirements;
use codex_protocol::exec_output::{ExecToolCallOutput, StreamOutput};
use codex_protocol::models::PermissionProfile;
use codex_protocol::protocol::AskForApproval;
use codex_sandboxing::{SandboxType, is_likely_sandbox_denied};
use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DetectionRequest {
    sandbox: String,
    exit_code: i32,
    output: String,
}

pub fn classify(request: DetectionRequest) -> Result<Value, String> {
    let sandbox = match request.sandbox.as_str() {
        "none" => SandboxType::None,
        "seatbelt" => SandboxType::MacosSeatbelt,
        "seccomp" => SandboxType::LinuxSeccomp,
        "windows_sandbox" => SandboxType::WindowsRestrictedToken,
        _ => return Err("unknown actual sandbox backend".into()),
    };
    let output = ExecToolCallOutput {
        exit_code: request.exit_code,
        aggregated_output: StreamOutput::new(request.output),
        ..Default::default()
    };
    Ok(json!({"revision": 1, "sandbox_denial": is_likely_sandbox_denied(sandbox, &output)}))
}

pub fn plan(
    sandbox: SandboxType,
    argv: &[String],
    approval: AskForApproval,
    profile: &PermissionProfile,
    constraints: &ConfigRequirements,
) -> Value {
    // Mirrors Approvable::wants_no_sandbox_approval and the local orchestrator
    // unsandboxed_execution_allowed guard. Managed network is not implemented
    // by this bridge; unsupported managed network config fails at admission.
    let wants_approval = match approval {
        AskForApproval::UnlessTrusted => true,
        AskForApproval::Granular(config) => config.sandbox_approval,
        AskForApproval::Never | AskForApproval::OnRequest => false,
    };
    let eligible = sandbox != SandboxType::None
        && wants_approval
        && !profile
            .file_system_sandbox_policy()
            .has_denied_read_restrictions();
    let review = eligible.then(|| {
        json!({
            "canonical_command": crate::command_canonicalization::canonicalize_command_for_approval(argv),
            "policy_fingerprint": constraints.exec_policy.as_ref().map(|p| p.value.fingerprint()),
        })
    });
    json!({"sandbox": sandbox.as_metric_tag(), "command": eligible.then_some(argv), "approval": review})
}
