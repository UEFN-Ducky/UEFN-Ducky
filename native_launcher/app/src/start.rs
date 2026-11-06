//! The start order and the exit code each outcome gives, kept free of Win32 so it tests
//! with a mock [`Host`]: instance hand-off, WebView2, window + splash, backend + hello,
//! navigate, then serve until the backend or the user ends it. The crash report is
//! written with the final code on every path.

use std::path::{Path, PathBuf};

use autostart::StartPlan;
use supervisor::protocol::Hello;
use supervisor::StartError;

use crate::exit;

/// How the running phase ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum End {
    /// The backend quit on purpose, or the user closed Ducky.
    Exited,
    /// The supervisor gave up with this contract code (22 after a crash loop).
    Fatal(i32, String),
}

/// One launch's side effects, in the order [`launch`] calls them.
pub trait Host {
    /// A living Ducky took this launch's argv (or `--background` left it running).
    fn hand_off(&mut self) -> bool;
    /// Serve the static UI on the public port (`crates/server`), backend paths held until
    /// hello. True = the page can load before hello and the backend binds an internal port.
    fn serve_static(&mut self) -> bool;
    /// Start `--backend` without waiting: Python's ~1.8 s boot runs while the window opens.
    fn spawn_backend(&mut self, plan: &StartPlan) -> Result<(), String>;
    fn webview2_ok(&mut self) -> bool;
    /// The window with the splash (hidden when the plan starts in the background).
    fn open_window(&mut self, plan: &StartPlan) -> Result<(), String>;
    /// Wait (pumping the window) for the backend's hello.
    fn wait_hello(&mut self) -> Result<Hello, StartError>;
    /// Let the held `/api`, `/__*` and websocket requests through to `hello.origin`.
    fn release(&mut self, hello: &Hello) -> Result<(), String>;
    /// Load the backend's page (or, started hidden, only report ready).
    fn navigate(&mut self, hello: &Hello) -> Result<(), String>;
    /// Page calls, tray, toasts and dialogs until the end.
    fn serve(&mut self) -> End;
    fn stage(&mut self, name: &str);
    /// Bye to the backend and wait for it (so a fallback window doesn't hand off to it).
    fn stop_backend(&mut self);
    /// Started directly and failed with 20–24: open the Python window instead.
    fn fallback(&mut self, code: u8);
    /// Write the crash report with the final code.
    fn finish(&mut self, code: u8, error: &str);
}

/// A contract code from a crate (`supervisor::exit`, `window::EXIT_WEBVIEW2`) as the
/// launcher's exit code; anything outside the contract is a start error.
pub fn contract_code(code: i32) -> u8 {
    match code {
        0 | 20..=24 => code as u8,
        _ => exit::START_ERROR,
    }
}

/// Run one launch through `host`; returns the process exit code. `direct` = not started by
/// `native_switch.run_native` (which falls back itself on 20–24).
pub fn launch(host: &mut impl Host, plan: &StartPlan, direct: bool) -> u8 {
    let (code, error) = steps(host, plan);
    host.stop_backend();
    if direct && crate::repoint::is_fallback_code(code) {
        host.fallback(code);
    }
    host.finish(code, &error);
    code
}

fn steps(host: &mut impl Host, plan: &StartPlan) -> (u8, String) {
    if host.hand_off() {
        return (exit::OK, String::new());
    }
    host.stage("instance");
    if host.serve_static() {
        host.stage("static_served");
    }
    if let Err(e) = host.spawn_backend(plan) {
        return (exit::START_ERROR, format!("backend: {e}"));
    }
    host.stage("backend_spawned");
    // A failure from here on stops the spawned backend in `finish`.
    if !host.webview2_ok() {
        return (exit::WEBVIEW2_MISSING, "WebView2 missing or too old".into());
    }
    if let Err(e) = host.open_window(plan) {
        return (exit::START_ERROR, format!("window: {e}"));
    }
    host.stage("window");
    let hello = match host.wait_hello() {
        Ok(h) => h,
        Err(e) => return (contract_code(e.code), e.reason),
    };
    host.stage("hello");
    if hello.handoff {
        // The backend handed this launch to another Ducky: no page here.
        return (exit::OK, String::new());
    }
    if let Err(e) = host.release(&hello) {
        return (exit::START_ERROR, format!("release: {e}"));
    }
    if let Err(e) = host.navigate(&hello) {
        return (exit::START_ERROR, format!("navigate: {e}"));
    }
    match host.serve() {
        End::Exited => (exit::OK, String::new()),
        End::Fatal(code, reason) => (contract_code(code), reason),
    }
}

