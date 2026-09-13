//! Fixed file operations. Never runs user programs with helper permissions.
//!
//! Runtime permission derivation follows the pinned exec-server/fs_sandbox.rs;
//! bounded regular-file opening follows exec-server/regular_file.rs. The aggregate
//! project discovery contract is Corki's context/project_instructions.py.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read};
use std::path::{Path, PathBuf};

use base64::Engine;
use codex_protocol::models::PermissionProfile;
use codex_protocol::permissions::{
    FileSystemAccessMode, FileSystemPath, FileSystemSandboxEntry, FileSystemSpecialPath,
    NetworkSandboxPolicy,
};
use codex_utils_absolute_path::AbsolutePathBuf;
#[cfg(not(target_os = "linux"))]
use codex_utils_absolute_path::canonicalize_preserving_symlinks;
use serde::Deserialize;
use serde_json::{Value, json};

pub const ARG: &str = "--corki-fs-helper";

pub fn runtime_profile(
    profile: &PermissionProfile,
    cwd: &Path,
    executable: &Path,
) -> Result<PermissionProfile, String> {
    let mut policy = profile.file_system_sandbox_policy();
    if !policy.has_full_disk_read_access() {
        let minimal = FileSystemSandboxEntry::new(
            FileSystemPath::Special {
                value: FileSystemSpecialPath::Minimal,
            },
            FileSystemAccessMode::Read,
        );
        if !policy.entries.contains(&minimal) {
            policy.entries.push(minimal);
        }
    }
    let executable = AbsolutePathBuf::from_absolute_path(executable).map_err(|e| e.to_string())?;
    if !policy.can_read_local_path_with_cwd(executable.as_path(), cwd) {
        policy.entries.push(FileSystemSandboxEntry::new(
            executable.into(),
            FileSystemAccessMode::Read,
        ));
    }
    // The pinned executor normalizes only top-level OS aliases, not arbitrary
    // model-controlled symlinks. Linux resolves aliases in its sandbox helper.
    #[cfg(not(target_os = "linux"))]
    for entry in &mut policy.entries {
        if let FileSystemPath::Path { path } = &mut entry.path
            && let Ok(native) = path.to_abs_path()
        {
            *path = normalize_alias(native).into();
        }
    }
    Ok(
        PermissionProfile::from_runtime_permissions_with_enforcement(
            profile.enforcement(),
            &policy,
            NetworkSandboxPolicy::Restricted,
        ),
    )
}

#[cfg(not(target_os = "linux"))]
fn normalize_alias(path: AbsolutePathBuf) -> AbsolutePathBuf {
    let raw = path.to_path_buf();
    for ancestor in raw.ancestors() {
        if fs::symlink_metadata(ancestor).is_err() {
            continue;
        }
        let Ok(normalized) = canonicalize_preserving_symlinks(ancestor) else {
            continue;
        };
        if normalized == ancestor {
            continue;
        }
        if let Ok(suffix) = raw.strip_prefix(ancestor)
            && let Ok(result) = AbsolutePathBuf::from_absolute_path(normalized.join(suffix))
        {
            return result;
        }
    }
    path
}

#[derive(Deserialize)]
#[serde(
    tag = "operation",
    content = "arguments",
    rename_all = "snake_case",
    deny_unknown_fields
)]
enum Request {
    Patch(Patch),
    Image(Image),
    ProjectInstructions(ProjectInstructions),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Patch {
    patch: String,
    authority: crate::fs_patch::Authority,
    #[serde(default)]
    prepare: bool,
    #[serde(default)]
    approved: bool,
    #[serde(default)]
    retry: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Image {
    path: PathBuf,
    max_bytes: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProjectInstructions {
    cwd: PathBuf,
    config: Config,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    max_bytes: u64,
    fallback_filenames: Vec<String>,
    root_markers: Vec<String>,
    trust_level: Option<String>,
}

fn regular_file(path: &Path) -> io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    // Do not pretend the Unix open contract covers Windows devices / pipes.
    // The current bridge has no Windows enforcement backend either.
    #[cfg(not(unix))]
    return Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "native filesystem helper requires Unix",
    ));
    #[cfg(unix)]
    {
        let file = options.open(path)?;
        if !file.metadata()?.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("path `{}` is not a regular file", path.display()),
            ));
        }
        Ok(file)
    }
}

