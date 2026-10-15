//! No-import WASM bridge for the exact engine used by pinned Codex MCP policy.
//! The host owns one fresh instance per operation; no pointers outlive that store.

use regex_lite::Regex;

#[no_mangle]
pub extern "C" fn allocate(len: usize) -> *mut u8 {
    let mut buffer = Vec::<u8>::with_capacity(len);
    let pointer = buffer.as_mut_ptr();
    std::mem::forget(buffer);
    pointer
}

unsafe fn text<'a>(pointer: *const u8, len: usize) -> Option<&'a str> {
    std::str::from_utf8(std::slice::from_raw_parts(pointer, len)).ok()
}

fn full(expression: &str) -> Result<Regex, regex_lite::Error> {
    Regex::new(&format!(r"\A(?:{expression})\z"))
}

/// Return 0 for valid, 1 for invalid original, 2 for invalid full wrapper.
///
/// # Safety
/// The caller must provide an allocated readable buffer of `len` bytes.
#[no_mangle]
pub unsafe extern "C" fn validate(pointer: *const u8, len: usize) -> i32 {
    let Some(expression) = text(pointer, len) else {
        return 1;
    };
    if Regex::new(expression).is_err() {
        return 1;
    }
    if full(expression).is_err() {
        2
    } else {
        0
    }
}

/// The host supplies valid buffers; -1 denotes an invalid expression/input.
///
/// # Safety
/// Both pointers must identify readable buffers of the corresponding lengths.
#[no_mangle]
pub unsafe extern "C" fn matches(
    pattern: *const u8,
    pattern_len: usize,
    candidate: *const u8,
    candidate_len: usize,
) -> i32 {
    let (Some(expression), Some(candidate)) =
        (text(pattern, pattern_len), text(candidate, candidate_len))
    else {
        return -1;
    };
    match full(expression) {
        Ok(regex) => i32::from(regex.is_match(candidate)),
        Err(_) => -1,
    }
}

#[cfg(test)]
#[path = "tests.rs"]
mod tests;

/// Validate the unanchored regex engine used by lifecycle hook matchers.
///
/// # Safety
/// The pointer must identify a readable buffer of the supplied length.
#[no_mangle]
pub unsafe extern "C" fn hook_validate(pointer: *const u8, len: usize) -> i32 {
    match text(pointer, len) {
        Some(pattern) => i32::from(regex::Regex::new(pattern).is_err()),
        None => 1,
    }
}

/// Search using the native hook regex language, not the MCP full-value wrapper.
///
/// # Safety
/// Both pointers must identify readable buffers of the corresponding lengths.
#[no_mangle]
pub unsafe extern "C" fn hook_matches(
    pattern: *const u8,
    pattern_len: usize,
    candidate: *const u8,
    candidate_len: usize,
) -> i32 {
    let (Some(expression), Some(candidate)) =
        (text(pattern, pattern_len), text(candidate, candidate_len))
    else {
        return -1;
    };
    match regex::Regex::new(expression) {
        Ok(regex) => i32::from(regex.is_match(candidate)),
        Err(_) => -1,
    }
}
