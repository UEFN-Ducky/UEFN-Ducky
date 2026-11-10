//! Runs `UEFN-Ducky.exe --backend [argv]` and keeps it alive.
//!
//! Start: spawn, wait for hello (20 s of silence or 180 s in all, else exit 23; a protocol mismatch is 21; spawn failure
//! is 24). Any backend line, stdout or stderr, breaks the silence; the last stderr line
//! (user paths as `~`, 300 chars) goes into every failure reason, so a report says why it died. A backend that dies (non-zero exit, no `quit` event) is restarted up to 2 times in
//! 60 s; the third crash records a failure in `launcher/crash_loop.json` and gives 22.
//! Stdin is closed on shutdown, which is the backend's signal to quit.

pub mod policy;
pub mod protocol;

use policy::{hello_deadline, record_crash_loop_failure, Decision, RestartPolicy};
use protocol::{encode_bye, encode_call, parse_line, BackendMsg, Hello, MAX_LINE};
use serde_json::Value;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::Mutex;
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

/// Hello wait: gives up after this long with no backend line (any line resets it)...
pub const HELLO_SILENCE: Duration = Duration::from_secs(20);
/// ...or this long after the spawn, whatever the backend says.
pub const HELLO_CAP: Duration = Duration::from_secs(180);
/// `headless.EXIT_INSTANCE_BUSY`: a living Ducky did not take this launch. Not a crash.
pub const BACKEND_EXIT_BUSY: i32 = 3;
const SHUTDOWN_GRACE: Duration = Duration::from_secs(5);

#[derive(Debug, Clone)]
pub struct Config {
    pub exe: PathBuf,
    /// Full argument list (use [`Config::ducky`] for `--backend` + passthrough argv).
    pub args: Vec<String>,
    pub env: Vec<(String, String)>,
    pub hello_silence: Duration,
    pub hello_cap: Duration,
    /// `%LOCALAPPDATA%/UEFN-Ducky`; `crash_loop.json` goes in its `launcher/` folder.
    pub app_data: PathBuf,
}

impl Config {
    /// `exe --backend [argv]`.
    pub fn ducky(exe: impl Into<PathBuf>, argv: &[String], app_data: impl Into<PathBuf>) -> Config {
        let mut args = vec!["--backend".to_string()];
        args.extend(argv.iter().cloned());
        Config { exe: exe.into(), args, env: Vec::new(), hello_silence: HELLO_SILENCE, hello_cap: HELLO_CAP, app_data: app_data.into() }
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
    /// One non-empty stderr line, already [`scrub`]bed.
    Err(String),
    Eof,
}

/// The backend still starting: its last stage and when it was spawned (None once it said
/// hello or failed). ponytail: one global, the launcher runs one supervisor.
static STARTING: Mutex<Option<(Option<String>, Instant)>> = Mutex::new(None);

fn set_starting(v: Option<(Option<String>, Instant)>) {
    if let Ok(mut g) = STARTING.lock() {
        *g = v;
    }
}

/// The loading screen's line while the backend starts: `Starting: panel_api_init (24 s)`.
pub fn starting_status() -> Option<String> {
    let g = STARTING.lock().ok()?;
    let (stage, at) = g.as_ref()?;
    status_text(stage.as_deref(), at.elapsed().as_secs())
}

/// ponytail: quiet for the first 3 s, so a normal start never flashes it.
pub fn status_text(stage: Option<&str>, secs: u64) -> Option<String> {
    (secs >= 3).then(|| format!("Starting: {} ({secs} s)", stage.unwrap_or("backend")))
}

/// Longest stderr line kept for a report.
pub const STDERR_KEEP: usize = 300;

/// The fixed list of crash slugs (`native_switch.CRASH_SLUGS`).
pub const CRASH_SLUGS: [&str; 5] = ["port_blocked", "import_error", "permission", "memory", "other"];

/// Why a backend died, from its error text: one of [`CRASH_SLUGS`]. Port first: a 10013
/// bind is a `PermissionError` too.
pub fn crash_slug(text: &str) -> &'static str {
    let t = text.to_ascii_lowercase();
    let has = |words: &[&str]| words.iter().any(|w| t.contains(w));
    if has(&["10013", "10048", "address already in use", "eaddrinuse", "only one usage of each socket"]) {
        "port_blocked"
    } else if has(&["modulenotfounderror", "importerror", "dll load failed"]) {
        "import_error"
    } else if has(&["permissionerror", "access is denied", "winerror 5]", "errno 13"]) {
        "permission"
    } else if has(&["memoryerror", "out of memory", "winerror 8]", "winerror 1455"]) {
        "memory"
    } else {
        "other"
    }
}

/// A stderr line safe for a report: `C:\Users\<name>` / `/Users/<name>` become `~`,
/// at most [`STDERR_KEEP`] chars.
pub fn scrub(line: &str) -> String {
    let lower = line.to_ascii_lowercase();
    let bytes = lower.as_bytes();
    let mut out = String::new();
    let mut i = 0;
    while let Some((at, len)) = ["\\users\\", "/users/"].iter().filter_map(|p| lower[i..].find(p).map(|k| (i + k, p.len()))).min() {
        let start = if at >= i + 2 && bytes[at - 1] == b':' && bytes[at - 2].is_ascii_alphabetic() { at - 2 } else { at };
        let end = lower[at + len..].find(['\\', '/']).map_or(lower.len(), |e| at + len + e);
        out.push_str(&line[i..start]);
        out.push('~');
        i = end;
    }
    out.push_str(&line[i..]);
    out.trim().chars().take(STDERR_KEEP).collect()
}

