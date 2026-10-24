//! One owned ConPTY session. A bounded JSON startup frame precedes raw terminal I/O.
//! The caller owns this helper in a Windows Job; no TCP listener or shell interpolation.

use std::collections::HashMap;
#[cfg(any(windows, test))]
use std::io::Write;
use std::io::{self, BufRead, Read};
use std::path::PathBuf;

use serde::Deserialize;
#[cfg(any(windows, test))]
use serde_json::json;

pub const ARG: &str = "--corki-terminal-process";
const START_LIMIT: u64 = 8_000_000;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Start {
    command: Vec<String>,
    cwd: PathBuf,
    env: HashMap<String, String>,
}

fn read_start(reader: &mut impl BufRead) -> Result<Start, String> {
    let mut frame = Vec::new();
    reader
        .take(START_LIMIT + 1)
        .read_until(b'\n', &mut frame)
        .map_err(|error| error.to_string())?;
    if frame.len() as u64 > START_LIMIT || frame.last() != Some(&b'\n') {
        return Err("invalid terminal startup frame".into());
    }
    let start: Start = serde_json::from_slice(&frame).map_err(|error| error.to_string())?;
    if start.command.first().is_none_or(String::is_empty) || !start.cwd.is_absolute() {
        return Err("terminal requires a program and absolute working directory".into());
    }
    if start.command.iter().any(|arg| arg.contains('\0'))
        || start
            .env
            .iter()
            .any(|(key, value)| key.is_empty() || key.contains(['\0', '=']) || value.contains('\0'))
    {
        return Err("invalid terminal arguments or environment".into());
    }
    Ok(start)
}

#[cfg(windows)]
fn report(result: serde_json::Value) -> io::Result<()> {
    let mut output = io::stdout().lock();
    writeln!(output, "{result}")?;
    output.flush()
}

// Once the parent disconnects, keep draining ConPTY while termination completes.
// Returning early would drop the console on this thread and can deadlock with
// its blocked output reader. Preserve the first error for the eventual status.
#[cfg(any(windows, test))]
fn forward_output(sink: &mut impl Write, failure: &mut Option<String>, bytes: &[u8]) {
    if failure.is_none() {
        if let Err(error) = sink.write_all(bytes).and_then(|_| sink.flush()) {
            *failure = Some(error.to_string());
        }
    }
}

