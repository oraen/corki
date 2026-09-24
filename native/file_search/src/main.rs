//! One-shot local candidate search. No credentials, network or model APIs.
//! The Python owner cancels/kills/joins this process when its query expires.

use std::collections::BTreeMap;
use std::io::{self, Read};
use std::time::{Duration, Instant};

use ignore::WalkBuilder;
use nucleo_matcher::pattern::{CaseMatching, Normalization, Pattern};
use nucleo_matcher::{Config, Matcher, Utf32Str};
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    query: String,
    #[serde(default = "default_limit")]
    limit: usize,
}

fn default_limit() -> usize {
    100
}

#[derive(Debug, Serialize)]
struct Candidate {
    path: String,
    directory: bool,
    score: u32,
}

fn search(request: Request) -> Result<Vec<Candidate>, &'static str> {
    if request.query.chars().count() > 1000 || !(1..=100).contains(&request.limit) {
        return Err("invalid search limits");
    }
    if request.query.is_empty() {
        return Ok(Vec::new());
    }
    let pattern = Pattern::parse(&request.query, CaseMatching::Ignore, Normalization::Smart);
    let mut matcher = Matcher::new(Config::DEFAULT.match_paths());
    let mut chars = Vec::new();
    // Reverse scores, then original path: bounded best matches without storing
    // a second full project index. This also includes empty directories.
    let mut best = BTreeMap::new();
    let started = Instant::now();
    let mut walker = WalkBuilder::new(".");
    walker.hidden(false).follow_links(true).require_git(true);
    for entry in walker.build() {
        if started.elapsed() > Duration::from_secs(3) {
            return Err("file search timed out");
        }
        // Codex skips individual traversal errors, including symlink loops.
        let Ok(entry) = entry else { continue };
        if entry.depth() == 0 {
            continue;
        }
        let path = entry.path().strip_prefix(".").unwrap_or(entry.path());
        let Some(path) = path.to_str() else { continue };
        // Never surface control names or a lossy path selecting another file.
        if path.len() > 65536 || path.chars().any(char::is_control) {
            continue;
        }
        let haystack = Utf32Str::new(path, &mut chars);
        let Some(score) = pattern.score(haystack, &mut matcher) else {
            continue;
        };
        let key = (std::cmp::Reverse(score), path.to_owned());
        if best.len() >= request.limit
            && best
                .last_key_value()
                .is_some_and(|(worst, _)| key >= *worst)
        {
            continue;
        }
        best.insert(
            key,
            Candidate {
                path: path.to_owned(),
                directory: entry.file_type().is_some_and(|kind| kind.is_dir()),
                score,
            },
        );
        if best.len() > request.limit {
            best.pop_last();
        }
    }
    Ok(best.into_values().collect())
}

fn main() {
    let mut input = Vec::new();
    let result = (|| {
        io::stdin()
            .take(16385)
            .read_to_end(&mut input)
            .map_err(|_| "input failed")?;
        if input.len() > 16384 {
            return Err("input too large");
        }
        let request = serde_json::from_slice(&input).map_err(|_| "invalid request")?;
        search(request)
    })();
    match result {
        Ok(rows) => {
            if serde_json::to_writer(io::stdout().lock(), &rows).is_err() {
                std::process::exit(1);
            }
        }
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
