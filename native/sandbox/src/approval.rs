//! Configuration admission differs from strict per-command revalidation.

use codex_config::ConfigRequirements;
use codex_protocol::protocol::AskForApproval;
use serde::Deserialize;

#[derive(Default, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Constraint {
    #[default]
    Configured,
    Memory,
    Guardian,
}

pub fn resolve(
    requested: AskForApproval,
    explicit: bool,
    mode: Constraint,
    requirements: &ConfigRequirements,
    startup: bool,
    memory_derivation: bool,
) -> Result<(AskForApproval, Vec<String>), String> {
    // phase2::agent::get_config replaces the parent constraint with allow_only(Never).
    if memory_derivation || matches!(mode, Constraint::Memory | Constraint::Guardian) {
        if requested != AskForApproval::Never {
            return Err("internal worker approval policy must be never".into());
        }
        return Ok((AskForApproval::Never, Vec::new()));
    }
    let constraint = &requirements.approval_policy;
    match constraint.can_set(&requested) {
        Ok(()) => Ok((requested, Vec::new())),
        Err(err) if startup => {
            let fallback = constraint.value();
            constraint.can_set(&fallback).map_err(|e| e.to_string())?;
            // core/config/mod.rs:3659 handles implicit defaults before the final
            // apply_requirement_constrained_value call at 3986. Only the latter
            // appends a startup warning; both paths choose the required default.
            let warnings = if explicit {
                vec![format!(
                    "Configured value for `approval_policy` is disallowed by requirements; falling back to required value {fallback:?}. Details: {err}"
                )]
            } else {
                Vec::new()
            };
            Ok((fallback, warnings))
        }
        Err(err) => Err(err.to_string()),
    }
}
