//! Source-relative managed constraints, using the pinned config parser and merger.

use std::path::{Path, PathBuf};

use codex_config::{
    AbsolutePathBuf, ConfigRequirements, ConfigRequirementsWithSources, RequirementSource,
    RequirementsLayerEntry, SandboxModeRequirement, TomlValue, compose_requirements_for_hostname,
    sandbox_mode_requirement_for_permission_profile,
};
use codex_protocol::models::PermissionProfile;
use codex_protocol::permissions::{
    FileSystemAccessMode, FileSystemPath, FileSystemSandboxEntry, FileSystemSandboxPolicy,
    ReadDenyMatcher,
};
use codex_protocol::protocol::AskForApproval;
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Layer {
    source: String,
    base_dir: Option<PathBuf>,
    value: TomlValue,
}

pub fn compose(layers: Vec<Layer>) -> Result<ConfigRequirementsWithSources, String> {
    let mut entries = Vec::new();
    for layer in layers {
        let table = layer
            .value
            .as_table()
            .ok_or("requirements must be a table")?;
        if table.keys().any(|key| {
            !matches!(
                key.as_str(),
                "allowed_sandbox_modes"
                    | "allowed_approval_policies"
                    | "permissions"
                    | "allowed_permission_profiles"
                    | "default_permissions"
                    | "rules"
            )
        }) {
            return Err("unsupported managed execution requirement".into());
        }
        let source = if Path::new(&layer.source).is_absolute() {
            RequirementSource::SystemRequirementsToml {
                file: AbsolutePathBuf::try_from(layer.source).map_err(|e| e.to_string())?,
            }
        } else {
            RequirementSource::EnterpriseManaged {
                id: layer.source.clone(),
                name: layer.source,
            }
        };
        let mut entry = RequirementsLayerEntry::from_toml_value(source, layer.value);
        if let Some(base_dir) = layer.base_dir {
            entry = entry.with_base_dir(
                base_dir
                    .try_into()
                    .map_err(|e: std::io::Error| e.to_string())?,
            );
        }
        entries.push(entry);
    }
    Ok(compose_requirements_for_hostname(entries, None)
        .map_err(|e| e.to_string())?
        .unwrap_or_default())
}

pub fn apply(
    profile: PermissionProfile,
    constraints: &ConfigRequirements,
    cwd: &Path,
    resolve: bool,
    approval_policy: AskForApproval,
) -> Result<(PermissionProfile, Vec<String>), String> {
    let mode = sandbox_mode_requirement_for_permission_profile(&profile);
    let deny = constraints
        .filesystem
        .as_ref()
        .filter(|s| !s.value.deny_read.is_empty());
    let validation = constraints
        .permission_profile
        .value
        .can_set(&profile)
        .map_err(|e| e.to_string())
        .and_then(|()| {
            if let Some(deny) = deny
                && matches!(
                    mode,
                    SandboxModeRequirement::DangerFullAccess
                        | SandboxModeRequirement::ExternalSandbox
                )
            {
                return Err(format!(
                    "sandbox_mode {mode:?} cannot enforce deny_read from {}",
                    deny.source
                ));
            }
            Ok(())
        });
    let original = profile.clone();
    let mut warnings = Vec::new();
    let mut profile = profile;
    if let Err(error) = validation {
        if !resolve {
            return Err(error);
        }
        if mode == SandboxModeRequirement::DangerFullAccess
            && approval_policy == AskForApproval::Never
        {
            return Err(format!(
                "approval_policy=never cannot accompany required read-only fallback: {error}"
            ));
        }
        warnings.push(format!(
            "permission profile fell back to read-only: {error}"
        ));
        profile = PermissionProfile::read_only();
    }
    let (mut filesystem, network) = profile.to_runtime_permissions();
    if profile != original {
        filesystem.preserve_deny_read_restrictions_from(&original.file_system_sandbox_policy());
    }
    if let Some(deny) = deny {
        let mut managed = FileSystemSandboxPolicy::restricted(Vec::new());
        for pattern in &deny.value.deny_read {
            let path = if pattern.contains_glob() {
                FileSystemPath::GlobPattern {
                    pattern: pattern.as_str().to_owned(),
                }
            } else {
                AbsolutePathBuf::try_from(pattern.as_str())
                    .map_err(|e| e.to_string())?
                    .into()
            };
            managed.entries.push(FileSystemSandboxEntry {
                path,
                access: FileSystemAccessMode::Deny,
                missing_path_behavior: None,
            });
        }
        filesystem.preserve_deny_read_restrictions_from(&managed);
        let matcher =
            ReadDenyMatcher::try_new_for_local_paths(&managed, cwd).map_err(|e| e.to_string())?;
        for entry in &filesystem.entries {
            if entry.access.can_read()
                && let FileSystemPath::Path { path } = &entry.path
                && let Ok(path) = path.to_abs_path()
                && matcher
                    .as_ref()
                    .is_some_and(|m| m.is_local_path_read_denied(path.as_path()))
            {
                return Err(format!(
                    "permissions.filesystem: readable root {} violates deny_read from {}",
                    path.display(),
                    deny.source
                ));
            }
        }
    }
    Ok((
        PermissionProfile::from_runtime_permissions_with_enforcement(
            profile.enforcement(),
            &filesystem,
            network,
        ),
        warnings,
    ))
}
