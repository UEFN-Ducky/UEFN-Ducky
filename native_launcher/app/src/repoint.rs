//! Repoint: shortcuts start `<install root>/Ducky-Launcher.exe` directly, so the launcher
//! does what the `UEFN-Ducky.exe` stub did before showing a window:
//! - root mode: hop to the current version's own launcher (`versions/current.json`),
//!   or to the root stub when that file is missing or broken;
//! - the `native_launcher` setting: `off` (or `auto` during a crash-loop hold) runs the
//!   Python window instead;
//! - a start that fails with 20–24 opens the Python window, so the user still gets one.
//!
//! Every hop passes argv through unchanged, in order.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

use serde_json::Value;

pub const LAUNCHER_EXE: &str = "Ducky-Launcher.exe";
pub const STUB_EXE: &str = "UEFN-Ducky.exe";
/// Tells `UEFN-Ducky.exe` to use the Python window and never start a launcher.
pub const NO_NATIVE_ENV: &str = "UEFN_DUCKY_NO_NATIVE";
/// Set by `native_switch.run_native`: Python decided and handles any fallback itself.
pub const NATIVE_CHILD_ENV: &str = "UEFN_DUCKY_NATIVE_CHILD";
/// Set on the root hop so a version launcher never hops again (no exec loop).
pub const HOPPED_ENV: &str = "UEFN_DUCKY_LAUNCHER_HOPPED";
/// `PanelSettings.native_launcher`'s default in `frontend/settings.py`.
pub const DEFAULT_NATIVE_LAUNCHER: &str = "off";

/// What a launch started from `exe` does first.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Root {
    /// A version folder (or already hopped): start Ducky here.
    RunHere,
    /// The install root with a usable pointer: that version's launcher.
    Exec(PathBuf),
    /// The install root without a usable pointer: today's stub.
    Stub(PathBuf),
}

/// `<root>/versions/current.json` → that version's `Ducky-Launcher.exe`, when it exists.
/// `dir` is relative to the root (`versions/app-1.2.x`); an absolute one is used as is.
pub fn current_launcher(root: &Path) -> Option<PathBuf> {
    let text = std::fs::read_to_string(root.join("versions").join("current.json")).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    let dir = v.get("dir")?.as_str()?.trim();
    if dir.is_empty() {
        return None;
    }
    let exe = root.join(dir).join(LAUNCHER_EXE);
    exe.is_file().then_some(exe)
}

fn same_file(a: &Path, b: &Path) -> bool {
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(x), Ok(y)) => x == y,
        _ => a == b,
    }
}

/// Root mode is a launcher whose folder holds `versions/`.
pub fn root_decision(exe: &Path, hopped: bool) -> Root {
    let Some(root) = exe.parent() else {
        return Root::RunHere;
    };
    if hopped || !root.join("versions").is_dir() {
        return Root::RunHere;
    }
    match current_launcher(root) {
        Some(target) if !same_file(&target, exe) => Root::Exec(target),
        _ => Root::Stub(root.join(STUB_EXE)),
    }
}

/// `native_launcher` from `panel_settings.json` as off | on | auto
/// (`settings.normalize_native_launcher`; a missing file or key is the default).
pub fn native_setting(app_data: &Path) -> String {
    let raw = std::fs::read_to_string(app_data.join("panel_settings.json"))
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| v.get("native_launcher").cloned());
    let mode = match raw {
        None => DEFAULT_NATIVE_LAUNCHER.to_string(),
        Some(Value::String(s)) => s.trim().to_ascii_lowercase(),
        Some(_) => String::new(),
    };
    if matches!(mode.as_str(), "off" | "on" | "auto") {
        mode
    } else {
        "off".to_string()
    }
}

/// `native_switch.decide` without the EXE and WebView2 checks (the launcher is running,
/// and a missing WebView2 exits 20, which falls back below).
pub fn wants_native(setting: &str, crash_loop_active: bool) -> bool {
    match setting {
        "on" => true,
        "auto" => !crash_loop_active,
        _ => false,
    }
}

/// A started-directly launch that ended with one of these opens the Python window.
pub fn is_fallback_code(code: u8) -> bool {
    (20..=24).contains(&code)
}

