//! Creation-time source discovery. No rule files are read during model execution.

use std::fs;
use std::io::ErrorKind;
use std::path::PathBuf;

use codex_execpolicy::PolicyParser;
use serde::Deserialize;

use crate::exec_policy::Source;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    config_folders: Vec<PathBuf>,
    sources: Vec<Source>,
    inherited: Option<Inherited>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Inherited {
    managed_identity: Option<Vec<String>>,
    sources: Vec<Source>,
}

pub struct Loaded {
    pub sources: Vec<Source>,
    pub warnings: Vec<String>,
}

pub fn load(request: Request, managed_identity: &Option<Vec<String>>) -> Result<Loaded, String> {
    if request
        .config_folders
        .iter()
        .any(|folder| !folder.is_absolute())
    {
        return Err("exec policy config folder must be absolute".into());
    }
    if let Some(inherited) = request.inherited
        && inherited.managed_identity == *managed_identity
    {
        return Ok(Loaded {
            sources: inherited.sources,
            warnings: Vec::new(),
        });
    }
    let mut paths = Vec::new();
    // Match collect_policy_files: finish discovery before reading any content,
    // preserve layer order, sort within a layer, and exclude symlink entries.
    for folder in request.config_folders {
        let dir = folder.join("rules");
        let read_error =
            |error| format!("failed to read rules files from {}: {error}", dir.display());
        let entries = match fs::read_dir(&dir) {
            Ok(entries) => entries,
            Err(error) if error.kind() == ErrorKind::NotFound => continue,
            Err(error) => return Err(read_error(error)),
        };
        let mut layer_paths = Vec::new();
        for entry in entries {
            let entry = entry.map_err(read_error)?;
            let kind = entry.file_type().map_err(read_error)?;
            let path = entry.path();
            if path.extension().and_then(|ext| ext.to_str()) == Some("rules") && kind.is_file() {
                layer_paths.push(path);
            }
        }
        layer_paths.sort();
        paths.extend(layer_paths);
    }
    let mut parser = PolicyParser::new();
    let mut captured = Vec::new();
    // Keep reads lazy: an earlier parse failure falls back immediately, before
    // attempting a later file read. UTF-8/read failures are fatal, not warnings.
    let files = paths.into_iter().map(|path| -> Result<Source, String> {
        let contents = fs::read_to_string(&path)
            .map_err(|error| format!("failed to read rules file {}: {error}", path.display()))?;
        Ok(Source {
            name: path.to_string_lossy().into_owned(),
            contents,
        })
    });
    for source in files.chain(request.sources.into_iter().map(Ok)) {
        let source = source?;
        if let Err(error) = parser.parse(&source.name, &source.contents) {
            // Drop only user sources. The independent managed policy is
            // validated before loading and applied on every command admission.
            return Ok(Loaded {
                sources: Vec::new(),
                warnings: vec![format!(
                    "failed to parse rules file {}: {error}",
                    source.name
                )],
            });
        }
        captured.push(source);
    }
    Ok(Loaded {
        sources: captured,
        warnings: Vec::new(),
    })
}
