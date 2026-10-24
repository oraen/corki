// A separate test target avoids compiling the borrowed Codex core modules' tests,
// which require their original workspace's core_test_support and crate layout.
#[path = "../src/windows_sandbox.rs"]
mod windows_sandbox;
