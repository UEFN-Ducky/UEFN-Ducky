//! Linux and macOS launch: one launcher per user, through a Unix socket in `$XDG_RUNTIME_DIR`. A second
//! launch sends `focus` and its argv there and exits; the first starts the Python app the way
//! `fallback()` does (`repoint::Spawn::python`) and stays to answer later launches until the
//! app exits. A later launch's argv goes on to the living panel over the same loopback
//! hand-off Windows uses (`ducky_instance::decide_launch`), which also raises its window.

use std::ffi::{OsStr, OsString};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{json, Value};

use crate::{exit, repoint};

pub const SOCKET_DIR: &str = "uefn-ducky";
pub const SOCKET_NAME: &str = "launcher.sock";
/// How long either side waits on the other's line.
pub const SOCKET_TIMEOUT: Duration = Duration::from_secs(2);
const REPLY_OK: &str = "ok";
/// The Python app writes `launcher/ready-<this pid>.json` (`start::write_ready`'s file, the
/// one the Windows launcher writes on first paint) once its window has painted: the splash
/// closes then.
pub const SPLASH_PID_ENV: &str = "UEFN_DUCKY_SPLASH_PID";
/// A panel that never says ready still loses the splash after this.
pub const SPLASH_CAP: Duration = Duration::from_secs(30);

/// `$XDG_RUNTIME_DIR/uefn-ducky/launcher.sock`; without one (a bare ssh session),
/// `/tmp/uefn-ducky-<uid>/launcher.sock`.
pub fn socket_path(runtime_dir: Option<&OsStr>, uid: u32) -> PathBuf {
    match runtime_dir.filter(|d| Path::new(d).is_absolute()) {
        Some(d) => Path::new(d).join(SOCKET_DIR).join(SOCKET_NAME),
        None => std::env::temp_dir().join(format!("{SOCKET_DIR}-{uid}")).join(SOCKET_NAME),
    }
}

fn uid() -> u32 {
    // ponytail: libc's getuid without the crate; uid_t is u32 on Linux and macOS.
    extern "C" {
        fn getuid() -> u32;
    }
    unsafe { getuid() }
}

/// The one line a second launch sends.
pub fn focus_line(args: &[OsString]) -> String {
    let args: Vec<String> = args.iter().map(|a| a.to_string_lossy().into_owned()).collect();
    format!("{}\n", json!({"op": "focus", "args": args}))
}

/// The argv a `focus` line carries; None for anything else.
pub fn parse_focus(line: &str) -> Option<Vec<OsString>> {
    let v: Value = serde_json::from_str(line.trim()).ok()?;
    if v.get("op")?.as_str()? != "focus" {
        return None;
    }
    Some(v.get("args")?.as_array()?.iter().filter_map(Value::as_str).map(OsString::from).collect())
}

/// Hand `args` to the launcher on `path`; true when it answered.
pub fn send(path: &Path, args: &[OsString]) -> bool {
    let talk = || -> std::io::Result<bool> {
        let mut s = UnixStream::connect(path)?;
        s.set_read_timeout(Some(SOCKET_TIMEOUT))?;
        s.set_write_timeout(Some(SOCKET_TIMEOUT))?;
        s.write_all(focus_line(args).as_bytes())?;
        let mut reply = String::new();
        BufReader::new(s).read_line(&mut reply)?;
        Ok(reply.trim() == REPLY_OK)
    };
    talk().unwrap_or(false)
}

pub enum Claim {
    /// This launch is the one: it serves the socket.
    Primary(UnixListener),
    /// A living launcher took this launch.
    HandedOff,
}

/// Become the launcher on `path`, or hand `args` to the one already there. A socket file
/// nobody answers on (a launcher that died) is removed and taken over.
pub fn claim(path: &Path, args: &[OsString]) -> std::io::Result<Claim> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))?;
    }
    for _ in 0..3 {
        if send(path, args) {
            return Ok(Claim::HandedOff);
        }
        match UnixListener::bind(path) {
            Ok(l) => return Ok(Claim::Primary(l)),
            // Stale, or a launch racing this one bound it first: the next send tells which.
            Err(e) if e.kind() == std::io::ErrorKind::AddrInUse => {
                if send(path, args) {
                    return Ok(Claim::HandedOff);
                }
                let _ = std::fs::remove_file(path);
            }
            Err(e) => return Err(e),
        }
    }
    Err(std::io::Error::other("launcher socket stays busy"))
}

/// Answer each later launch (`ok` first, so it exits at once), then give its argv to `on_focus`.
pub fn serve(listener: UnixListener, mut on_focus: impl FnMut(Vec<OsString>) + Send + 'static) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let _ = stream.set_read_timeout(Some(SOCKET_TIMEOUT));
            let mut line = String::new();
            let Ok(mut w) = stream.try_clone() else { continue };
            if BufReader::new(stream).read_line(&mut line).is_err() {
                continue;
            }
            let Some(args) = parse_focus(&line) else { continue };
            let _ = w.write_all(format!("{REPLY_OK}\n").as_bytes());
            drop(w);
            on_focus(args);
        }
    })
}

