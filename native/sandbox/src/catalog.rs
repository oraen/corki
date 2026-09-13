//! Native core/config/mod.rs catalog precedence and managed default validation.

use codex_config::ConfigRequirementsToml;
use codex_config::permissions_toml::PermissionsToml;

use crate::selection::upstream;

pub struct Catalog {
    pub profiles: Option<PermissionsToml>,
    pub fallback: Option<String>,
}

pub fn assemble(
    configured: Option<&PermissionsToml>,
    requirements: &ConfigRequirementsToml,
) -> Result<Catalog, String> {
    let mut profiles = configured.cloned();
    if let Some(managed) = requirements
        .permissions
        .as_ref()
        .filter(|p| !p.profiles.is_empty())
    {
        let profiles = profiles.get_or_insert_with(PermissionsToml::default);
        for (id, profile) in &managed.profiles {
            if profiles.entries.contains_key(id) {
                return Err(format!(
                    "managed permissions profile `{id}` conflicts with a config-defined profile of the same name"
                ));
            }
            profiles.entries.insert(id.clone(), profile.clone());
        }
    }
    upstream::validate_user_permission_profile_names(profiles.as_ref())
        .map_err(|e| e.to_string())?;
    let Some(allowed) = requirements.allowed_permission_profiles.as_ref() else {
        if requirements.default_permissions.is_some() {
            return Err("managed default_permissions requires allowed_permission_profiles".into());
        }
        return Ok(Catalog {
            profiles,
            fallback: None,
        });
    };
    for id in allowed.keys() {
        if !upstream::is_builtin_permission_profile_name(id)
            && !profiles
                .as_ref()
                .is_some_and(|p| p.entries.contains_key(id))
        {
            return Err(format!(
                "allowed_permission_profiles refers to undefined profile `{id}`"
            ));
        }
    }
    let fallback = requirements.default_permissions.clone().or_else(|| {
        (allowed.get(upstream::BUILT_IN_READ_ONLY_PROFILE) == Some(&true)
            && allowed.get(upstream::BUILT_IN_WORKSPACE_PROFILE) == Some(&true))
            .then(|| upstream::BUILT_IN_WORKSPACE_PROFILE.to_string())
    }).ok_or("managed default_permissions must be set unless both :workspace and :read-only are allowed")?;
    if allowed.get(&fallback) != Some(&true) {
        return Err(format!(
            "managed default_permissions `{fallback}` must be allowed by allowed_permission_profiles"
        ));
    }
    Ok(Catalog {
        profiles,
        fallback: Some(fallback),
    })
}
