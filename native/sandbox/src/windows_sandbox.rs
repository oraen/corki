//! Explicit-home Windows sandbox launch adapter. No Codex home discovery.

#[cfg(windows)]
use std::collections::HashMap;
use std::path::{Path, PathBuf};

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use codex_protocol::config_types::WindowsSandboxLevel;
use codex_protocol::models::PermissionProfile;
use codex_utils_absolute_path::AbsolutePathBuf;
use serde::{Deserialize, Serialize};

pub const ARG: &str = "--corki-windows-sandbox";
const AUTHORITY_LIMIT: usize = 1_000_000;
// CreateProcessW counts UTF-16 code units, including the terminating NUL.
const WINDOWS_COMMAND_LINE_LIMIT: usize = 32_767;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Authority {
    pub home: PathBuf,
    pub cwd: PathBuf,
    pub policy_cwd: AbsolutePathBuf,
    pub workspace_roots: Vec<AbsolutePathBuf>,
    pub profile: PermissionProfile,
    pub level: WindowsSandboxLevel,
    pub command: Vec<String>,
}

impl Authority {
    fn validate(&self) -> Result<(), String> {
        if !self.home.is_absolute() || !self.cwd.is_absolute() {
            return Err("Windows sandbox requires absolute host home and cwd".into());
        }
        if self.workspace_roots.is_empty() {
            return Err("Windows sandbox requires explicit host workspace roots".into());
        }
        if !matches!(self.profile, PermissionProfile::Managed { .. }) {
            return Err("Windows sandbox launch requires managed permissions".into());
        }
        if self.command.first().is_none_or(String::is_empty)
            || self.command.iter().any(|arg| arg.contains('\0'))
        {
            return Err("Windows sandbox requires a nonempty command without NUL bytes".into());
        }
        if self.level == WindowsSandboxLevel::Disabled {
            return Err("Windows sandbox launch cannot disable its backend".into());
        }
        Ok(())
    }

    pub fn command_args(&self, executable: &Path) -> Result<Vec<String>, String> {
        self.validate()?;
        let bytes = serde_json::to_vec(self).map_err(|error| error.to_string())?;
        if bytes.len() > AUTHORITY_LIMIT {
            return Err("Windows sandbox authority exceeds its limit".into());
        }
        let executable = executable
            .to_str()
            .ok_or("invalid sandbox executable path")?;
        if executable.is_empty() || executable.contains(['\0', '"']) || executable.ends_with('\\') {
            return Err("invalid sandbox executable path".into());
        }
        let encoded = URL_SAFE_NO_PAD.encode(bytes);
        // The executable may need surrounding quotes. The other two arguments
        // have no whitespace/quotes and need no escaping. Include two spaces
        // and the final NUL; reserve quotes even when they are not necessary.
        let units = executable.encode_utf16().count() + 2 + 2 + ARG.len() + encoded.len() + 1;
        if units > WINDOWS_COMMAND_LINE_LIMIT {
            return Err("Windows sandbox launch exceeds the 32767 UTF-16 command-line limit; shorten the command or permission configuration".into());
        }
        Ok(vec![executable.into(), ARG.into(), encoded])
    }

    fn decode(encoded: &str) -> Result<Self, String> {
        if encoded.len() > AUTHORITY_LIMIT * 4 / 3 + 4 {
            return Err("Windows sandbox authority exceeds its limit".into());
        }
        let bytes = URL_SAFE_NO_PAD
            .decode(encoded)
            .map_err(|error| error.to_string())?;
        let authority: Self = serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
        authority.validate()?;
        Ok(authority)
    }
}

#[cfg(windows)]
pub fn level_for_profile(
    profile: &PermissionProfile,
    policy_cwd: &Path,
) -> Result<WindowsSandboxLevel, String> {
    let cwd = AbsolutePathBuf::from_absolute_path(policy_cwd).map_err(|e| e.to_string())?;
    let sandbox = codex_sandboxing::SandboxType::WindowsRestrictedToken;
    // Decide before launch using the pinned backend's actual policy checks.
    // Elevation here provisions the restricted identity; it never means running
    // the model command as administrator or relaxing its file permissions.
    if codex_sandboxing::resolve_windows_restricted_token_filesystem_overrides(
        sandbox,
        profile,
        &cwd,
        WindowsSandboxLevel::RestrictedToken,
    )
    .is_ok()
    {
        return Ok(WindowsSandboxLevel::RestrictedToken);
    }
    codex_sandboxing::resolve_windows_elevated_filesystem_overrides(sandbox, profile, &cwd, true)?;
    Ok(WindowsSandboxLevel::Elevated)
}

