//! No-import URL computation using the parser and dependency versions pinned by Codex.
//! One instance belongs to one host operation; guest allocations die with its Store.

use url::{Host, Url};

#[no_mangle]
pub extern "C" fn allocate(len: usize) -> *mut u8 {
    let mut buffer = Vec::<u8>::with_capacity(len);
    let pointer = buffer.as_mut_ptr();
    std::mem::forget(buffer);
    pointer
}

fn canonical(raw: &str, agent_policy: bool) -> Result<String, i64> {
    let url = Url::parse(raw).map_err(|_| -1_i64)?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err(-2);
    }
    if agent_policy {
        if !url.username().is_empty() || url.password().is_some() || url.fragment().is_some() {
            return Err(-3);
        }
        let loopback = match url.host() {
            Some(Host::Domain(host)) => host == "localhost",
            Some(Host::Ipv4(address)) => address.is_loopback(),
            Some(Host::Ipv6(address)) => address.is_loopback(),
            None => false,
        };
        if url.scheme() == "http" && !loopback {
            return Err(-4);
        }
    }
    Ok(url.into())
}

/// Return packed (pointer << 32 | byte length), or a negative policy/parser error.
///
/// # Safety
/// The pointer must identify an allocated readable buffer of `len` bytes. The host
/// must constrain linear memory below 2 GiB so packed results remain positive.
#[no_mangle]
pub unsafe extern "C" fn parse(pointer: *const u8, len: usize, agent_policy: i32) -> i64 {
    let Ok(raw) = std::str::from_utf8(std::slice::from_raw_parts(pointer, len)) else {
        return -1;
    };
    packed(canonical(raw, agent_policy != 0))
}

/// Resolve a redirect Location with the same URL grammar as initial requests.
///
/// # Safety
/// Both buffers must be allocated/readable, and the host must bound memory below 2 GiB.
#[no_mangle]
pub unsafe extern "C" fn join(
    base: *const u8,
    base_len: usize,
    location: *const u8,
    location_len: usize,
) -> i64 {
    let (Ok(base), Ok(location)) = (
        std::str::from_utf8(std::slice::from_raw_parts(base, base_len)),
        std::str::from_utf8(std::slice::from_raw_parts(location, location_len)),
    ) else {
        return -1;
    };
    let result = Url::parse(base)
        .and_then(|url| url.join(location))
        .map(String::from)
        .map_err(|_| -1);
    packed(result)
}

fn packed(result: Result<String, i64>) -> i64 {
    match result {
        Ok(value) => {
            let mut bytes = value.into_bytes();
            let result = ((bytes.as_mut_ptr() as u64) << 32) | bytes.len() as u64;
            std::mem::forget(bytes);
            result as i64
        }
        Err(status) => status,
    }
}

#[cfg(test)]
#[path = "tests.rs"]
mod tests;
