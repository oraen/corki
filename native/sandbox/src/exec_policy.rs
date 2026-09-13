//! Native command parsing and rule evaluation for host-owned approval policies.
//!
//! This is an execution-only gate. Trusted filesystem helpers and permission
//! derivation do not carry a model command and must not enter this evaluator.

use crate::exec_amendments::{self, Options};
use codex_execpolicy::{Decision, Evaluation, MatchOptions, Policy, PolicyParser, RuleMatch};
use codex_protocol::models::{PermissionProfile, SandboxPermissions};
use codex_protocol::permissions::FileSystemSandboxKind;
use codex_protocol::protocol::AskForApproval;
use codex_shell_command::bash::parse_shell_lc_plain_commands;
use codex_shell_command::is_dangerous_command::{
    DangerousCommandMatch, DangerousCommandPlatform, dangerous_command_match_for_platform,
    dangerous_powershell_words_match,
};
use serde::{Deserialize, Serialize};

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub name: String,
    pub contents: String,
}

pub struct Checked {
    pub bypass_sandbox: bool,
    pub warnings: Vec<String>,
    pub approval: Option<Approval>,
}

#[derive(Serialize)]
pub struct Approval {
    pub reason: Option<String>,
    pub policy_fingerprint: Option<Vec<String>>,
    pub canonical_command: Vec<String>,
    pub proposed_execpolicy_amendment: Option<Vec<String>>,
}

/// Shared model-visible and execution policy; preserve parse fallback and overlay order.
pub fn effective_policy(
    sources: &[Source],
    managed: Option<&Policy>,
    exec_options: &Options,
) -> Result<(Policy, Vec<String>), String> {
    let mut parser = PolicyParser::new();
    let mut warnings = Vec::new();
    for source in sources {
        if let Err(error) = parser.parse(&source.name, &source.contents) {
            // Mirrors load_exec_policy_with_warning for a host without managed
            // exec-policy rules: never retain a partially parsed allow policy.
            warnings.push(format!(
                "failed to parse exec policy {}: {error}",
                source.name
            ));
            break;
        }
    }
    let mut policy = if warnings.is_empty() {
        parser.build()
    } else {
        Policy::empty()
    };
    // Approved live amendments survive a prior user-file parse fallback. Apply
    // model filtering only afterward, before the independent managed overlay.
    for prefix in &exec_options.approved_prefixes {
        policy
            .add_prefix_rule(prefix, Decision::Allow)
            .map_err(|e| e.to_string())?;
    }
    let policy = exec_amendments::filter_allow_rules(policy, exec_options.honor_allow_prefix_rules);
    // Requirements remain independent of user parse fallback and cannot be
    // weakened by an explicit user allow. Native evaluation picks the strictest
    // matching decision across the combined policy.
    let policy = managed.map_or_else(|| policy.clone(), |overlay| policy.merge_overlay(overlay));
    Ok((policy, warnings))
}

