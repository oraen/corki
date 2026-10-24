//! Keep batch-file quoting in Rust's Windows Command implementation, like Codex.
//! The Python owner assigns this helper to its Job before any user code can run.

pub const ARG: &str = "--corki-windows-program";

pub fn main() {
    #[cfg(windows)]
    let result = run();
    #[cfg(not(windows))]
    let result: std::io::Result<i32> = Err(std::io::Error::other("requires Windows"));
    let code = match result {
        Ok(code) => code,
        Err(error) => {
            eprintln!("cannot start Windows batch program: {error}");
            1
        }
    };
    std::process::exit(code);
}

#[cfg(windows)]
fn run() -> std::io::Result<i32> {
    use std::path::Path;
    use std::process::Command;

    let mut args = std::env::args_os().skip(2);
    let program = args.next().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "missing batch program")
    })?;
    let path = Path::new(&program);
    let extension = path.extension().and_then(|value| value.to_str()).unwrap_or("");
    if !path.is_absolute()
        || !(extension.eq_ignore_ascii_case("cmd") || extension.eq_ignore_ascii_case("bat"))
    {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "expected an absolute .cmd or .bat path",
        ));
    }
    // Inherit cwd, environment and all three streams. Do not concatenate a shell
    // command or interpret stdin: it belongs to the tool/MCP protocol.
    let status = Command::new(program).args(args).status()?;
    Ok(status.code().unwrap_or(1))
}
