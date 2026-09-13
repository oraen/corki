//! Pure text rendering through the pinned tracker; no workspace filesystem reads.
#![allow(dead_code)]

include!("../reference/codex-rs/core/src/turn_diff_tracker.rs");

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderRequest {
    cwd: PathUri,
    left_path: PathUri,
    right_path: PathUri,
    left_content: Option<String>,
    right_content: Option<String>,
}

pub fn render(request: RenderRequest) -> serde_json::Value {
    let tracker = TurnDiffTracker::with_environment_display_roots([("".to_owned(), request.cwd)]);
    let result = tracker.render_diff(
        &TrackedPath::new("", &request.left_path),
        request.left_content.as_deref(),
        &TrackedPath::new("", &request.right_path),
        request.right_content.as_deref(),
    );
    serde_json::json!({"version": 1, "diff": result})
}
