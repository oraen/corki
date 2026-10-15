//! Permission prompt generation uses the pinned renderer and effective rule policy.

use crate::{exec_amendments::Options, exec_policy, requirements};
use codex_context_fragments::ContextualUserFragment;
use codex_execpolicy::Policy;
use codex_prompts::{ApprovalPromptContext, PermissionsInstructions};
use codex_protocol::config_types::ApprovalsReviewer;
use codex_protocol::models::PermissionProfile;
use codex_protocol::openai_models::{ApprovalMessages, PermissionMessages};
use codex_protocol::protocol::AskForApproval;
use codex_utils_path_uri::PathUri;
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::BTreeSet;
use std::path::PathBuf;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    #[serde(default)]
    corki_metadata: bool,
    profile: PermissionProfile,
    cwd: PathBuf,
    policy_cwd: PathBuf,
    #[serde(default)]
    workspace_roots: Vec<PathBuf>,
    approval_policy: AskForApproval,
    sources: Vec<exec_policy::Source>,
    requirements: Vec<requirements::Layer>,
    options: Options,
    approval_messages: Option<ApprovalMessages>,
    permission_messages: Option<PermissionMessages>,
}

pub fn render(req: Request) -> Result<Value, String> {
    crate::metadata::require_corki_metadata(req.corki_metadata)?;
    if !req.cwd.is_absolute() || !req.policy_cwd.is_absolute() {
        return Err("permission context requires absolute cwd and policy cwd".into());
    }
    let constraints =
        codex_config::ConfigRequirements::try_from(requirements::compose(req.requirements)?)
            .map_err(|e| e.to_string())?;
    let (profile, mut warnings) = requirements::apply(
        req.profile,
        &constraints,
        &req.policy_cwd,
        false,
        req.approval_policy,
    )?;
    let (policy, policy_warnings) = exec_policy::effective_policy(
        &req.sources,
        constraints.exec_policy.as_ref().map(|m| m.value.as_ref()),
        &req.options,
    )?;
    warnings.extend(policy_warnings);
    let roots = req
        .workspace_roots
        .iter()
        .map(|root| {
            if !root.is_absolute() {
                return Err("permission workspace root must be absolute".to_string());
            }
            PathUri::from_host_native_path(root).map_err(|e| e.to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let filesystem =
        crate::environment_context::FileSystemContext::from_permission_profile(&profile, &roots)
            .render();
    let network = constraints.network.as_ref().map(|network| {
        crate::environment_context::NetworkContext::new(
            network
                .domains
                .as_ref()
                .and_then(codex_config::NetworkDomainPermissionsToml::allowed_domains)
                .unwrap_or_default(),
            network
                .domains
                .as_ref()
                .and_then(codex_config::NetworkDomainPermissionsToml::denied_domains)
                .unwrap_or_default(),
        )
        .render()
    });
    let instructions = |policy| {
        PermissionsInstructions::from_permission_profile(
            &profile,
            req.approval_policy,
            ApprovalPromptContext::new(
                ApprovalsReviewer::User,
                req.approval_messages.as_ref(),
                req.permission_messages.as_ref(),
            ),
            policy,
            &req.cwd,
            false,
            false,
        )
        .render()
    };
    let prefixes: BTreeSet<Vec<String>> = policy.get_allowed_prefixes().into_iter().collect();
    Ok(json!({
        "permission_context": true,
        "model_permission_messages": true,
        "text": instructions(&policy),
        "without_prefixes": instructions(&Policy::empty()),
        "prefixes": prefixes,
        "warnings": warnings,
        "environment": {"version": 1, "filesystem": filesystem, "network": network},
    }))
}
