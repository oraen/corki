//! Negotiate product metadata semantics before admitting commands or context.

use codex_protocol::permissions::is_protected_metadata_name;
use std::ffi::OsStr;

pub fn require_corki_metadata(requested: bool) -> Result<(), String> {
    if requested && !is_protected_metadata_name(OsStr::new(".corki")) {
        return Err(
            "sandbox compiler lacks Corki workspace metadata protection; rebuild it".into(),
        );
    }
    Ok(())
}
