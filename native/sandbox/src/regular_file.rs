//! Bounded file helpers must not open devices or impersonate named-pipe servers.
//! Follows the pinned exec-server/regular_file.rs open flags and disk-file check.

use std::fs::{File, OpenOptions};
use std::io;
use std::path::Path;

pub fn open(path: &Path) -> io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::SECURITY_IDENTIFICATION;
        options.security_qos_flags(SECURITY_IDENTIFICATION);
    }
    let file = options.open(path)?;
    if !is_disk_file(&file) || !file.metadata()?.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("path `{}` is not a regular file", path.display()),
        ));
    }
    Ok(file)
}

#[cfg(windows)]
fn is_disk_file(file: &File) -> bool {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{FILE_TYPE_DISK, GetFileType};
    // SAFETY: the file owns a valid handle throughout this synchronous call.
    unsafe { GetFileType(file.as_raw_handle()) == FILE_TYPE_DISK }
}

#[cfg(not(windows))]
fn is_disk_file(_file: &File) -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    #[test]
    fn reads_regular_file_and_rejects_directory() {
        let root = std::env::temp_dir().join(format!("corki-file-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let path = root.join("中文 file.txt");
        std::fs::write(&path, "你好").unwrap();
        let mut content = String::new();
        open(&path).unwrap().read_to_string(&mut content).unwrap();
        assert_eq!(content, "你好");
        assert!(open(&root).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn rejects_windows_devices() {
        assert!(open(Path::new("NUL")).is_err());
        assert!(open(Path::new(r"\\.\NUL")).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn rejects_fifo_without_waiting_for_writer() {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;
        let path = std::env::temp_dir().join(format!("corki-fifo-{}", std::process::id()));
        let native = CString::new(path.as_os_str().as_bytes()).unwrap();
        // SAFETY: NUL-terminated pathname remains alive for the call.
        assert_eq!(unsafe { libc::mkfifo(native.as_ptr(), 0o600) }, 0);
        assert!(open(&path).is_err());
        std::fs::remove_file(path).unwrap();
    }
}