/// One process start: the EXE, argv in order, extra env.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Spawn {
    pub exe: PathBuf,
    pub args: Vec<OsString>,
    pub env: Vec<(String, String)>,
}

impl Spawn {
    pub fn hop(exe: PathBuf, args: &[OsString]) -> Spawn {
        Spawn { exe, args: args.to_vec(), env: vec![(HOPPED_ENV.into(), "1".into())] }
    }

    pub fn python(exe: PathBuf, args: &[OsString]) -> Spawn {
        Spawn { exe, args: args.to_vec(), env: vec![(NO_NATIVE_ENV.into(), "1".into())] }
    }

    /// Start it and let it run on its own (this launcher exits right after).
    pub fn start(&self) -> std::io::Result<()> {
        let mut cmd = std::process::Command::new(&self.exe);
        cmd.args(&self.args)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        // Nobody waits on this child (this launcher exits and `run_native` stops at our exit
        // 0), so it is a direct start that must fall back on its own.
        cmd.env_remove(NATIVE_CHILD_ENV);
        for (k, v) in &self.env {
            cmd.env(k, v);
        }
        if let Some(dir) = self.exe.parent() {
            cmd.current_dir(dir);
        }
        cmd.spawn().map(drop)
    }
}

/// The stub beside this launcher (the same version folder).
pub fn sibling_stub(exe: &Path) -> PathBuf {
    exe.parent().map(|d| d.join(STUB_EXE)).unwrap_or_else(|| PathBuf::from(STUB_EXE))
}