/// Load the page now? With the static UI served here (`early_page`) the WebView2 navigates
/// as soon as it is ready; otherwise it waits for the backend's hello.
pub fn should_navigate(page_host_ready: bool, navigated: bool, early_page: bool, backend_ready: bool) -> bool {
    page_host_ready
        && !navigated
        && splash::first_paint(early_page, backend_ready) == splash::FirstPaint::Navigate
}

/// The panel's built web UI for a backend EXE, as `webview_app._web_root` finds it: the
/// PyInstaller `_internal` folder, a flat (Nuitka) layout, or a source checkout.
pub fn web_dist(backend_exe: &Path) -> Option<PathBuf> {
    let dir = backend_exe.parent()?;
    let rel = Path::new("frontend").join("ui_web").join("web").join("dist");
    [dir.join("_internal").join(&rel), dir.join(&rel), dir.join("ducky_app").join(&rel)]
        .into_iter()
        .find(|d| d.join("index.html").is_file())
}

/// The page is up. Under `native_switch` (`native_child`): the ready file it waits for (it
/// records the start itself). A direct start: no ready file (nobody reads it), its own
/// `starts.json` row instead.
pub fn on_ready(app_data: &Path, native_child: bool, launcher_pid: u32, window_ms: u64, painted_ms: u64) -> std::io::Result<()> {
    if native_child {
        write_ready(app_data, launcher_pid, window_ms, painted_ms)
    } else {
        crate::starts::record(app_data, true, Some(window_ms), Some(painted_ms), "")
    }
}

/// `launcher/ready-<pid>.json`: `native_switch.run_native` waits for it, then exits.
pub fn ready_path(app_data: &Path, launcher_pid: u32) -> PathBuf {
    app_data.join("launcher").join(format!("ready-{launcher_pid}.json"))
}