#[cfg(windows)]
async fn run(start: Start, mut input: io::BufReader<io::Stdin>) -> Result<i32, String> {
    use codex_utils_pty::{TerminalSize, spawn_pty_process};
    let spawned = if let Some(sandboxed) =
        crate::windows_sandbox::spawn_if_wrapped(&start.command, &start.cwd, &start.env).await?
    {
        sandboxed
    } else {
        spawn_pty_process(
            &start.command[0],
            &start.command[1..],
            &start.cwd,
            &start.env,
            &None,
            TerminalSize::default(),
            &[],
        )
        .await
        .map_err(|error| error.to_string())?
    };
    // No user output is forwarded before the caller consumes this acknowledgment.
    let mut output_failure = report(json!({"ready": true, "version": 1}))
        .err()
        .map(|error| error.to_string());
    let mut output = spawned.stdout_rx;
    let mut errors = spawned.stderr_rx;
    let mut exit = spawned.exit_rx;
    let mut session = Some(spawned.session);
    if output_failure.is_some() {
        session.as_ref().unwrap().request_terminate();
    }
    let writer = session.as_ref().unwrap().writer_sender();
    let (input_closed_tx, mut input_closed) = tokio::sync::oneshot::channel();
    // A detached OS reader does not hold Tokio runtime shutdown hostage when the
    // parent keeps stdin open. Process exit closes it; buffers remain bounded.
    std::thread::spawn(move || {
        let mut buffer = [0; 8192];
        loop {
            match input.read(&mut buffer) {
                Ok(0) => break,
                Ok(size) => {
                    if writer.blocking_send(buffer[..size].to_vec()).is_err() {
                        break;
                    }
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
        let _ = input_closed_tx.send(());
    });
    let mut code = None;
    let mut output_open = true;
    let mut errors_open = true;
    let mut input_open = true;
    let mut closing = None;
    while output_open || errors_open || code.is_none() {
        tokio::select! {
            status = &mut exit, if code.is_none() => {
                code = Some(status.unwrap_or(1));
                // Closing ConPTY can block until its remaining output is read.
                // Do it off-thread while continuing to drain the output channel.
                let owned = session.take();
                closing = Some(tokio::task::spawn_blocking(move || drop(owned)));
            },
            bytes = output.recv(), if output_open => match bytes {
                Some(bytes) => {
                    forward_output(&mut io::stdout(), &mut output_failure, &bytes);
                    if output_failure.is_some() {
                        if let Some(owned) = &session { owned.request_terminate(); }
                    }
                },
                None => output_open = false,
            },
            bytes = errors.recv(), if errors_open => match bytes {
                Some(bytes) => {
                    forward_output(&mut io::stdout(), &mut output_failure, &bytes);
                    if output_failure.is_some() {
                        if let Some(owned) = &session { owned.request_terminate(); }
                    }
                },
                None => errors_open = false,
            },
            _ = &mut input_closed, if input_open && code.is_none() => {
                input_open = false;
                if let Some(owned) = &session { owned.request_terminate(); }
            },
        }
    }
    if let Some(closing) = closing {
        closing.await.map_err(|e| e.to_string())?;
    }
    match output_failure {
        Some(error) => Err(error),
        None => Ok(code.unwrap_or(1)),
    }
}

pub fn main() {
    let mut input = io::BufReader::new(io::stdin());
    let start = read_start(&mut input);
    #[cfg(windows)]
    let result = start.and_then(|start| {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .map_err(|error| error.to_string())?;
        let result = runtime.block_on(run(start, input));
        runtime.shutdown_timeout(std::time::Duration::from_secs(1));
        result
    });
    #[cfg(not(windows))]
    let result: Result<i32, String> = start.and_then(|_| Err("ConPTY requires Windows".into()));
    let code = match result {
        Ok(code) => code,
        Err(error) => {
            // Before readiness this is a startup failure; after readiness stderr
            // is diagnostic only and must not be injected into terminal output.
            eprintln!("{error}");
            1
        }
    };
    std::process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disconnected_output_is_drained_without_retrying_the_failed_sink() {
        struct BrokenSink(usize);
        impl Write for BrokenSink {
            fn write(&mut self, _: &[u8]) -> io::Result<usize> {
                self.0 += 1;
                Err(io::Error::new(
                    io::ErrorKind::BrokenPipe,
                    "parent disconnected",
                ))
            }
            fn flush(&mut self) -> io::Result<()> {
                Ok(())
            }
        }
        let mut sink = BrokenSink(0);
        let mut failure = None;
        for chunk in [b"first".as_slice(), b"remaining output", b"last"] {
            forward_output(&mut sink, &mut failure, chunk);
        }
        assert_eq!(sink.0, 1);
        assert_eq!(failure.as_deref(), Some("parent disconnected"));
    }

    #[test]
    fn startup_preserves_following_raw_input() {
        let cwd = std::env::current_dir().unwrap();
        let mut bytes =
            serde_json::to_vec(&json!({"command": ["program", "a b"], "cwd": cwd, "env": {}}))
                .unwrap();
        bytes.extend_from_slice(b"\nhello\n\x03");
        let mut reader = io::Cursor::new(bytes);
        let start = read_start(&mut reader).unwrap();
        assert_eq!(start.command[1], "a b");
        let mut tail = Vec::new();
        reader.read_to_end(&mut tail).unwrap();
        assert_eq!(tail, b"hello\n\x03");
    }

    #[test]
    fn startup_rejects_unterminated_oversize_and_relative_paths() {
        for bytes in [
            vec![b' '; START_LIMIT as usize + 1],
            b"{}".to_vec(),
            b"{\"command\":[\"program\"],\"cwd\":\"relative\",\"env\":{}}\n".to_vec(),
        ] {
            assert!(read_start(&mut io::Cursor::new(bytes)).is_err());
        }
    }
}