/// Before anything else: the process to start instead of running Ducky here, if any.
/// `native_child` = started by `native_switch.run_native`, which already decided.
pub fn before_start(
    exe: &Path,
    args: &[OsString],
    hopped: bool,
    native_child: bool,
    setting: impl FnOnce() -> String,
    crash_loop_active: impl FnOnce() -> bool,
) -> Option<Spawn> {
    match root_decision(exe, hopped) {
        Root::Exec(target) => return Some(Spawn::hop(target, args)),
        // The stub picks the version and the window itself, as before Repoint.
        Root::Stub(stub) => return Some(Spawn { exe: stub, args: args.to_vec(), env: Vec::new() }),
        Root::RunHere => {}
    }
    if native_child {
        return None;
    }
    let mode = setting();
    // Only `auto` reads the crash-loop hold, as in `native_switch.decide`.
    if !wants_native(&mode, mode == "auto" && crash_loop_active()) {
        return Some(Spawn::python(sibling_stub(exe), args));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("ducky-repoint-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn touch(p: &Path) {
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, b"").unwrap();
    }

    fn install(name: &str, current: Option<&str>) -> (PathBuf, PathBuf) {
        let root = scratch(name);
        let exe = root.join(LAUNCHER_EXE);
        touch(&exe);
        touch(&root.join("versions/app-1.2.472").join(LAUNCHER_EXE));
        if let Some(text) = current {
            std::fs::write(root.join("versions/current.json"), text).unwrap();
        }
        (root, exe)
    }

    #[test]
    fn root_mode_execs_the_version_launcher() {
        let (root, exe) =
            install("ok", Some(r#"{"version":"1.2.472","dir":"versions/app-1.2.472","written":1793942000.5}"#));
        assert_eq!(root_decision(&exe, false), Root::Exec(root.join("versions/app-1.2.472").join(LAUNCHER_EXE)));
        // Already hopped: never again.
        assert_eq!(root_decision(&exe, true), Root::RunHere);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn broken_or_missing_pointer_goes_to_the_stub() {
        for (name, text) in [
            ("missing", None),
            ("garbage", Some("{not json")),
            ("no_dir", Some(r#"{"version":"1.2.472"}"#)),
            ("gone", Some(r#"{"version":"1.2.9","dir":"versions/app-1.2.9"}"#)),
            ("self", Some(r#"{"version":"1.2.472","dir":"."}"#)),
        ] {
            let (root, exe) = install(name, text);
            assert_eq!(root_decision(&exe, false), Root::Stub(root.join(STUB_EXE)), "{name}");
            let _ = std::fs::remove_dir_all(&root);
        }
    }

    #[test]
    fn a_version_folder_runs_here() {
        let (root, _) = install("version", Some(r#"{"dir":"versions/app-1.2.472"}"#));
        let version_exe = root.join("versions/app-1.2.472").join(LAUNCHER_EXE);
        assert_eq!(root_decision(&version_exe, false), Root::RunHere);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn setting_matches_normalize_native_launcher() {
        let d = scratch("settings");
        assert_eq!(native_setting(&d), DEFAULT_NATIVE_LAUNCHER);
        for (json, want) in [
            (r#"{"native_launcher":"auto"}"#, "auto"),
            (r#"{"native_launcher":" ON "}"#, "on"),
            (r#"{"native_launcher":"off"}"#, "off"),
            (r#"{"native_launcher":"maybe"}"#, "off"),
            (r#"{"native_launcher":1}"#, "off"),
            (r#"{"theme":"dark"}"#, DEFAULT_NATIVE_LAUNCHER),
            ("broken", DEFAULT_NATIVE_LAUNCHER),
        ] {
            std::fs::write(d.join("panel_settings.json"), json).unwrap();
            assert_eq!(native_setting(&d), want, "{json}");
        }
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn before_start_paths() {
        let args: Vec<OsString> = ["uefn-ducky://a", "--background"].map(OsString::from).to_vec();
        let (root, exe) = install("before", Some(r#"{"dir":"versions/app-1.2.472"}"#));
        let version = root.join("versions/app-1.2.472").join(LAUNCHER_EXE);
        let never = || -> String { panic!("setting read in root mode") };
        // Root: hop, whatever the setting.
        let s = before_start(&exe, &args, false, false, never, || false).unwrap();
        assert_eq!((s.exe, s.args), (version.clone(), args.clone()));
        // Version folder, setting off: the Python window with NO_NATIVE, argv in order.
        let s = before_start(&version, &args, true, false, || "off".into(), || false).unwrap();
        assert_eq!(s, Spawn::python(sibling_stub(&version), &args));
        // auto during a crash-loop hold: Python too; `on` ignores the hold.
        assert!(before_start(&version, &args, true, false, || "auto".into(), || true).is_some());
        assert!(before_start(&version, &args, true, false, || "auto".into(), || false).is_none());
        assert!(before_start(&version, &args, true, false, || "on".into(), || true).is_none());
        // Started by Python's run_native: it already decided.
        assert!(before_start(&version, &args, true, true, || "off".into(), || true).is_none());
        // Broken pointer at the root: the stub, no NO_NATIVE (it decides itself).
        std::fs::write(root.join("versions/current.json"), "nope").unwrap();
        let s = before_start(&exe, &args, false, false, never, || false).unwrap();
        assert_eq!(s, Spawn { exe: root.join(STUB_EXE), args: args.clone(), env: vec![] });
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn decide_like_native_switch() {
        assert!(!wants_native("off", false));
        assert!(wants_native("on", true));
        assert!(wants_native("auto", false));
        assert!(!wants_native("auto", true));
    }

    #[test]
    fn fallback_codes_and_spawns_keep_argv() {
        for c in 20..=24 {
            assert!(is_fallback_code(c));
        }
        for c in [0, 3, 19, 25] {
            assert!(!is_fallback_code(c));
        }
        let args: Vec<OsString> =
            ["--background", "C:/a b.verse", "uefn-ducky://x?y=1", "--after-update", "42"].map(OsString::from).to_vec();
        let p = Spawn::python(PathBuf::from("C:/v/UEFN-Ducky.exe"), &args);
        assert_eq!(p.args, args);
        assert_eq!(p.env, [(NO_NATIVE_ENV.to_string(), "1".to_string())]);
        let h = Spawn::hop(PathBuf::from("C:/v/Ducky-Launcher.exe"), &args);
        assert_eq!(h.args, args);
        assert_eq!(h.env, [(HOPPED_ENV.to_string(), "1".to_string())]);
        assert_eq!(sibling_stub(Path::new("C:/v/Ducky-Launcher.exe")), Path::new("C:/v").join(STUB_EXE));
    }
}