pub fn write_ready(app_data: &Path, launcher_pid: u32, window_ms: u64, painted_ms: u64) -> std::io::Result<()> {
    let path = ready_path(app_data, launcher_pid);
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let body = serde_json::json!({"window_ms": window_ms, "painted_ms": painted_ms}).to_string();
    // Written whole, then renamed: the Python side may read it at any moment.
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, body)?;
    std::fs::rename(&tmp, &path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hello(handoff: bool) -> Hello {
        Hello {
            protocol: 1,
            app_version: "1.2.471".into(),
            port: 4199,
            origin: "http://127.0.0.1:4199".into(),
            profile_dir: String::new(),
            pid: 7,
            handoff,
        }
    }

    #[derive(Default)]
    struct Mock {
        calls: Vec<String>,
        handed_off: bool,
        no_webview2: bool,
        window_err: bool,
        backend: Option<Result<Hello, StartError>>,
        end: Option<End>,
        finished: Option<u8>,
    }

    impl Host for Mock {
        fn hand_off(&mut self) -> bool {
            self.calls.push("hand_off".into());
            self.handed_off
        }
        fn serve_static(&mut self) -> bool {
            self.calls.push("static".into());
            true
        }
        fn release(&mut self, _hello: &Hello) -> Result<(), String> {
            self.calls.push("release".into());
            Ok(())
        }
        fn webview2_ok(&mut self) -> bool {
            self.calls.push("webview2".into());
            !self.no_webview2
        }
        fn open_window(&mut self, plan: &StartPlan) -> Result<(), String> {
            self.calls.push(format!("window(show={})", plan.show_window));
            if self.window_err { Err("no".into()) } else { Ok(()) }
        }
        fn spawn_backend(&mut self, _plan: &StartPlan) -> Result<(), String> {
            self.calls.push("spawn".into());
            Ok(())
        }
        fn wait_hello(&mut self) -> Result<Hello, StartError> {
            self.calls.push("hello".into());
            self.backend.take().unwrap_or_else(|| Ok(hello(false)))
        }
        fn navigate(&mut self, _hello: &Hello) -> Result<(), String> {
            self.calls.push("navigate".into());
            Ok(())
        }
        fn serve(&mut self) -> End {
            self.calls.push("serve".into());
            self.end.take().unwrap_or(End::Exited)
        }
        fn stage(&mut self, name: &str) {
            self.calls.push(format!("stage:{name}"));
        }
        fn stop_backend(&mut self) {
            self.calls.push("stop".into());
        }
        fn fallback(&mut self, code: u8) {
            self.calls.push(format!("fallback:{code}"));
        }
        fn finish(&mut self, code: u8, _error: &str) {
            self.calls.push(format!("finish:{code}"));
            self.finished = Some(code);
        }
    }

    fn plan(args: &[&str]) -> StartPlan {
        let mut argv = vec!["Ducky-Launcher.exe".to_string()];
        argv.extend(args.iter().map(|s| s.to_string()));
        autostart::plan(&argv, None)
    }

    fn err(code: i32) -> Option<Result<Hello, StartError>> {
        Some(Err(StartError { code, reason: format!("code {code}") }))
    }

    #[test]
    fn start_order_on_a_normal_launch() {
        let mut m = Mock::default();
        assert_eq!(launch(&mut m, &plan(&[]), false), 0);
        assert_eq!(
            m.calls,
            [
                "hand_off", "stage:instance", "static", "stage:static_served", "spawn", "stage:backend_spawned",
                "webview2", "window(show=true)", "stage:window", "hello", "stage:hello", "release", "navigate",
                "serve", "stop", "finish:0"
            ]
        );
    }

    #[test]
    fn background_opens_hidden() {
        let mut m = Mock::default();
        launch(&mut m, &plan(&["--background"]), false);
        assert!(m.calls.contains(&"window(show=false)".to_string()));
    }

    #[test]
    fn instance_hand_off_exits_0_before_anything_else() {
        let mut m = Mock { handed_off: true, ..Default::default() };
        assert_eq!(launch(&mut m, &plan(&[]), false), 0);
        assert_eq!(m.calls, ["hand_off", "stop", "finish:0"]);
    }

    #[test]
    fn backend_hand_off_exits_0_without_a_page() {
        let mut m = Mock { backend: Some(Ok(hello(true))), ..Default::default() };
        assert_eq!(launch(&mut m, &plan(&[]), false), 0);
        assert!(!m.calls.iter().any(|c| c == "navigate" || c == "serve"));
        assert_eq!(m.finished, Some(0));
    }

    #[test]
    fn exit_codes_per_contract() {
        let run = |m: &mut Mock| launch(m, &plan(&[]), false);
        assert_eq!(run(&mut Mock { no_webview2: true, ..Default::default() }), 20);
        assert_eq!(run(&mut Mock { backend: err(21), ..Default::default() }), 21);
        assert_eq!(run(&mut Mock { backend: err(22), ..Default::default() }), 22);
        assert_eq!(run(&mut Mock { backend: err(23), ..Default::default() }), 23);
        // Backend exit 3 (BUSY) reaches the app as the supervisor's start error.
        assert_eq!(run(&mut Mock { backend: err(supervisor::exit::START_ERROR), ..Default::default() }), 24);
        assert_eq!(run(&mut Mock { window_err: true, ..Default::default() }), 24);
        assert_eq!(run(&mut Mock { end: Some(End::Fatal(22, "loop".into())), ..Default::default() }), 22);
        assert_eq!(run(&mut Mock { end: Some(End::Exited), ..Default::default() }), 0);
    }

    #[test]
    fn direct_start_falls_back_to_python_on_each_code() {
        let cases: Vec<(Mock, u8)> = vec![
            (Mock { no_webview2: true, ..Default::default() }, 20),
            (Mock { backend: err(21), ..Default::default() }, 21),
            (Mock { backend: err(22), ..Default::default() }, 22),
            (Mock { backend: err(23), ..Default::default() }, 23),
            (Mock { window_err: true, ..Default::default() }, 24),
        ];
        for (mut m, code) in cases {
            assert_eq!(launch(&mut m, &plan(&[]), true), code);
            let n = m.calls.len();
            // Backend stopped first, then the Python window, then the report.
            assert_eq!(m.calls[n - 3..], ["stop".to_string(), format!("fallback:{code}"), format!("finish:{code}")]);
        }
        // Started by run_native: Python falls back itself.
        let mut m = Mock { no_webview2: true, ..Default::default() };
        launch(&mut m, &plan(&[]), false);
        assert!(!m.calls.iter().any(|c| c.starts_with("fallback")));
        // Ok and hand-off never fall back.
        for mut m in [Mock::default(), Mock { handed_off: true, ..Default::default() }] {
            assert_eq!(launch(&mut m, &plan(&[]), true), 0);
            assert!(!m.calls.iter().any(|c| c.starts_with("fallback")));
        }
    }

    #[test]
    fn webview2_missing_stops_before_the_window() {
        let mut m = Mock { no_webview2: true, ..Default::default() };
        launch(&mut m, &plan(&[]), false);
        assert!(!m.calls.iter().any(|c| c.starts_with("window")));
        assert_eq!(m.calls.last().unwrap(), "finish:20");
    }

    #[test]
    fn backend_spawns_before_webview2_and_window() {
        let mut m = Mock::default();
        launch(&mut m, &plan(&[]), false);
        let at = |name: &str| m.calls.iter().position(|c| c.starts_with(name)).unwrap();
        assert!(at("spawn") < at("webview2"));
        assert!(at("spawn") < at("window"));
        assert!(at("window") < at("hello"));
        // Handed off before anything starts: no backend at all.
        let mut m = Mock { handed_off: true, ..Default::default() };
        launch(&mut m, &plan(&[]), false);
        assert!(!m.calls.iter().any(|c| c == "spawn"));
    }

    #[test]
    fn no_hello_still_gives_23_after_an_early_spawn() {
        let mut m = Mock { backend: err(supervisor::exit::NO_HELLO), ..Default::default() };
        assert_eq!(launch(&mut m, &plan(&[]), false), 23);
        assert!(!m.calls.iter().any(|c| c == "navigate" || c == "release"));
    }

    #[test]
    fn static_ui_before_spawn_and_release_right_after_hello() {
        let mut m = Mock::default();
        launch(&mut m, &plan(&[]), false);
        let at = |name: &str| m.calls.iter().position(|c| c == name).unwrap();
        assert!(at("static") < at("spawn"));
        assert_eq!(at("release"), at("stage:hello") + 1);
        assert!(at("release") < at("navigate"));
        // A hand-off hello releases nothing: that page belongs to another Ducky.
        let mut m = Mock { backend: Some(Ok(hello(true))), ..Default::default() };
        launch(&mut m, &plan(&[]), false);
        assert!(!m.calls.iter().any(|c| c == "release"));
    }

    #[test]
    fn page_loads_on_env_ready_not_on_hello() {
        // Static UI served here: navigate as soon as the WebView2 is ready, before hello.
        assert!(should_navigate(true, false, true, false));
        // Not before the WebView2 (and its shim) is ready, and only once.
        assert!(!should_navigate(false, false, true, false));
        assert!(!should_navigate(true, true, true, true));
        // No static UI here: wait for hello, as before.
        assert!(!should_navigate(true, false, false, false));
        assert!(should_navigate(true, false, false, true));
    }

    #[test]
    fn web_dist_layouts() {
        let base = std::env::temp_dir().join(format!("ducky-dist-{}", std::process::id()));
        let rel = Path::new("frontend/ui_web/web/dist");
        for layout in ["_internal", "", "ducky_app"] {
            let _ = std::fs::remove_dir_all(&base);
            let dist = base.join(layout).join(rel);
            std::fs::create_dir_all(&dist).unwrap();
            std::fs::write(dist.join("index.html"), "<html>").unwrap();
            assert_eq!(web_dist(&base.join("UEFN-Ducky.exe")), Some(dist), "{layout}");
        }
        let _ = std::fs::remove_dir_all(&base);
        assert_eq!(web_dist(&base.join("UEFN-Ducky.exe")), None);
    }

    #[test]
    fn contract_code_mapping() {
        for c in [0, 20, 21, 22, 23, 24] {
            assert_eq!(contract_code(c) as i32, c);
        }
        assert_eq!(contract_code(3), 24);
        assert_eq!(contract_code(-1), 24);
        assert_eq!(contract_code(window::EXIT_WEBVIEW2), 20);
    }

    #[test]
    fn direct_start_records_a_row_native_child_writes_the_ready_file() {
        let dir = std::env::temp_dir().join(format!("ducky-onready-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        on_ready(&dir, false, 42, 40, 900).unwrap();
        assert!(!ready_path(&dir, 42).exists(), "direct start: no ready file");
        let v: serde_json::Value =
            serde_json::from_slice(&std::fs::read(dir.join("launcher/starts.json")).unwrap()).unwrap();
        let rows = v["starts"].as_array().unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!((rows[0]["mode"].as_str(), rows[0]["painted_ms"].as_u64()), (Some("native"), Some(900)));
        on_ready(&dir, true, 43, 40, 900).unwrap();
        assert!(ready_path(&dir, 43).is_file(), "native_switch still gets its ready file");
        let v: serde_json::Value =
            serde_json::from_slice(&std::fs::read(dir.join("launcher/starts.json")).unwrap()).unwrap();
        assert_eq!(v["starts"].as_array().unwrap().len(), 1, "native_switch records that start itself");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn ready_file_shape() {
        let dir = std::env::temp_dir().join(format!("ducky-ready-{}", std::process::id()));
        write_ready(&dir, 42, 120, 480).unwrap();
        let v: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(ready_path(&dir, 42)).unwrap()).unwrap();
        assert_eq!(v, serde_json::json!({"window_ms": 120, "painted_ms": 480}));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