fn bounded_read(path: &Path, limit: u64) -> io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    regular_file(path)?.take(limit).read_to_end(&mut bytes)?;
    Ok(bytes)
}

fn project_instructions(request: ProjectInstructions) -> Result<String, String> {
    let ProjectInstructions { cwd, config } = request;
    if !cwd.is_absolute() {
        return Err("absolute project cwd required".into());
    }
    if !matches!(
        config.trust_level.as_deref(),
        None | Some("trusted" | "untrusted")
    ) {
        return Err("invalid project trust".into());
    }
    if config.max_bytes == 0 || config.trust_level.as_deref() == Some("untrusted") {
        return Ok("[]".into());
    }
    let root = cwd
        .ancestors()
        .find(|directory| {
            config
                .root_markers
                .iter()
                .any(|marker| directory.join(marker).metadata().is_ok())
        })
        .unwrap_or(&cwd);
    let mut directories = Vec::new();
    for directory in cwd.ancestors() {
        directories.push(directory);
        if directory == root {
            break;
        }
    }
    let mut names = vec!["AGENTS.override.md".to_string(), "AGENTS.md".to_string()];
    for name in config.fallback_filenames {
        if !name.is_empty() && !names.contains(&name) {
            names.push(name);
        }
    }
    // All metadata probes precede any budgeted reads: permission errors in a
    // later directory must not disappear because an earlier doc fills the budget.
    let mut paths = Vec::new();
    for directory in directories.into_iter().rev() {
        for name in &names {
            let path = directory.join(name);
            match path.metadata() {
                Ok(metadata) if metadata.is_file() => {
                    paths.push(path);
                    break;
                }
                Ok(_) => (),
                Err(error) if error.kind() == io::ErrorKind::NotFound => (),
                Err(error) => return Err(format!("{}: {error}", path.display())),
            }
        }
    }
    let mut remaining = config.max_bytes;
    let mut entries = Vec::new();
    for path in paths {
        if remaining == 0 {
            break;
        }
        let bytes = match bounded_read(&path, remaining) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(format!("{}: {error}", path.display())),
        };
        let text = String::from_utf8_lossy(&bytes);
        // Python str.strip also treats these four C0 separators as whitespace.
        if !text
            .trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
            .is_empty()
        {
            entries.push(json!({"text": text, "source": path}));
            remaining -= bytes.len() as u64;
        }
    }
    serde_json::to_string(&entries).map_err(|e| e.to_string())
}

fn run() -> Result<String, String> {
    let mut input = Vec::new();
    io::stdin()
        .lock()
        .take(8_000_001)
        .read_to_end(&mut input)
        .map_err(|e| e.to_string())?;
    if input.len() > 8_000_000 {
        return Err("filesystem helper input exceeds its limit".into());
    }
    match serde_json::from_slice::<Request>(&input).map_err(|e| e.to_string())? {
        Request::Patch(patch) => crate::fs_patch::apply(
            &patch.patch,
            &patch.authority,
            patch.prepare,
            patch.approved,
            patch.retry,
        ),
        Request::Image(image) => {
            let limit = image
                .max_bytes
                .checked_add(1)
                .ok_or("invalid image byte limit")?;
            let bytes = bounded_read(&image.path, limit).map_err(|e| e.to_string())?;
            if bytes.len() as u64 > image.max_bytes {
                return Err(format!("image exceeds {} byte limit", image.max_bytes));
            }
            Ok(base64::engine::general_purpose::STANDARD.encode(bytes))
        }
        Request::ProjectInstructions(request) => project_instructions(request),
    }
}

pub fn main() {
    let response: Value = match run() {
        Ok(value) => json!({"ok": value}),
        Err(error) => json!({"error": error.chars().take(2000).collect::<String>()}),
    };
    println!("{response}");
}