fn read_stderr(stderr: impl Read + Send + 'static, tx: mpsc::Sender<Line>) {
    thread::spawn(move || {
        for line in BufReader::new(stderr).split(b'\n').map_while(Result::ok) {
            let line = scrub(&String::from_utf8_lossy(&line));
            if !line.is_empty() && tx.send(Line::Err(line)).is_err() {
                return;
            }
        }
    });
}

/// After stdout ended: take the stderr lines still on their way (short wait each).
fn drain_stderr(rx: &Receiver<Line>, last_err: &mut String) {
    while let Ok(line) = rx.recv_timeout(Duration::from_millis(200)) {
        if let Line::Err(s) = line {
            *last_err = s;
        }
    }
}

fn with_stderr(reason: String, last_err: &str) -> String {
    if last_err.is_empty() {
        reason
    } else {
        format!("{reason}: {last_err}")
    }
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

fn died_with(failure: SpawnFailure, last_err: &str) -> SpawnFailure {
    match failure {
        SpawnFailure::Died(r) => SpawnFailure::Died(with_stderr(r, last_err)),
        SpawnFailure::Fatal(e) => SpawnFailure::Fatal(StartError::new(e.code, with_stderr(e.reason, last_err))),
    }
}

/// One running backend process that said hello.
pub struct Backend {
    child: Child,
    stdin: Option<ChildStdin>,
    rx: Receiver<Line>,
    /// The backend's last stderr line (scrubbed).
    last_err: String,
    pub hello: Hello,
}

impl Backend {
    fn spawn(cfg: &Config) -> Result<Backend, SpawnFailure> {
        let mut cmd = Command::new(&cfg.exe);
        cmd.args(&cfg.args).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
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
        read_stderr(child.stderr.take().expect("piped stderr"), tx.clone());
        read_lines(child.stdout.take().expect("piped stdout"), tx);
        let spawned = Instant::now();
        let mut last_line = spawned;
        let mut stage: Option<String> = None;
        let mut last_err = String::new();
        set_starting(Some((None, spawned)));
        loop {
            let deadline = hello_deadline(spawned, last_line, cfg.hello_silence, cfg.hello_cap);
            let left = deadline.saturating_duration_since(Instant::now());
            let got = rx.recv_timeout(left);
            last_line = Instant::now();
            match got {
                Ok(Line::Msg(BackendMsg::Hello(hello))) => {
                    if !hello.protocol_ok() {
                        drop(stdin);
                        kill(&mut child);
                        return Err(SpawnFailure::Fatal(StartError::new(
                            exit::PROTOCOL_MISMATCH,
                            format!("backend protocol {} not in [1,1]", hello.protocol),
                        )));
                    }
                    return Ok(Backend { child, stdin, rx, last_err, hello });
                }
                Ok(Line::Msg(BackendMsg::Event { name, data })) if name == "stage" => {
                    stage = data["name"].as_str().map(String::from).or(stage);
                    set_starting(Some((stage.clone(), spawned)));
                }
                Ok(Line::Msg(_)) => continue,
                Ok(Line::Err(s)) => last_err = s,
                Ok(Line::Eof) | Err(RecvTimeoutError::Disconnected) => {
                    drop(stdin);
                    let st = wait_timeout(&mut child, Duration::from_secs(2)).or_else(|| kill(&mut child));
                    drain_stderr(&rx, &mut last_err);
                    return Err(died_with(before_hello_failure(st.and_then(|s| s.code())), &last_err));
                }
                Err(RecvTimeoutError::Timeout) => {
                    drop(stdin);
                    kill(&mut child);
                    let reason = format!(
                        "no hello, last stage {} after {} s",
                        stage.as_deref().unwrap_or("none"),
                        spawned.elapsed().as_secs()
                    );
                    return Err(SpawnFailure::Fatal(StartError::new(exit::NO_HELLO, with_stderr(reason, &last_err))));
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
            let spawned = Backend::spawn(&self.cfg);
            set_starting(None);
            match spawned {
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
                record_crash_loop_failure(&self.cfg.app_data, crash_slug(&reason));
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
        let deadline = Instant::now() + timeout;
        let line = loop {
            let Some(b) = self.backend.as_mut() else { return Step::Exited };
            match b.rx.recv_timeout(deadline.saturating_duration_since(Instant::now())) {
                Ok(Line::Err(s)) => b.last_err = s,
                other => break other,
            }
        };
        match line {
            Ok(Line::Err(_)) => Step::Idle,
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
                drain_stderr(&b.rx, &mut b.last_err);
                if let Err(e) = self.on_crash(with_stderr(describe(st), &b.last_err)) {
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

    #[test]
    fn status_line_names_the_stage_after_3_s() {
        assert_eq!(status_text(Some("panel_api_init"), 24).as_deref(), Some("Starting: panel_api_init (24 s)"));
        assert_eq!(status_text(None, 5).as_deref(), Some("Starting: backend (5 s)"));
        assert_eq!(status_text(Some("x"), 2), None);
    }

    #[test]
    fn slugs_and_scrub() {
        assert_eq!(crash_slug("PermissionError: [WinError 10013] An attempt was made"), "port_blocked");
        assert_eq!(crash_slug("ModuleNotFoundError: No module named 'x'"), "import_error");
        assert_eq!(crash_slug("PermissionError: [WinError 5] Access is denied"), "permission");
        assert_eq!(crash_slug("MemoryError"), "memory");
        assert_eq!(crash_slug("exit 1"), "other");
        assert_eq!(scrub(r"  File C:\Users\Bob Smith\AppData\x.py, /Users/al/y  "), r"File ~\AppData\x.py, ~/y");
        assert_eq!(scrub(&"é".repeat(400)).chars().count(), STDERR_KEEP);
        assert_eq!(scrub("no path"), "no path");
    }
}
