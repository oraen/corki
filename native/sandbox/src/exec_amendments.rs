//! Pinned rule suggestion and append semantics; no model command execution.
use codex_execpolicy::{Decision, Evaluation, MatchOptions, Policy, PrefixRule, RuleMatch};
use serde::Deserialize;
use serde_json::{Value, json};
use std::path::PathBuf;
use std::sync::Arc;

#[derive(Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Options {
    pub honor_allow_prefix_rules: bool,
    pub prefix_rule: Option<Vec<String>>,
    pub approved_prefixes: Vec<Vec<String>>,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            honor_allow_prefix_rules: true,
            prefix_rule: None,
            approved_prefixes: Vec::new(),
        }
    }
}

pub fn filter_allow_rules(policy: Policy, honor: bool) -> Policy {
    if honor {
        return policy;
    }
    let rules = policy
        .rules()
        .iter_all()
        .flat_map(|(program, rules)| {
            rules.iter().filter_map(move |rule| {
                let is_allow = rule
                    .as_any()
                    .downcast_ref::<PrefixRule>()
                    .is_some_and(|prefix| prefix.decision == Decision::Allow);
                (!is_allow).then(|| (program.clone(), Arc::clone(rule)))
            })
        })
        .collect();
    Policy::from_parts(
        rules,
        policy.network_rules().to_vec(),
        policy.host_executables().clone(),
    )
}

