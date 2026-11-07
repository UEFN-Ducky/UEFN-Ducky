//! Repoint: shortcuts start `<install root>/Ducky-Launcher.exe` directly, so the launcher
//! does what the `UEFN-Ducky.exe` stub did before showing a window:
//! - root mode: hop to the current version's own launcher (`versions/current.json`), or,
//!   when that pointer is missing or broken, once to the root stub with NO_NATIVE (the
//!   Python window, which never starts a launcher again);
//! - the `native_launcher` setting: `off` and `auto` run the Python window instead (auto
//!   until this launcher hosts Browser tabs, focus windows, capture, snip and menus);
//! - a start that fails with 20–24 opens the Python window, so the user still gets one.
//!
//! A launch started by `native_switch` (NATIVE_CHILD) never hops, and a hopped launch
//! (HOP) never hops again: no chain is longer than one hop. Argv passes through unchanged.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

use serde_json::Value;

pub const LAUNCHER_EXE: &str = "Ducky-Launcher.exe";
pub const STUB_EXE: &str = "UEFN-Ducky.exe";
/// Tells `UEFN-Ducky.exe` to use the Python window and never start a launcher.
pub const NO_NATIVE_ENV: &str = "UEFN_DUCKY_NO_NATIVE";
/// Set by `native_switch.run_native`: Python decided and handles any fallback itself.
pub const NATIVE_CHILD_ENV: &str = "UEFN_DUCKY_NATIVE_CHILD";
/// Set on every hop: the next launcher runs here (or the Python window), never hops again.
pub const HOP_ENV: &str = "UEFN_DUCKY_LAUNCHER_HOP";
/// `app_versions.LAUNCHER_ENV`: the EXE that was started before any hop.
pub const LAUNCHER_ENV: &str = "UEFN_DUCKY_LAUNCHER_EXE";
/// `PanelSettings.native_launcher`'s default in `frontend/settings.py`.
pub const DEFAULT_NATIVE_LAUNCHER: &str = "auto";
/// Python's copy of the setting (`settings.LAUNCHER_SETTING_FILE`): the default store keeps
/// settings in ducky.db, which this launcher doesn't read.
pub const SETTING_FILE: &str = "launcher/native_launcher.json";
/// `app_versions.VERSIONS_DIR_NAME` / `CURRENT_FILE`.
pub const VERSIONS_DIR: &str = "versions";
pub const CURRENT_FILE: &str = "current.json";

/// What a launch started from `exe` does first.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Root {
    /// A version folder, the current one, a dev build or already hopped: start Ducky here.
    RunHere,
    /// The install root and the pointer names another version: that version's launcher.
    Exec(PathBuf),
    /// The install root without a usable pointer: the stub, once, with NO_NATIVE.
    Stub(PathBuf),
}

/// What `current.json` points at: the launcher EXE and the version it names.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pointer {
    pub exe: PathBuf,
    pub version: String,
}

/// The launcher in `current.json` at `file`, `dir` resolved against `base` (or absolute),
/// when that EXE exists. `app_versions.write_current` writes `versions/app-<v>` (relative
/// to `%LOCALAPPDATA%/UEFN-Ducky`) or the installed EXE's absolute folder.
pub fn pointer_launcher(file: &Path, base: &Path) -> Option<Pointer> {
    let text = std::fs::read_to_string(file).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    let dir = v.get("dir")?.as_str()?.trim();
    if dir.is_empty() {
        return None;
    }
    // `join` keeps an absolute `dir` as it is.
    let exe = base.join(dir).join(LAUNCHER_EXE);
    let version = v.get("version").and_then(Value::as_str).unwrap_or("").trim().to_string();
    exe.is_file().then_some(Pointer { exe, version })
}

/// `%LOCALAPPDATA%/UEFN-Ducky/versions/current.json` first (what Python writes), then
/// `<root>/versions/current.json` (a portable/drill layout, `dir` relative to the root).
pub fn current_launcher(app_data: &Path, root: &Path) -> Option<Pointer> {
    pointer_launcher(&app_data.join(VERSIONS_DIR).join(CURRENT_FILE), app_data)
        .or_else(|| pointer_launcher(&root.join(VERSIONS_DIR).join(CURRENT_FILE), root))
}

