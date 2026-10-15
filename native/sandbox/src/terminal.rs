//! Local retained-terminal policy evidence and review (never command execution).

use std::path::PathBuf;

use codex_protocol::models::PermissionProfile;
use codex_protocol::permissions::NetworkSandboxPolicy;
use codex_protocol::protocol::AskForApproval;
use codex_sandboxing::policy_transforms::effective_permission_profile;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Snapshot {
    profile: PermissionProfile,
    policy_cwd: PathBuf,
    bypassed: bool,
}

pub fn snapshot(profile: &PermissionProfile, policy_cwd: PathBuf, bypassed: bool) -> Snapshot {
    Snapshot {
        profile: effective_permission_profile(profile, None),
        policy_cwd,
        bypassed,
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    launch: Snapshot,
    current: Snapshot,
    approval_policy: AskForApproval,
}

fn filesystem_only(profile: &PermissionProfile) -> PermissionProfile {
    let mut profile = profile.clone();
    if let PermissionProfile::Managed { network, .. } = &mut profile {
        *network = NetworkSandboxPolicy::Restricted;
    }
    profile
}

pub fn review(request: Request) -> Result<Value, String> {
    let Request {
        launch,
        current,
        approval_policy,
    } = request;
    if !launch.policy_cwd.is_absolute() || !current.policy_cwd.is_absolute() || current.bypassed {
        return Err("invalid retained terminal policy evidence".into());
    }
    let same_cwd = launch.policy_cwd == current.policy_cwd;
    if current
        .profile
        .file_system_sandbox_policy()
        .has_denied_read_restrictions()
        && (launch.bypassed
            || !same_cwd
            || filesystem_only(&launch.profile) != filesystem_only(&current.profile))
    {
        return Err("this terminal cannot enforce the current denied-read restrictions; start a new terminal".into());
    }
    let required = launch.bypassed || !same_cwd || launch.profile != current.profile;
    if !required {
        return Ok(json!({"revision": 1, "review": false, "reason": null}));
    }
    match approval_policy {
        AskForApproval::Never => {
            return Err("terminal input requires approval but approvals are disabled".into());
        }
        AskForApproval::Granular(policy) if !policy.sandbox_approval => {
            return Err("terminal input requires sandbox approval disabled by policy".into());
        }
        AskForApproval::UnlessTrusted | AskForApproval::OnRequest | AskForApproval::Granular(_) => {
        }
    }
    let authority = if launch.bypassed {
        "This terminal was launched outside the sandbox, bypassing any managed network proxy."
    } else if launch.profile == PermissionProfile::Disabled {
        "This terminal runs without a filesystem sandbox."
    } else {
        "This terminal retains sandbox or network settings that differ from the current permissions."
    };
    Ok(
        json!({"revision": 1, "review": true, "reason": format!("Send input to an existing terminal. {authority} The cwd is its launch directory; the terminal's current directory and state may have changed.")}),
    )
}
