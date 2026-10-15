//! Compile user named profiles through the exact pinned upstream implementation.

use std::path::Path;

use codex_config::config_toml::ProjectConfig;
use codex_config::permissions_toml::{NetworkToml, PermissionsToml};
use codex_config::{AbsolutePathBuf, ConfigRequirementsToml};
use codex_protocol::config_types::TrustLevel;
use codex_protocol::config_types::WindowsSandboxLevel;
use codex_protocol::models::{ActivePermissionProfile, PermissionProfile};
use serde::Deserialize;

// The file is supplied by build.py's fixed-commit export, not the working tree.
// Keep upstream path/glob/inheritance behavior, without depending on codex-core.
#[allow(dead_code)]
pub(crate) mod upstream {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/reference/codex-rs/core/src/config/permissions.rs"
    ));
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    #[serde(rename = "type")]
    pub kind: String,
    pub permissions: Option<PermissionsToml>,
    default_permissions: Option<String>,
    project_trust: Option<TrustLevel>,
}

pub struct Resolved {
    pub profile: PermissionProfile,
    pub active: ActivePermissionProfile,
    pub roots: Vec<AbsolutePathBuf>,
    pub warnings: Vec<String>,
}

pub fn resolve(
    selection: Selection,
    requirements: &ConfigRequirementsToml,
    cwd: &Path,
) -> Result<Resolved, String> {
    if selection.kind != "selection" {
        return Err("invalid permission selection".into());
    }
    let catalog = crate::catalog::assemble(selection.permissions.as_ref(), requirements)?;
    let mut selected = selection.default_permissions;
    let mut warnings = Vec::new();
    if let Some(allowed) = requirements.allowed_permission_profiles.as_ref() {
        if let Some(id) = selected.as_ref()
            && allowed.get(id) != Some(&true)
        {
            warnings.push(format!("Configured permission_profile `{id}` is disallowed by requirements; falling back to `{}`", catalog.fallback.as_deref().unwrap()));
            selected = catalog.fallback.clone();
        } else if selected.is_none() {
            selected = catalog.fallback.clone();
        }
    }
    if catalog.profiles.as_ref().is_some_and(|p| !p.is_empty()) && selected.is_none() {
        return Err(
            "config defines permission profiles but does not set default_permissions".into(),
        );
    }
    // Host config resolution supplies active-project trust, not model arguments.
    let id = selected.unwrap_or_else(|| {
        upstream::default_builtin_permission_profile_name(
            &ProjectConfig {
                trust_level: selection.project_trust,
            },
            WindowsSandboxLevel::Disabled,
        )
        .to_string()
    });
    let (filesystem, network) = upstream::compile_permission_profile_selection(
        catalog.profiles.as_ref(),
        &id,
        None,
        &mut warnings,
    )
    .map_err(|e| e.to_string())?;
    if !upstream::is_builtin_permission_profile_name(&id) {
        let resolved =
            upstream::resolve_permission_profile(catalog.profiles.as_ref().unwrap(), &id)
                .map_err(|e| e.to_string())?;
        if let Some(mut network) = resolved.network {
            network.enabled = None;
            if network != NetworkToml::default() {
                return Err(
                    "named profile network proxy configuration is not yet supported".into(),
                );
            }
        }
    }
    let mut roots =
        upstream::compile_permission_profile_workspace_roots(catalog.profiles.as_ref(), &id, cwd)
            .map_err(|e| e.to_string())?;
    let mut seen = std::collections::HashSet::new();
    roots.retain(|root| seen.insert(root.clone()));
    let filesystem = filesystem.with_materialized_project_roots_for_workspace_roots(&roots);
    let profile = upstream::builtin_permission_profile(&id, None)
        .unwrap_or_else(|| PermissionProfile::from_runtime_permissions(&filesystem, network));
    let extends = selection
        .permissions
        .as_ref()
        .and_then(|p| p.entries.get(&id))
        .and_then(|p| p.extends.clone());
    Ok(Resolved {
        profile,
        active: ActivePermissionProfile { id, extends },
        roots,
        warnings,
    })
}