fn same_file(a: &Path, b: &Path) -> bool {
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(x), Ok(y)) => x == y,
        _ => a == b,
    }
}

/// `…/versions/app-<v>/Ducky-Launcher.exe`: a version's own launcher.
fn in_version_folder(exe: &Path) -> bool {
    let Some(dir) = exe.parent() else { return false };
    let named = |p: &Path, f: &dyn Fn(&str) -> bool| p.file_name().and_then(|n| n.to_str()).is_some_and(f);
    named(dir, &|n| n.starts_with("app-")) && dir.parent().is_some_and(|v| named(v, &|n| n.eq_ignore_ascii_case(VERSIONS_DIR)))
}

/// The root can run `version` itself: it is this launcher's own version and the root holds a
/// complete install (`.ready`, as `app_versions.is_ready`, or at least its stub).
fn root_runs(root: &Path, own_version: &str, version: &str) -> bool {
    !version.is_empty()
        && version == own_version
        && (root.join(".ready").is_file() || root.join(STUB_EXE).is_file())
}

/// `version` is older than `than` (dotted numbers); false when either does not parse.
fn older(version: &str, than: &str) -> bool {
    let key = |v: &str| v.trim().split('.').map(|p| p.parse::<u32>().ok()).collect::<Option<Vec<_>>>();
    matches!((key(version), key(than)), (Some(a), Some(b)) if a < b)
}

/// Root mode: an installed root launcher (the stub beside it, not a version folder), not
/// hopped. A dev build with no stub beside it always runs here. `own_version` is this
/// launcher's `--version`.
pub fn root_decision(exe: &Path, hopped: bool, app_data: &Path, own_version: &str) -> Root {
    let Some(root) = exe.parent() else {
        return Root::RunHere;
    };
    if hopped || in_version_folder(exe) || !root.join(STUB_EXE).is_file() {
        return Root::RunHere;
    }
    match current_launcher(app_data, root) {
        // The pointer names this very launcher (no update yet): it is the current one.
        Some(p) if same_file(&p.exe, exe) => Root::RunHere,
        // Same version as the root (Setup installed it, then a start pinned its folder):
        // the hop would only cost 120–490 ms.
        Some(p) if root_runs(root, own_version, &p.version) => Root::RunHere,
        // An older version than the root: Setup installed a newer one since that folder was
        // pinned. Like Python's hand_off, never start an older version; this root's backend
        // repoints current.json at itself (1.2.473 Setup kept starting 1.2.472).
        Some(p) if older(&p.version, own_version) => Root::RunHere,
        // A newer version (an in-place update): its own launcher.
        Some(p) => Root::Exec(p.exe),
        None => Root::Stub(root.join(STUB_EXE)),
    }
}

