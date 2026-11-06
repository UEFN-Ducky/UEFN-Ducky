//! Runs `UEFN-Ducky.exe --backend [argv]` and keeps it alive.
//!
//! Start: spawn, wait for hello (15 s, else exit 23; a protocol mismatch is 21; spawn failure
//! is 24). A backend that dies (non-zero exit, no `quit` event) is restarted up to 2 times in
//! 60 s; the third crash records a failure in `launcher/crash_loop.json` and gives 22.
//! Stdin is closed on shutdown, which is the backend's signal to quit.

pub mod policy;
pub mod protocol;

use policy::{record_crash_loop_failure, Decision, RestartPolicy};
use protocol::{encode_bye, encode_call, parse_line, BackendMsg, Hello, MAX_LINE};
use serde_json::Value;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

/// Launcher exit codes from the contract.
pub mod exit {
    pub const OK: i32 = 0;
    pub const WEBVIEW2_MISSING: i32 = 20;
    pub const PROTOCOL_MISMATCH: i32 = 21;
    pub const CRASH_LOOP: i32 = 22;
    pub const NO_HELLO: i32 = 23;
    pub const START_ERROR: i32 = 24;
}

pub const HELLO_TIMEOUT: Duration = Duration::from_secs(15);
/// `headless.EXIT_INSTANCE_BUSY`: a living Ducky did not take this launch. Not a crash.
pub const BACKEND_EXIT_BUSY: i32 = 3;
const SHUTDOWN_GRACE: Duration = Duration::from_secs(5);

#[derive(Debug, Clone)]
pub struct Config {
    pub exe: PathBuf,
    /// Full argument list (use [`Config::ducky`] for `--backend` + passthrough argv).
    pub args: Vec<String>,
    pub env: Vec<(String, String)>,
    pub hello_timeout: Duration,
    /// `%LOCALAPPDATA%/UEFN-Ducky`; `crash_loop.json` goes in its `launcher/` folder.
    pub app_data: PathBuf,
}

impl Config {
    /// `exe --backend [argv]`.
    pub fn ducky(exe: impl Into<PathBuf>, argv: &[String], app_data: impl Into<PathBuf>) -> Config {
        let mut args = vec!["--backend".to_string()];
        args.extend(argv.iter().cloned());
        Config { exe: exe.into(), args, env: Vec::new(), hello_timeout: HELLO_TIMEOUT, app_data: app_data.into() }
    }
}

/// Why a start failed: the launcher exit code plus a short reason.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartError {
    pub code: i32,
    pub reason: String,
}

impl StartError {
    fn new(code: i32, reason: impl Into<String>) -> Self {
        StartError { code, reason: reason.into() }
    }
}

/// What the stdout reader thread sends.
#[derive(Debug)]
enum Line {
    Msg(BackendMsg),
    Eof,
}

enum SpawnFailure {
    /// The process ended before hello: counts as a crash.
    Died(String),
    Fatal(StartError),
}

fn read_lines(stdout: impl Read + Send + 'static, tx: mpsc::Sender<Line>) {
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut buf = Vec::new();
        loop {
            buf.clear();
            match reader.by_ref().take(MAX_LINE as u64 + 1).read_until(b'\n', &mut buf) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            if buf.len() > MAX_LINE && buf.last() != Some(&b'\n') {
                // Over 1 MB: drop the rest of this line.
                let mut sink = Vec::new();
                if reader.read_until(b'\n', &mut sink).unwrap_or(0) == 0 {
                    break;
                }
                continue;
            }
            if let Some(msg) = std::str::from_utf8(&buf).ok().and_then(parse_line) {
                if tx.send(Line::Msg(msg)).is_err() {
                    return;
                }
            }
        }
        let _ = tx.send(Line::Eof);
    });
}

fn wait_timeout(child: &mut Child, limit: Duration) -> Option<ExitStatus> {
    let deadline = Instant::now() + limit;
    loop {
        if let Ok(Some(st)) = child.try_wait() {
            return Some(st);
        }
        if Instant::now() >= deadline {
            return None;
        }
        thread::sleep(Duration::from_millis(15));
    }
}

fn kill(child: &mut Child) -> Option<ExitStatus> {
    let _ = child.kill();
    child.wait().ok()
}

fn describe(st: Option<ExitStatus>) -> String {
    describe_code(st.and_then(|s| s.code()))
}