pub fn check(
    argv: &[String],
    sources: &[Source],
    profile: &PermissionProfile,
    managed: Option<&Policy>,
    approval_policy: AskForApproval,
    sandbox_permissions: SandboxPermissions,
    exec_options: &Options,
) -> Result<Checked, String> {
    // Native handlers reject a disallowed override before explicit rule matching.
    // Sticky preapproved grants are a separate, not-yet-implemented path.
    let rejects_override = match approval_policy {
        AskForApproval::Never => true,
        AskForApproval::Granular(config) => !config.allows_sandbox_approval(),
        AskForApproval::OnRequest | AskForApproval::UnlessTrusted => false,
    };
    if sandbox_permissions.requests_sandbox_override() && rejects_override {
        return Err(format!(
            "approval policy is {approval_policy:?}; reject command — you cannot ask for escalated permissions if the approval policy is {approval_policy:?}"
        ));
    }
    let (policy, warnings) = effective_policy(sources, managed, exec_options)?;
    let platform = DangerousCommandPlatform::host();
    let (commands, powershell_origin) = match parse_shell_lc_plain_commands(argv) {
        Some(commands) if !commands.is_empty() => (commands, false),
        _ => {
            let lowered = (platform == DangerousCommandPlatform::Windows)
                .then(|| {
                    codex_shell_command::powershell::parse_powershell_command_into_plain_commands(
                        argv,
                    )
                })
                .flatten()
                .filter(|commands| !commands.is_empty());
            match lowered {
                Some(commands) => (commands, true),
                None => (vec![argv.to_vec()], false),
            }
        }
    };
    let fallback = |command: &[String]| {
        let dangerous = if powershell_origin {
            dangerous_powershell_words_match(command, platform)
        } else {
            dangerous_command_match_for_platform(command, platform)
        };
        let unenforced_windows_restriction = cfg!(windows)
            && matches!(profile, PermissionProfile::Managed { .. })
            && profile.file_system_sandbox_policy().kind == FileSystemSandboxKind::Restricted
            && !profile
                .file_system_sandbox_policy()
                .has_full_disk_write_access();
        if dangerous.is_some() || unenforced_windows_restriction {
            if approval_policy == AskForApproval::Never {
                Decision::Forbidden
            } else {
                Decision::Prompt
            }
        } else if approval_policy == AskForApproval::UnlessTrusted {
            Decision::Prompt
        } else if sandbox_permissions.requests_sandbox_override()
            && profile.file_system_sandbox_policy().kind == FileSystemSandboxKind::Restricted
        {
            Decision::Prompt
        } else {
            Decision::Allow
        }
    };
    let options = MatchOptions {
        resolve_host_executables: true,
    };
    let evaluation = policy.check_multiple_with_options(commands.iter(), &fallback, &options);
    match evaluation.decision {
        Decision::Forbidden => Err(forbidden_reason(
            argv,
            &evaluation,
            powershell_origin,
            platform,
        )),
        Decision::Prompt => {
            let matched = evaluation
                .matched_rules
                .iter()
                .filter_map(|rule| match rule {
                    RuleMatch::PrefixRuleMatch {
                        matched_prefix,
                        decision: Decision::Prompt,
                        justification,
                        ..
                    } => Some((matched_prefix.len(), justification.as_deref())),
                    _ => None,
                })
                .max_by_key(|(length, _)| *length);
            match approval_policy {
                AskForApproval::Never => {
                    return Err(
                        "approval required by policy, but AskForApproval is set to Never".into(),
                    );
                }
                AskForApproval::Granular(config)
                    if matched.is_some() && !config.allows_rules_approval() =>
                {
                    return Err(
                        "approval required by policy rule, but AskForApproval::Granular.rules is false".into(),
                    );
                }
                AskForApproval::Granular(config)
                    if matched.is_none() && !config.allows_sandbox_approval() =>
                {
                    let dangerous = evaluation.matched_rules.iter().find_map(|rule| match rule {
                        RuleMatch::HeuristicsRuleMatch {
                            command,
                            decision: Decision::Prompt,
                        } => {
                            if powershell_origin {
                                dangerous_powershell_words_match(command, platform)
                            } else {
                                dangerous_command_match_for_platform(command, platform)
                            }
                        }
                        _ => None,
                    });
                    if matches!(dangerous, Some(DangerousCommandMatch::ForcedRm)) {
                        return Err(
                            "rm -f style commands are not permitted. Use a safer approach".into(),
                        );
                    }
                    return Err("approval required by policy, but AskForApproval::Granular.sandbox_approval is false".into());
                }
                _ => {}
            }
            let command =
                shlex::try_join(argv.iter().map(String::as_str)).unwrap_or_else(|_| argv.join(" "));
            let reason = matched.map(|(_, justification)| match justification {
                Some(text) => format!("`{command}` requires approval: {text}"),
                None => format!("`{command}` requires approval by policy"),
            });
            Ok(Checked {
                bypass_sandbox: false,
                warnings,
                approval: Some(Approval {
                    reason,
                    canonical_command:
                        crate::command_canonicalization::canonicalize_command_for_approval(argv),
                    proposed_execpolicy_amendment: exec_amendments::proposed(
                        exec_options,
                        &policy,
                        &commands,
                        &evaluation,
                        &fallback,
                        &options,
                    ),
                    policy_fingerprint: managed.map(|policy| {
                        codex_execpolicy::RequirementsExecPolicy::new(policy.clone()).fingerprint()
                    }),
                }),
            })
        }
        Decision::Allow => Ok(Checked {
            // Native Skip permits bypass only when every lowered segment is
            // explicitly allowed. A heuristic Allow is never such authority.
            bypass_sandbox: commands.iter().all(|command| {
                policy
                    .matches_for_command_with_options(command, None, &options)
                    .iter()
                    .any(|rule| {
                        matches!(rule, RuleMatch::PrefixRuleMatch { .. })
                            && rule.decision() == Decision::Allow
                    })
            }) && !profile
                .file_system_sandbox_policy()
                .has_denied_read_restrictions(),
            warnings,
            approval: None,
        }),
    }
}

fn forbidden_reason(
    argv: &[String],
    evaluation: &Evaluation,
    powershell_origin: bool,
    platform: DangerousCommandPlatform,
) -> String {
    let render = |args: &[String]| {
        shlex::try_join(args.iter().map(String::as_str)).unwrap_or_else(|_| args.join(" "))
    };
    let command = render(argv);
    let matched = evaluation
        .matched_rules
        .iter()
        .filter_map(|rule| match rule {
            RuleMatch::PrefixRuleMatch {
                matched_prefix,
                decision: Decision::Forbidden,
                justification,
                ..
            } => Some((matched_prefix, justification.as_deref())),
            _ => None,
        })
        .max_by_key(|(prefix, _)| prefix.len());
    match matched {
        Some((_, Some(justification))) => format!("`{command}` rejected: {justification}"),
        Some((prefix, None)) => format!(
            "`{command}` rejected: policy forbids commands starting with `{}`",
            render(prefix)
        ),
        None => {
            let dangerous = evaluation.matched_rules.iter().find_map(|rule| match rule {
                RuleMatch::HeuristicsRuleMatch {
                    command,
                    decision: Decision::Forbidden,
                } => {
                    if powershell_origin {
                        dangerous_powershell_words_match(command, platform)
                    } else {
                        dangerous_command_match_for_platform(command, platform)
                    }
                }
                _ => None,
            });
            let reason = match dangerous {
                Some(DangerousCommandMatch::ForcedRm) => {
                    "rm -f style commands are not permitted. Use a safer approach"
                }
                Some(DangerousCommandMatch::Other) | None => "blocked by policy",
            };
            format!("`{command}` rejected: {reason}")
        }
    }
}