/// `native_launcher` as off | on | auto (`settings.normalize_native_launcher`): from
/// [`SETTING_FILE`], else `panel_settings.json` (the files store); missing is the default.
pub fn native_setting(app_data: &Path) -> String {
    let read = |name: &str| {
        std::fs::read_to_string(app_data.join(name))
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v.get("native_launcher").cloned())
    };
    let raw = read(SETTING_FILE).or_else(|| read("panel_settings.json"));
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
/// Always: the launcher's window is up in ~0.1 s, the Python window in 1-2 s, and the
/// setting no longer turns it off (same rule as `native_switch.decide`). A start that
/// fails with 20–24 still opens the Python window.
pub fn wants_native(_setting: &str, _crash_loop_active: bool) -> bool {
    true
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
    /// To another version's launcher: it runs there, never hops again.
    pub fn hop(exe: PathBuf, args: &[OsString]) -> Spawn {
        Spawn { exe, args: args.to_vec(), env: vec![(HOP_ENV.into(), "1".into())] }
    }

    /// To a stub as the Python window: NO_NATIVE, so it never starts a launcher again.
    pub fn python(exe: PathBuf, args: &[OsString]) -> Spawn {
        Spawn { exe, args: args.to_vec(), env: vec![(NO_NATIVE_ENV.into(), "1".into()), (HOP_ENV.into(), "1".into())] }
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
        // The EXE that was started, as Python's hand_off_env passes it: without it the
        // version's backend took its own folder for the start, called itself portable (no
        // updates) and pinned the Run key inside versions/.
        if std::env::var_os(LAUNCHER_ENV).is_none() {
            if let Ok(me) = std::env::current_exe() {
                cmd.env(LAUNCHER_ENV, me);
            }
        }
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
    app_data: &Path,
    own_version: &str,
    setting: impl FnOnce() -> String,
    crash_loop_active: impl FnOnce() -> bool,
) -> Option<Spawn> {
    // First: Python started us and decided. Hopping from here is what looped (root stub →
    // native_switch → root launcher → stub ...).
    if native_child {
        return None;
    }
    let mode = setting();
    // Only `auto` reads the crash-loop hold, as in `native_switch.decide`.
    if !wants_native(&mode, mode == "auto" && crash_loop_active()) {
        return Some(Spawn::python(sibling_stub(exe), args));
    }
    match root_decision(exe, hopped, app_data, own_version) {
        Root::RunHere => None,
        Root::Exec(target) => Some(Spawn::hop(target, args)),
        // No usable pointer: one Python window, never a launcher again.
        Root::Stub(stub) => Some(Spawn::python(stub, args)),
    }
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

    /// The test launcher's own version: older than every pointer below, unless on purpose.
    const OWN: &str = "1.2.1";

    #[test]
    fn an_older_pointer_runs_the_newer_root() {
        // 1.2.473 Setup over a copy that had updated itself to 1.2.472: start 1.2.473.
        let l = Layout::new("older");
        l.pointer(r#"{"version":"1.2.472","dir":"versions/app-1.2.472"}"#);
        assert_eq!(root_decision(&l.exe, false, &l.app_data, "1.2.473"), Root::RunHere);
        assert_eq!(root_decision(&l.exe, false, &l.app_data, "1.2.471"), Root::Exec(l.version.clone()));
        assert!(older("1.2.9", "1.2.10") && !older("1.2.10", "1.2.9") && !older("1.2.472", "1.2.472"));
        assert!(!older("", "1.2.473") && !older("1.2.x", "1.2.473"));
        l.drop_all();
    }

    #[test]
    fn same_version_runs_at_the_root_other_versions_hop_once() {
        let l = Layout::new("samever");
        // Setup's version pinned in its versions folder: same as the root, no hop.
        l.pointer(&serde_json::json!({"version": OWN, "dir": "versions/app-1.2.472"}).to_string());
        assert_eq!(l.decide(false), Root::RunHere);
        assert!(l.start(&args(), false, false, "on").is_none());
        // An in-place update (or a rollback) to another version: one hop to its launcher.
        l.pointer(r#"{"version":"1.2.472","dir":"versions/app-1.2.472"}"#);
        assert_eq!(l.start(&args(), false, false, "on"), Some(Spawn::hop(l.version.clone(), &args())));
        // No version in the pointer: it can't be the same, so hop as before.
        l.pointer(r#"{"dir":"versions/app-1.2.472"}"#);
        assert_eq!(l.decide(false), Root::Exec(l.version.clone()));
        l.drop_all();
    }

    /// An install root (stub + launcher) and an app data folder holding one version.
    struct Layout {
        root: PathBuf,
        exe: PathBuf,
        app_data: PathBuf,
        version: PathBuf,
    }

    impl Layout {
        fn new(name: &str) -> Layout {
            let base = scratch(name);
            let root = base.join("Programs/UEFN Ducky");
            let exe = root.join(LAUNCHER_EXE);
            touch(&exe);
            touch(&root.join(STUB_EXE));
            let app_data = base.join("UEFN-Ducky");
            let version = app_data.join("versions/app-1.2.472").join(LAUNCHER_EXE);
            touch(&version);
            Layout { root, exe, app_data, version }
        }

        fn pointer(&self, text: &str) {
            std::fs::write(self.app_data.join("versions/current.json"), text).unwrap();
        }

        fn decide(&self, hopped: bool) -> Root {
            root_decision(&self.exe, hopped, &self.app_data, OWN)
        }

        fn start(&self, args: &[OsString], hopped: bool, native_child: bool, mode: &str) -> Option<Spawn> {
            let mode = mode.to_string();
            before_start(&self.exe, args, hopped, native_child, &self.app_data, OWN, move || mode, || false)
        }

        fn drop_all(self) {
            let _ = std::fs::remove_dir_all(self.root.parent().unwrap().parent().unwrap());
        }
    }

    fn args() -> Vec<OsString> {
        ["uefn-ducky://a", "--background", "C:/a b.verse"].map(OsString::from).to_vec()
    }

    #[test]
    fn localappdata_pointer_relative_and_absolute() {
        let l = Layout::new("pointer");
        l.pointer(r#"{"version":"1.2.472","dir":"versions/app-1.2.472","installed":"1.2.470","written":1.5}"#);
        assert_eq!(l.decide(false), Root::Exec(l.version.clone()));
        let other = l.app_data.join("elsewhere");
        touch(&other.join(LAUNCHER_EXE));
        l.pointer(&serde_json::json!({"version": "1.2.473", "dir": other.to_string_lossy()}).to_string());
        assert_eq!(l.decide(false), Root::Exec(other.join(LAUNCHER_EXE)));
        l.drop_all();
    }

    #[test]
    fn pointer_at_the_install_root_runs_here() {
        // `write_current` with no ready version: the installed EXE's own folder.
        let l = Layout::new("self");
        l.pointer(&serde_json::json!({"version": "1.2.470", "dir": l.root.to_string_lossy()}).to_string());
        assert_eq!(l.decide(false), Root::RunHere);
        assert!(l.start(&args(), false, false, "on").is_none());
        l.drop_all();
    }

    #[test]
    fn root_layout_pointer_is_the_second_place() {
        let l = Layout::new("rootlayout");
        let v = l.root.join("versions/app-1.2.472").join(LAUNCHER_EXE);
        touch(&v);
        std::fs::write(l.root.join("versions/current.json"), r#"{"dir":"versions/app-1.2.472"}"#).unwrap();
        assert_eq!(l.decide(false), Root::Exec(v));
        // LOCALAPPDATA wins when both are there.
        l.pointer(r#"{"dir":"versions/app-1.2.472"}"#);
        assert_eq!(l.decide(false), Root::Exec(l.version.clone()));
        l.drop_all();
    }

    #[test]
    fn every_root_failure_is_one_python_window() {
        for (name, text) in [
            ("missing", None),
            ("broken", Some("{not json")),
            ("no_dir", Some(r#"{"version":"1.2.472"}"#)),
            ("missing_folder", Some(r#"{"version":"1.2.9","dir":"versions/app-1.2.9"}"#)),
        ] {
            let l = Layout::new(name);
            if let Some(t) = text {
                l.pointer(t);
            }
            let s = l.start(&args(), false, false, "on").expect(name);
            assert_eq!(s, Spawn::python(l.root.join(STUB_EXE), &args()), "{name}");
            assert!(s.env.contains(&(NO_NATIVE_ENV.to_string(), "1".to_string())), "{name}");
            // Started again in the chain (by native_switch, or after a hop): runs here, no
            // second stub start, so the old loop can't happen.
            assert!(l.start(&args(), false, true, "on").is_none(), "{name}");
            assert!(l.start(&args(), true, false, "on").is_none(), "{name}");
            l.drop_all();
        }
        // A saved off is ignored at the root too: the pointer's launcher, one hop.
        let l = Layout::new("off");
        l.pointer(r#"{"dir":"versions/app-1.2.472"}"#);
        assert_eq!(l.start(&args(), false, false, "off"), Some(Spawn::hop(l.version.clone(), &args())));
        l.drop_all();
    }

    #[test]
    fn native_child_never_hops_and_reads_nothing() {
        let l = Layout::new("child");
        l.pointer(r#"{"dir":"versions/app-1.2.472"}"#);
        let never = || -> String { panic!("setting read under run_native") };
        assert!(before_start(&l.exe, &args(), false, true, &l.app_data, OWN, never, || panic!()).is_none());
        l.drop_all();
    }

    #[test]
    fn hop_guard_stops_a_second_hop() {
        let l = Layout::new("guard");
        l.pointer(r#"{"dir":"versions/app-1.2.472"}"#);
        let s = l.start(&args(), false, false, "on").unwrap();
        assert_eq!(s, Spawn::hop(l.version.clone(), &args()));
        assert!(s.env.contains(&(HOP_ENV.to_string(), "1".to_string())));
        // The hopped launcher (HOP=1) runs where it is, even at a root with a pointer.
        assert_eq!(l.decide(true), Root::RunHere);
        assert!(l.start(&args(), true, false, "on").is_none());
        l.drop_all();
    }

    #[test]
    fn version_folders_and_dev_builds_run_here() {
        let l = Layout::new("version");
        l.pointer(r#"{"dir":"versions/app-1.2.9"}"#);
        touch(&l.version.with_file_name(STUB_EXE));
        assert_eq!(root_decision(&l.version, false, &l.app_data, OWN), Root::RunHere);
        // A cargo build with no stub beside it never hops into the installed app.
        let dev = l.app_data.join("target/release").join(LAUNCHER_EXE);
        touch(&dev);
        assert_eq!(root_decision(&dev, false, &l.app_data, OWN), Root::RunHere);
        l.drop_all();
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
        // Python's mirror (the db store has no panel_settings.json) wins over the file.
        std::fs::write(d.join("panel_settings.json"), r#"{"native_launcher":"on"}"#).unwrap();
        std::fs::create_dir_all(d.join("launcher")).unwrap();
        std::fs::write(d.join(SETTING_FILE), r#"{"native_launcher":"off"}"#).unwrap();
        assert_eq!(native_setting(&d), "off");
        std::fs::write(d.join(SETTING_FILE), r#"{"native_launcher":"auto"}"#).unwrap();
        assert_eq!(native_setting(&d), "auto");
        std::fs::remove_file(d.join("panel_settings.json")).unwrap();
        std::fs::remove_file(d.join(SETTING_FILE)).unwrap();
        assert_eq!(native_setting(&d), "auto", "no file at all: Python's default");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn version_launcher_runs_here_whatever_the_setting() {
        let l = Layout::new("hold");
        let v = |mode: &'static str, hold: bool| {
            before_start(&l.version, &args(), true, false, &l.app_data, OWN, move || mode.into(), move || hold)
        };
        for mode in ["off", "auto", "on", "garbage"] {
            assert!(v(mode, false).is_none(), "{mode}");
            assert!(v(mode, true).is_none(), "{mode} during a hold");
        }
        l.drop_all();
    }

    #[test]
    fn always_native() {
        for mode in ["off", "on", "auto", ""] {
            assert!(wants_native(mode, false), "{mode}");
            assert!(wants_native(mode, true), "{mode} during a hold");
        }
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
        assert_eq!(p.env, [(NO_NATIVE_ENV.to_string(), "1".to_string()), (HOP_ENV.to_string(), "1".to_string())]);
        let h = Spawn::hop(PathBuf::from("C:/v/Ducky-Launcher.exe"), &args);
        assert_eq!(h.args, args);
        assert_eq!(h.env, [(HOP_ENV.to_string(), "1".to_string())]);
        assert_eq!(sibling_stub(Path::new("C:/v/Ducky-Launcher.exe")), Path::new("C:/v").join(STUB_EXE));
    }
}