fn describe_code(code: Option<i32>) -> String {
    match code {
        Some(c) => format!("exit {c}"),
        None => "exit unknown".to_string(),
    }
}

/// A backend that ended before hello: BUSY is a start error (24, Python falls back), any
/// other end counts as a crash.
fn before_hello_failure(code: Option<i32>) -> SpawnFailure {
    if code == Some(BACKEND_EXIT_BUSY) {
        return SpawnFailure::Fatal(StartError::new(exit::START_ERROR, "backend busy: a running Ducky did not take this launch"));
    }
    SpawnFailure::Died(format!("backend ended before hello ({})", describe_code(code)))
}

/// One running backend process that said hello.
pub struct Backend {
    child: Child,
    stdin: Option<ChildStdin>,
    rx: Receiver<Line>,
    pub hello: Hello,
}

impl Backend {
    fn spawn(cfg: &Config) -> Result<Backend, SpawnFailure> {
        let mut cmd = Command::new(&cfg.exe);
        cmd.args(&cfg.args).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        for (k, v) in &cfg.env {
            cmd.env(k, v);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        let mut child = cmd.spawn().map_err(|e| {
            SpawnFailure::Fatal(StartError::new(exit::START_ERROR, format!("spawn {}: {e}", cfg.exe.display())))
        })?;
        let stdin = child.stdin.take();
        let (tx, rx) = mpsc::channel();
        read_lines(child.stdout.take().expect("piped stdout"), tx);
        let deadline = Instant::now() + cfg.hello_timeout;
        loop {
            let left = deadline.saturating_duration_since(Instant::now());
            match rx.recv_timeout(left) {
                Ok(Line::Msg(BackendMsg::Hello(hello))) => {
                    if !hello.protocol_ok() {
                        drop(stdin);
                        kill(&mut child);
                        return Err(SpawnFailure::Fatal(StartError::new(
                            exit::PROTOCOL_MISMATCH,
                            format!("backend protocol {} not in [1,1]", hello.protocol),
                        )));
                    }
                    return Ok(Backend { child, stdin, rx, hello });
                }
                Ok(Line::Msg(_)) => continue,
                Ok(Line::Eof) | Err(RecvTimeoutError::Disconnected) => {
                    drop(stdin);
                    let st = wait_timeout(&mut child, Duration::from_secs(2)).or_else(|| kill(&mut child));
                    return Err(before_hello_failure(st.and_then(|s| s.code())));
                }
                Err(RecvTimeoutError::Timeout) => {
                    drop(stdin);
                    kill(&mut child);
                    return Err(SpawnFailure::Fatal(StartError::new(
                        exit::NO_HELLO,
                        format!("no hello within {} s", cfg.hello_timeout.as_secs_f32()),
                    )));
                }
            }
        }
    }

    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    fn send(&mut self, line: &str) -> io::Result<()> {
        match self.stdin.as_mut() {
            Some(w) => {
                w.write_all(line.as_bytes())?;
                w.flush()
            }
            None => Err(io::Error::new(io::ErrorKind::BrokenPipe, "stdin closed")),
        }
    }

    /// Send bye, close stdin, wait up to the grace period, then kill.
    fn shutdown(&mut self, reason: &str) -> Option<ExitStatus> {
        let _ = self.send(&encode_bye(reason));
        self.stdin = None;
        wait_timeout(&mut self.child, SHUTDOWN_GRACE).or_else(|| kill(&mut self.child))
    }
}

/// What [`Supervisor::next`] saw.
#[derive(Debug, Clone, PartialEq)]
pub enum Step {
    /// A result or event (or other object) from the backend.
    Msg(BackendMsg),
    /// Nothing within the timeout.
    Idle,
    /// The backend crashed and a new one said hello (re-point the proxy at its origin).
    Restarted(Hello),
    /// The backend ended on purpose (`quit` event or exit 0): the launcher exits with 0.
    Exited,
    /// Gave up: exit with `code` (22 after a crash loop).
    Fatal(StartError),
}

pub struct Supervisor {
    cfg: Config,
    policy: RestartPolicy,
    backend: Option<Backend>,
    quitting: bool,
}

impl Supervisor {
    /// Spawn and wait for hello, restarting a backend that dies before hello.
    pub fn start(cfg: Config) -> Result<Supervisor, StartError> {
        let mut sup = Supervisor { cfg, policy: RestartPolicy::new(), backend: None, quitting: false };
        sup.spawn_until_hello()?;
        Ok(sup)
    }