/// Removes the socket file when the launcher ends.
struct SocketFile(PathBuf);

impl Drop for SocketFile {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

/// A later launch's argv to the living panel (raises its window). The panel not up yet (its
/// `panel.pid` missing): the launch is dropped.
// ponytail: argv sent before the panel is up is lost; queue it until panel.pid if a
// double-click during start ever matters.
fn forward(args: &[OsString]) {
    if let Some(cfg) = ducky_instance::Config::from_env() {
        let _ = ducky_instance::decide_launch(args, &cfg, &ducky_instance::RealSystem);
    }
}

/// A Linux or macOS launch. Exit codes as on Windows ([`exit`]).
pub fn run(args: Vec<OsString>) -> u8 {
    let path = socket_path(std::env::var_os("XDG_RUNTIME_DIR").as_deref(), uid());
    let listener = match claim(&path, &args) {
        Ok(Claim::HandedOff) => return exit::OK,
        Ok(Claim::Primary(l)) => Some(l),
        // No socket (read-only runtime dir): start anyway; panel.pid below still guards.
        Err(_) => None,
    };
    let _socket = listener.map(|l| {
        serve(l, |a| forward(&a));
        SocketFile(path)
    });
    // A Ducky started without this launcher (the Python app directly) takes the launch.
    if let Some(cfg) = ducky_instance::Config::from_env() {
        if ducky_instance::decide_launch(&args, &cfg, &ducky_instance::RealSystem) != ducky_instance::Launch::StartFresh {
            return exit::OK;
        }
    }
    let exe = std::env::current_exe().unwrap_or_default();
    let mut cmd = repoint::Spawn::python(repoint::sibling_stub(&exe), &args, "linux_launcher").command();
    let me = std::process::id();
    cmd.env(SPLASH_PID_ENV, me.to_string());
    let Ok(mut child) = cmd.spawn() else {
        return exit::START_ERROR;
    };
    let app_data = ducky_instance::Config::from_env().map(|c| c.app_data).unwrap_or_default();
    let ready = crate::start::ready_path(&app_data, me);
    // ponytail: no splash on macOS; the Python window paints its own. Native one in l-native if slow.
    #[cfg(target_os = "linux")]
    {
        let mut exited = || matches!(child.try_wait(), Ok(Some(_)));
        splash::linux::show_until(splash::Splash::from_env(), || ready.exists() || exited(), SPLASH_CAP);
    }
    let waited = child.wait();
    let _ = std::fs::remove_file(&ready);
    match waited {
        Ok(_) => exit::OK,
        Err(_) => exit::START_ERROR,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;

    fn temp_sock(tag: &str) -> PathBuf {
        let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        std::env::temp_dir().join(format!("s02-{tag}-{}-{n}", std::process::id())).join(SOCKET_NAME)
    }

    #[test]
    fn socket_lives_in_the_runtime_dir() {
        assert_eq!(socket_path(Some(OsStr::new("/run/user/1000")), 1000), Path::new("/run/user/1000/uefn-ducky/launcher.sock"));
        assert_eq!(socket_path(None, 1000), std::env::temp_dir().join("uefn-ducky-1000").join(SOCKET_NAME));
        assert_eq!(socket_path(Some(OsStr::new("rel")), 7), std::env::temp_dir().join("uefn-ducky-7").join(SOCKET_NAME));
    }

    #[test]
    fn focus_line_round_trips() {
        let args = vec![OsString::from("uefn-ducky://store/x"), OsString::from("/home/a/b c.txt")];
        assert_eq!(parse_focus(&focus_line(&args)), Some(args));
        assert_eq!(parse_focus(r#"{"op":"quit"}"#), None);
        assert_eq!(parse_focus("junk"), None);
    }

    #[test]
    fn a_second_launch_hands_focus_and_argv_to_the_first() {
        let path = temp_sock("handshake");
        let Claim::Primary(l) = claim(&path, &[]).unwrap() else { panic!("first launch must own the socket") };
        assert_eq!(std::fs::metadata(path.parent().unwrap()).unwrap().permissions().mode() & 0o777, 0o700);
        let (tx, rx) = mpsc::channel();
        serve(l, move |a| tx.send(a).unwrap());
        let args = vec![OsString::from("--open"), OsString::from("/p")];
        assert!(matches!(claim(&path, &args).unwrap(), Claim::HandedOff));
        assert_eq!(rx.recv_timeout(Duration::from_secs(5)).unwrap(), args);
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn a_dead_launchers_socket_is_taken_over() {
        let path = temp_sock("stale");
        let Claim::Primary(l) = claim(&path, &[]).unwrap() else { panic!() };
        drop(l); // the file stays, nobody listens
        assert!(path.exists());
        assert!(matches!(claim(&path, &[]).unwrap(), Claim::Primary(_)));
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn the_socket_file_goes_with_the_launcher() {
        let path = temp_sock("cleanup");
        let Claim::Primary(_l) = claim(&path, &[]).unwrap() else { panic!() };
        drop(SocketFile(path.clone()));
        assert!(!path.exists());
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }
}