pub fn proposed(
    options: &Options,
    policy: &Policy,
    commands: &[Vec<String>],
    evaluation: &Evaluation,
    fallback: &impl Fn(&[String]) -> Decision,
    match_options: &MatchOptions,
) -> Option<Vec<String>> {
    if !options.honor_allow_prefix_rules {
        return None;
    }
    let has_policy_match = evaluation
        .matched_rules
        .iter()
        .any(|rule| matches!(rule, RuleMatch::PrefixRuleMatch { .. }));
    if let Some(prefix) = options.prefix_rule.as_ref()
        && !prefix.is_empty()
        && !BANNED_PREFIX_SUGGESTIONS
            .iter()
            .any(|banned| prefix.iter().map(String::as_str).eq(banned.iter().copied()))
        && !has_policy_match
    {
        let mut updated = policy.clone();
        if updated.add_prefix_rule(prefix, Decision::Allow).is_ok()
            && commands.iter().all(|cmd| {
                updated
                    .check_with_options(cmd, fallback, match_options)
                    .decision
                    == Decision::Allow
            })
        {
            return Some(prefix.clone());
        }
    }
    if evaluation.matched_rules.iter().any(|rule| {
        matches!(rule, RuleMatch::PrefixRuleMatch { .. }) && rule.decision() == Decision::Prompt
    }) {
        return None;
    }
    evaluation.matched_rules.iter().find_map(|rule| match rule {
        RuleMatch::HeuristicsRuleMatch {
            command,
            decision: Decision::Prompt,
        } => Some(command.clone()),
        _ => None,
    })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Append {
    pub path: PathBuf,
    pub prefix: Vec<String>,
    current_policy: Option<CurrentPolicy>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CurrentPolicy {
    sources: Vec<crate::exec_policy::Source>,
    approved_prefixes: Vec<Vec<String>>,
    requirements: Vec<crate::requirements::Layer>,
}

pub fn append(request: Append) -> Result<Value, String> {
    if !request.path.is_absolute() {
        return Err("exec policy path must be absolute".into());
    }
    // Runtime captures this view under the shared update lock. Never reload the
    // just-written file or apply a consumer model's allow-rule filtering here.
    let mut current_policy = if let Some(current) = request.current_policy {
        let constraints = codex_config::ConfigRequirements::try_from(crate::requirements::compose(
            current.requirements,
        )?)
        .map_err(|e| e.to_string())?;
        crate::exec_policy::effective_policy(
            &current.sources,
            constraints.exec_policy.as_ref().map(|m| m.value.as_ref()),
            &Options {
                approved_prefixes: current.approved_prefixes,
                ..Options::default()
            },
        )?
        .0
    } else {
        // Compatibility for old bare append requests with no running policy.
        Policy::empty()
    };
    codex_execpolicy::blocking_append_allow_prefix_rule(&request.path, &request.prefix)
        .map_err(|e| e.to_string())?;
    let existing_evaluation = current_policy.check_multiple_with_options(
        [&request.prefix],
        &|_| Decision::Forbidden,
        &MatchOptions {
            resolve_host_executables: true,
        },
    );
    let already_allowed = existing_evaluation.decision == Decision::Allow
        && existing_evaluation.matched_rules.iter().any(|rule_match| {
            matches!(rule_match, RuleMatch::PrefixRuleMatch { .. })
                && rule_match.decision() == Decision::Allow
        });
    // Upstream publishes the in-memory rule only after the append succeeds.
    // Rule validation is also after disk append, not an invented transaction.
    if !already_allowed {
        current_policy
            .add_prefix_rule(&request.prefix, Decision::Allow)
            .map_err(|e| e.to_string())?;
    }
    Ok(json!({
        "execpolicy_amendment_written": true,
        "execpolicy_amendment_published": !already_allowed,
    }))
}

// Exact list from pinned core/src/exec_policy.rs, not a first-token heuristic.
pub(crate) static BANNED_PREFIX_SUGGESTIONS: &[&[&str]] = &[
    &["/bin/bash"],
    &["/bin/bash", "-c"],
    &["/bin/bash", "-lc"],
    &["/bin/sh"],
    &["/bin/sh", "-c"],
    &["/bin/sh", "-lc"],
    &["/bin/zsh"],
    &["/bin/zsh", "-c"],
    &["/bin/zsh", "-lc"],
    &["Rscript"],
    &["bash"],
    &["bash", "-c"],
    &["bash", "-lc"],
    &["bun"],
    &["bun", "-e"],
    &["bun", "run"],
    &["cmd"],
    &["cmd", "/c"],
    &["cmd", "/k"],
    &["cmd.exe"],
    &["cmd.exe", "/c"],
    &["cmd.exe", "/k"],
    &["dash"],
    &["dash", "-c"],
    &["deno"],
    &["deno", "eval"],
    &["env"],
    &["fish"],
    &["fish", "-c"],
    &["git"],
    &["julia"],
    &["julia", "-e"],
    &["ksh"],
    &["ksh", "-c"],
    &["lua"],
    &["lua", "-e"],
    &["node"],
    &["node", "-e"],
    &["nodejs"],
    &["nodejs", "-e"],
    &["npm", "run"],
    &["osascript"],
    &["perl"],
    &["perl", "-e"],
    &["php"],
    &["php", "-r"],
    &["pnpm", "run"],
    &["powershell"],
    &["powershell", "-Command"],
    &["powershell", "-EncodedCommand"],
    &["powershell", "-File"],
    &["powershell", "-c"],
    &["powershell.exe"],
    &["powershell.exe", "-Command"],
    &["powershell.exe", "-EncodedCommand"],
    &["powershell.exe", "-File"],
    &["powershell.exe", "-c"],
    &["pwsh"],
    &["pwsh", "-Command"],
    &["pwsh", "-EncodedCommand"],
    &["pwsh", "-File"],
    &["pwsh", "-c"],
    &["pwsh", "-e"],
    &["pwsh", "-ec"],
    &["pwsh", "-f"],
    &["py"],
    &["py", "-3"],
    &["pypy"],
    &["pypy3"],
    &["python"],
    &["python", "-"],
    &["python", "-c"],
    &["python3"],
    &["python3", "-"],
    &["python3", "-c"],
    &["pythonw"],
    &["pyw"],
    &["rm"],
    &["ruby"],
    &["ruby", "-e"],
    &["sh"],
    &["sh", "-c"],
    &["sh", "-lc"],
    &["sudo"],
    &["yarn", "run"],
    &["zsh"],
    &["zsh", "-c"],
    &["zsh", "-lc"],
];