    fn spawn_until_hello(&mut self) -> Result<&Hello, StartError> {
        loop {
            match Backend::spawn(&self.cfg) {
                Ok(b) => {
                    self.backend = Some(b);
                    return Ok(&self.backend.as_ref().unwrap().hello);
                }
                Err(SpawnFailure::Fatal(e)) => return Err(e),
                Err(SpawnFailure::Died(reason)) => self.on_crash(reason)?,
            }
        }
    }

    fn on_crash(&mut self, reason: String) -> Result<(), StartError> {
        match self.policy.on_crash(Instant::now()) {
            Decision::Restart => Ok(()),
            Decision::GiveUp => {
                record_crash_loop_failure(&self.cfg.app_data);
                Err(StartError::new(exit::CRASH_LOOP, format!("backend crashed 3 times in 60 s ({reason})")))
            }
        }
    }

    pub fn hello(&self) -> Option<&Hello> {
        self.backend.as_ref().map(|b| &b.hello)
    }

    pub fn backend_pid(&self) -> Option<u32> {
        self.backend.as_ref().map(Backend::pid)
    }

    /// `{"t":"call",...}` to the backend.
    pub fn call(&mut self, id: &str, name: &str, args: &[Value]) -> io::Result<()> {
        match self.backend.as_mut() {
            Some(b) => b.send(&encode_call(id, name, args)),
            None => Err(io::Error::new(io::ErrorKind::NotConnected, "no backend")),
        }
    }

    /// Next backend line, or a restart / exit when the process ended.
    pub fn next(&mut self, timeout: Duration) -> Step {
        let line = match self.backend.as_ref() {
            Some(b) => b.rx.recv_timeout(timeout),
            None => return Step::Exited,
        };
        match line {
            Ok(Line::Msg(msg)) => {
                if matches!(&msg, BackendMsg::Event { name, .. } if name == "quit") {
                    self.quitting = true;
                }
                Step::Msg(msg)
            }
            Err(RecvTimeoutError::Timeout) => Step::Idle,
            Ok(Line::Eof) | Err(RecvTimeoutError::Disconnected) => {
                let mut b = self.backend.take().unwrap();
                b.stdin = None;
                let st = wait_timeout(&mut b.child, Duration::from_secs(2)).or_else(|| kill(&mut b.child));
                if self.quitting || st.map(|s| s.success()).unwrap_or(false) {
                    return Step::Exited;
                }
                if let Err(e) = self.on_crash(describe(st)) {
                    return Step::Fatal(e);
                }
                match self.spawn_until_hello() {
                    Ok(h) => Step::Restarted(h.clone()),
                    Err(e) => Step::Fatal(e),
                }
            }
        }
    }

    /// Bye + close stdin + wait (kill after 5 s).
    pub fn shutdown(&mut self, reason: &str) -> Option<ExitStatus> {
        self.backend.take().and_then(|mut b| b.shutdown(reason))
    }
}

impl Drop for Supervisor {
    fn drop(&mut self) {
        self.shutdown("launcher exit");
    }
}

#[cfg(test)]
mod busy_tests {
    use super::*;

    #[test]
    fn busy_before_hello_is_start_error_not_crash() {
        match before_hello_failure(Some(BACKEND_EXIT_BUSY)) {
            SpawnFailure::Fatal(e) => assert_eq!(e.code, exit::START_ERROR),
            SpawnFailure::Died(_) => panic!("busy must not count as a crash"),
        }
        assert!(matches!(before_hello_failure(Some(1)), SpawnFailure::Died(_)));
        assert!(matches!(before_hello_failure(None), SpawnFailure::Died(_)));
    }

    #[test]
    fn hello_handoff_flag() {
        let h = |line: &str| match parse_line(line) {
            Some(BackendMsg::Hello(h)) => h,
            other => panic!("{other:?}"),
        };
        assert!(h(r#"{"t":"hello","protocol":1,"port":4199,"pid":9,"handoff":true}"#).handoff);
        assert!(!h(r#"{"t":"hello","protocol":1,"port":4199}"#).handoff);
    }
}