#[cfg(windows)]
pub async fn spawn(
    authority: Authority,
    environment: HashMap<String, String>,
    tty: bool,
) -> Result<codex_utils_pty::SpawnedProcess, String> {
    use codex_windows_sandbox::{WindowsSandboxProxySettingsMode, WindowsSandboxSessionRequest};
    authority.validate()?;
    // Match the pinned direct-spawn boundary: derive carveouts against the
    // host policy root, not the model-selected command working directory.
    let overrides = if authority.level == WindowsSandboxLevel::Elevated {
        codex_sandboxing::resolve_windows_elevated_filesystem_overrides(
            codex_sandboxing::SandboxType::WindowsRestrictedToken,
            &authority.profile,
            &authority.policy_cwd,
            true,
        )
    } else {
        codex_sandboxing::resolve_windows_restricted_token_filesystem_overrides(
            codex_sandboxing::SandboxType::WindowsRestrictedToken,
            &authority.profile,
            &authority.policy_cwd,
            authority.level,
        )
    }?;
    codex_windows_sandbox::spawn_windows_sandbox_session_for_level(WindowsSandboxSessionRequest {
        permission_profile: &authority.profile,
        workspace_roots: &authority.workspace_roots,
        codex_home: &authority.home,
        command: authority.command,
        cwd: &authority.cwd,
        env_map: environment,
        windows_sandbox_level: authority.level,
        proxy_enforced: false,
        network_proxy_restricting_sid: None,
        proxy_settings_mode: WindowsSandboxProxySettingsMode::Reconcile,
        timeout_ms: None,
        read_roots_override: overrides
            .as_ref()
            .and_then(|value| value.read_roots_override.as_deref()),
        read_roots_include_platform_defaults: overrides
            .as_ref()
            .is_some_and(|value| value.read_roots_include_platform_defaults),
        write_roots_override: overrides
            .as_ref()
            .and_then(|value| value.write_roots_override.as_deref()),
        deny_read_paths_override: overrides
            .as_ref()
            .map_or(&[], |value| value.additional_deny_read_paths.as_slice()),
        deny_write_paths_override: overrides
            .as_ref()
            .map_or(&[], |value| value.additional_deny_write_paths.as_slice()),
        tty,
        stdin_open: true,
        use_private_desktop: true,
    })
    .await
    .map_err(|error| error.to_string())
}

#[cfg(windows)]
pub async fn spawn_if_wrapped(
    command: &[String],
    cwd: &Path,
    environment: &HashMap<String, String>,
) -> Result<Option<codex_utils_pty::SpawnedProcess>, String> {
    if command.len() != 3 || command[1] != ARG {
        return Ok(None);
    }
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    if Path::new(&command[0]) != executable {
        return Ok(None);
    }
    let authority = Authority::decode(&command[2])?;
    if authority.cwd != cwd {
        return Err("Windows sandbox cwd changed after admission".into());
    }
    spawn(authority, environment.clone(), true).await.map(Some)
}

#[cfg(windows)]
pub fn main() -> ! {
    let result = (|| -> Result<i32, String> {
        let args = std::env::args().skip(2).collect::<Vec<_>>();
        if args.len() != 1 {
            return Err("Windows sandbox requires exactly one authority argument".into());
        }
        let authority = Authority::decode(&args[0])?;
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .map_err(|error| error.to_string())?;
        // Capture the already filtered execution environment at launch, not the
        // compiler's own environment. Environment values never travel in argv.
        let result = runtime.block_on(async {
            let session = spawn(authority, std::env::vars().collect(), false).await?;
            Ok(codex_windows_sandbox::forward_sandbox_session_stdio(session).await)
        });
        runtime.shutdown_timeout(std::time::Duration::from_secs(1));
        result
    })();
    std::process::exit(match result {
        Ok(code) => code,
        Err(error) => {
            eprintln!("Windows sandbox failed: {error}");
            1
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn authority() -> Authority {
        let directory = std::env::current_dir().unwrap();
        Authority {
            home: directory.join("private home"),
            workspace_roots: vec![directory.clone().try_into().unwrap()],
            policy_cwd: directory.join("host workspace").try_into().unwrap(),
            cwd: directory,
            profile: PermissionProfile::read_only(),
            level: WindowsSandboxLevel::RestrictedToken,
            command: vec!["program".into(), "中文 argument".into()],
        }
    }

    #[test]
    fn authority_roundtrip_preserves_host_identity_and_command() {
        let original = authority();
        let argv = original.command_args(Path::new("helper")).unwrap();
        let restored = Authority::decode(&argv[2]).unwrap();
        assert_eq!(restored.home, original.home);
        assert_eq!(restored.cwd, original.cwd);
        assert_eq!(restored.policy_cwd, original.policy_cwd);
        assert_ne!(restored.policy_cwd.as_path(), restored.cwd);
        assert_eq!(restored.command, original.command);
    }

    #[test]
    fn malformed_or_disabled_authority_cannot_launch() {
        assert!(Authority::decode("not:base64").is_err());
        assert!(Authority::decode(&"a".repeat(AUTHORITY_LIMIT * 2)).is_err());
        let mut value = authority();
        value.home = PathBuf::from("relative");
        assert!(value.command_args(Path::new("helper")).is_err());
        value = authority();
        value.level = WindowsSandboxLevel::Disabled;
        assert!(value.command_args(Path::new("helper")).is_err());
        value = authority();
        value.workspace_roots.clear();
        assert!(value.command_args(Path::new("helper")).is_err());
        value = authority();
        value.profile = PermissionProfile::Disabled;
        assert!(value.command_args(Path::new("helper")).is_err());
    }

    #[test]
    fn oversized_launch_is_rejected_before_process_creation() {
        let mut value = authority();
        value.command.push("x".repeat(WINDOWS_COMMAND_LINE_LIMIT));
        assert!(
            value
                .command_args(Path::new("helper.exe"))
                .unwrap_err()
                .contains("32767")
        );
        let value = authority();
        // Non-BMP characters count as two UTF-16 units, not one character.
        let executable = format!("{}helper.exe", "🦀".repeat(17_000));
        assert!(
            value
                .command_args(Path::new(&executable))
                .unwrap_err()
                .contains("32767")
        );
        assert!(value.command_args(Path::new("helper\0.exe")).is_err());
    }
}
