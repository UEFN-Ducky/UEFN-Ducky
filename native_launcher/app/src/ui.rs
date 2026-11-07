//! The real [`Host`]: the window, splash, tray, page shim and native dialogs on the UI
//! thread, and the supervisor on its own thread. The two talk over channels; the
//! supervisor wakes the UI thread with a message to a hidden message-only window (thread
//! messages would be lost in the move/resize and dialog modal loops).

use std::cell::RefCell;
use std::ffi::OsString;
use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver, Sender, TryRecvError};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use autostart::StartPlan;
use ducky_instance::{InstanceLock, Launch, RealSystem};
use serde_json::{json, Value};
use supervisor::protocol::{BackendMsg, Hello};
use supervisor::{StartError, Step, Supervisor};
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{
    take_pwstr, AddScriptToExecuteOnDocumentCreatedCompletedHandler, NavigationCompletedEventHandler,
    NavigationStartingEventHandler, WebMessageReceivedEventHandler,
};
use windows::core::{HSTRING, PWSTR};
use windows::Win32::Foundation::*;
use windows::Win32::UI::Shell::ExtractIconW;
use windows::Win32::UI::WindowsAndMessaging::*;

use crate::page::{Action, Bridge};
use crate::{dialog, repoint};
use crate::start::{self, End, Host};
use crate::LOCK_WAIT;

// ponytail: not `mod windows` — that name would shadow the `windows` crate in this file.
#[path = "windows.rs"]
mod wins;
use wins::{create_main, create_msg_window, native_op, show_main};

const WM_WAKE: u32 = WM_APP + 1;
const TITLE: &str = "UEFN Ducky";
const BACKEND_EXE_ENV: &str = "UEFN_DUCKY_BACKEND_EXE";
const NATIVE_CHILD_ENV: &str = "UEFN_DUCKY_NATIVE_CHILD";
/// `headless.BACKEND_UI_PORT_ENV`: the backend binds here (0 = any free port) while the
/// launcher serves the public UI port.
const BACKEND_UI_PORT_ENV: &str = "UEFN_DUCKY_BACKEND_UI_PORT";
/// A Vite dev URL replaces the bundled page: no static UI from here then.
const WEB_DEV_ENV: &str = "UEFN_DUCKY_WEB_DEV";
/// How often the supervisor thread looks for page calls between backend lines.
/// ponytail: polling, a wake pipe if 10 ms of call latency ever shows.
const SUP_POLL: Duration = Duration::from_millis(10);

enum ToUi {
    Started(Result<(Hello, Option<u32>), StartError>),
    Step(Step),
}

enum ToSup {
    Call { id: String, name: String, args: Vec<Value> },
    Quit,
}

struct Ui {
    t0: Instant,
    app_data: PathBuf,
    record: report::LauncherRecord,
    msg_hwnd: HWND,
    main: Option<HWND>,
    main_prev_proc: isize,
    splash_hwnd: Option<HWND>,
    splash: splash::Splash,
    webview: Option<ICoreWebView2>,
    /// about:blank loaded and the shim installed: real navigations may start.
    page_host_ready: bool,
    url: Option<String>,
    navigated: bool,
    /// The static UI and the held backend paths on the public port (`serve_static`).
    server: Option<server::Server>,
    hello_seen: bool,
    /// Page calls, backend results and the start-up cache.
    bridge: Bridge,
    ready_written: bool,
    painted_ms: Option<u64>,
    /// Started by `native_switch.run_native`: the ready file waits for hello too.
    ready_needs_hello: bool,
    window_ms: u64,
    title: String,
    tray: Option<tray::win::Tray>,
    tray_icon: *mut std::ffi::c_void,
    to_sup: Option<Sender<ToSup>>,
    from_sup: Option<Receiver<ToUi>>,
    sup_thread: Option<JoinHandle<()>>,
    started: Option<Result<(Hello, Option<u32>), StartError>>,
    end: Option<End>,
    /// The backend's pop-out windows (`window_open`).
    extras: wins::Registry,
}

thread_local! {
    static UI: RefCell<Option<Ui>> = const { RefCell::new(None) };
}

/// Run `f` on the UI state. Never call into Win32 that can re-enter (dialogs, message
/// pumps, DestroyWindow) inside `f`: take what you need out first.
fn with<R>(f: impl FnOnce(&mut Ui) -> R) -> Option<R> {
    UI.with(|u| u.borrow_mut().as_mut().map(f))
}

fn ms_since(t0: Instant) -> u64 {
    t0.elapsed().as_millis() as u64
}

/// `UEFN_DUCKY_BACKEND_EXE` (set by `native_switch`), else `UEFN-Ducky.exe` beside this EXE.
fn backend_exe() -> PathBuf {
    if let Some(p) = std::env::var_os(BACKEND_EXE_ENV).filter(|v| !v.is_empty()) {
        return PathBuf::from(p);
    }
    let dir = std::env::current_exe().ok().and_then(|p| p.parent().map(PathBuf::from)).unwrap_or_default();
    dir.join("UEFN-Ducky.exe")
}

pub struct UiHost {
    args: Vec<OsString>,
    lock: Option<InstanceLock>,
}

impl UiHost {
    pub fn new(args: Vec<OsString>) -> UiHost {
        let app_data = ducky_instance::Config::from_env().map(|c| c.app_data).unwrap_or_default();
        UI.with(|u| {
            *u.borrow_mut() = Some(Ui {
                t0: Instant::now(),
                app_data,
                record: report::LauncherRecord::new(crate::APP_VERSION),
                msg_hwnd: HWND::default(),
                main: None,
                main_prev_proc: 0,
                splash_hwnd: None,
                splash: splash::Splash::new(false),
                webview: None,
                page_host_ready: false,
                url: None,
                navigated: false,
                server: None,
                hello_seen: false,
                bridge: Bridge::new(
                    shim::cache::default_path().map(|p| shim::cache::StartupCache::load(p, crate::APP_VERSION)),
                ),
                ready_written: false,
                painted_ms: None,
                ready_needs_hello: std::env::var(NATIVE_CHILD_ENV).is_ok_and(|v| v.trim() == "1"),
                window_ms: 0,
                title: TITLE.to_string(),
                tray: None,
                tray_icon: std::ptr::null_mut(),
                to_sup: None,
                from_sup: None,
                sup_thread: None,
                started: None,
                end: None,
                extras: Default::default(),
            })
        });
        UiHost { args, lock: None }
    }

    /// The plan for these args (argv[0] put back, as `autostart::plan` expects).
    pub fn plan(&self) -> StartPlan {
        let mut argv = vec!["Ducky-Launcher.exe".to_string()];
        argv.extend(self.args.iter().map(|a| a.to_string_lossy().into_owned()));
        let hidden = std::env::var(autostart::START_HIDDEN_ENV).ok();
        autostart::plan(&argv, hidden.as_deref())
    }
}

impl Host for UiHost {
    fn hand_off(&mut self) -> bool {
        // Held until the backend said hello (it has claimed panel.pid by then), so a second
        // launch waits for this one and then hands off to it.
        self.lock = Some(InstanceLock::acquire(LOCK_WAIT));
        match ducky_instance::Config::from_env() {
            Some(cfg) => matches!(
                ducky_instance::decide_launch(&self.args, &cfg, &RealSystem),
                Launch::HandedOff | Launch::LeftRunning
            ),
            None => false,
        }
    }

    fn serve_static(&mut self) -> bool {
        if std::env::var_os(WEB_DEV_ENV).is_some_and(|v| !v.is_empty()) {
            return false;
        }
        let Some(dist) = start::web_dist(&backend_exe()) else {
            return false;
        };
        let app_data = with(|u| u.app_data.clone()).unwrap_or_default();
        let port = server::port::pick_panel_port(&app_data).ui_port();
        // Taken (another Ducky or an old window): the backend keeps the port, as before.
        let Ok(srv) = server::Server::bind(port, dist, server::DEFAULT_HOLD) else {
            return false;
        };
        with(|u| {
            u.server = Some(srv);
            u.url = Some(format!("http://127.0.0.1:{port}/"));
        });
        true
    }

    fn release(&mut self, hello: &Hello) -> Result<(), String> {
        let released = with(|u| {
            u.hello_seen = true;
            u.bridge.on_hello(hello);
            u.server.as_ref().map(|s| s.release_origin(&hello.origin))
        })
        .flatten();
        match released {
            Some(false) => Err(format!("backend origin does not parse: {}", hello.origin)),
            _ => Ok(()),
        }
    }

    fn webview2_ok(&mut self) -> bool {
        window::check_webview2().is_ok()
    }

    fn open_window(&mut self, plan: &StartPlan) -> Result<(), String> {
        tray::win::set_process_aumid();
        let icon = unsafe { ExtractIconW(None, &HSTRING::from(backend_exe().as_os_str()), 0) };
        with(|u| {
            u.splash = splash::Splash::new(plan.show_splash);
            u.tray_icon = icon.0 as _;
        });
        if plan.show_tray {
            let t = tray::win::Tray::new(icon.0 as _, TITLE, Box::new(on_tray));
            with(|u| u.tray = t);
        }
        if plan.show_window {
            create_main().map_err(|e| e.to_string())?;
        }
        Ok(())
    }

    fn spawn_backend(&mut self, plan: &StartPlan) -> Result<(), String> {
        // The wake target first: the supervisor thread posts to it from its first line on.
        let msg_hwnd = create_msg_window().map_err(|e| e.to_string())?;
        let app_data = with(|u| {
            u.msg_hwnd = msg_hwnd;
            u.app_data.clone()
        })
        .unwrap_or_default();
        let mut cfg = supervisor::Config::ducky(backend_exe(), &plan.argv[1..], app_data);
        cfg.env = plan.backend_env.clone();
        cfg.env.push((NATIVE_CHILD_ENV.to_string(), "1".to_string()));
        if with(|u| u.server.is_some()).unwrap_or(false) {
            cfg.env.push((BACKEND_UI_PORT_ENV.to_string(), "0".to_string()));
        }
        let (to_sup, sup_rx) = mpsc::channel();
        let (ui_tx, from_sup) = mpsc::channel();
        let wake = msg_hwnd.0 as isize;
        let handle = std::thread::spawn(move || supervise(cfg, sup_rx, ui_tx, wake));
        with(|u| {
            u.to_sup = Some(to_sup);
            u.from_sup = Some(from_sup);
            u.sup_thread = Some(handle);
        });
        Ok(())
    }

    fn wait_hello(&mut self) -> Result<Hello, StartError> {
        // Pump the window (splash, drag, tray) until the hello or a start error arrives.
        let mut msg = MSG::default();
        loop {
            if let Some(r) = with(|u| u.started.take()).flatten() {
                self.lock = None;
                return r.map(|(hello, pid)| {
                    with(|u| u.record.backend_pid = pid);
                    hello
                });
            }
            if !unsafe { GetMessageW(&mut msg, None, 0, 0) }.as_bool() {
                // Closed before the backend was up: nothing to fall back to.
                return Err(StartError { code: 0, reason: "closed before hello".into() });
            }
            unsafe {
                let _ = TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
    }

    fn navigate(&mut self, hello: &Hello) -> Result<(), String> {
        let early = with(|u| u.server.is_some()).unwrap_or(false);
        // With the static UI here the page stays on the public port; hello's origin is the
        // backend's internal one, reached through the released gate.
        let url = if early { None } else { Some(window::start_url(&hello.origin).map_err(|e| e.to_string())?) };
        let shown = with(|u| {
            if let Some(url) = url {
                u.url = Some(url);
            }
            u.main.is_some()
        })
        .unwrap_or(false);
        if shown {
            navigate_when_ready();
        }
        // Hidden start, or a page that painted before hello: `run_native` may let go now.
        write_ready_once();
        Ok(())
    }

    fn serve(&mut self) -> End {
        let mut msg = MSG::default();
        while unsafe { GetMessageW(&mut msg, None, 0, 0) }.as_bool() {
            unsafe {
                let _ = TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
        with(|u| u.end.take()).flatten().unwrap_or(End::Exited)
    }

    fn stage(&mut self, name: &str) {
        with(|u| u.record.stage(name));
    }

    fn stop_backend(&mut self) {
        let (tx, thread) = with(|u| (u.to_sup.take(), u.sup_thread.take())).unwrap_or((None, None));
        if let Some(tx) = tx {
            let _ = tx.send(ToSup::Quit);
        }
        if let Some(t) = thread {
            let _ = t.join();
        }
    }

    fn fallback(&mut self, code: u8) {
        // The rollout numbers first, as `native_switch` does before its Python window.
        let app_data = with(|u| u.app_data.clone()).unwrap_or_default();
        let _ = crate::starts::record(&app_data, false, None, None, crate::starts::exit_reason(code));
        let exe = std::env::current_exe().unwrap_or_default();
        let started = repoint::Spawn::python(repoint::sibling_stub(&exe), &self.args).start();
        with(|u| {
            u.record.stage(if started.is_ok() { "fallback_python" } else { "fallback_python_failed" });
            u.record.stage(&format!("fallback_code_{code}"));
        });
    }

    fn finish(&mut self, code: u8, error: &str) {
        drop(with(|u| u.tray.take()));
        with(|u| {
            let exit = report::Exit::from_code(code as i32).unwrap_or(report::Exit::StartError);
            u.record.finish(exit, error);
            if let Some(path) = report::boot_json_path() {
                let _ = report::write(&path, &u.record);
            }
        });
    }
}

// --- supervisor thread ------------------------------------------------------------------

fn supervise(cfg: supervisor::Config, rx: Receiver<ToSup>, ui: Sender<ToUi>, wake: isize) {
    let post = |m: ToUi| {
        let _ = ui.send(m);
        unsafe {
            let _ = PostMessageW(Some(HWND(wake as _)), WM_WAKE, WPARAM(0), LPARAM(0));
        }
    };
    let mut sup = match Supervisor::start(cfg) {
        Ok(s) => s,
        Err(e) => return post(ToUi::Started(Err(e))),
    };
    let hello = sup.hello().cloned().expect("started supervisor has a hello");
    post(ToUi::Started(Ok((hello, sup.backend_pid()))));
    loop {
        loop {
            match rx.try_recv() {
                Ok(ToSup::Call { id, name, args }) => {
                    if let Err(e) = sup.call(&id, &name, &args) {
                        let msg = BackendMsg::Result { id, result: Err(format!("backend: {e}")) };
                        post(ToUi::Step(Step::Msg(msg)));
                    }
                }
                Ok(ToSup::Quit) | Err(TryRecvError::Disconnected) => {
                    sup.shutdown("launcher exit");
                    return;
                }
                Err(TryRecvError::Empty) => break,
            }
        }
        match sup.next(SUP_POLL) {
            Step::Idle => {}
            end @ (Step::Exited | Step::Fatal(_)) => return post(ToUi::Step(end)),
            step => post(ToUi::Step(step)),
        }
    }
}

// --- UI thread: backend lines ------------------------------------------------------------

fn drain_backend() {
    loop {
        let item = with(|u| u.from_sup.as_ref().map(|rx| rx.try_recv().ok())).flatten().flatten();
        match item {
            Some(ToUi::Started(r)) => {
                with(|u| u.started = Some(r));
            }
            Some(ToUi::Step(step)) => on_step(step),
            None => return,
        }
    }
}

fn on_step(step: Step) {
    match step {
        Step::Msg(BackendMsg::Result { id, result }) => {
            let actions = with(|u| u.bridge.on_result(&id, &result)).unwrap_or_default();
            run_actions(actions);
        }
        Step::Msg(BackendMsg::Event { name, data }) => on_event(&name, data),
        Step::Msg(_) | Step::Idle => {}
        Step::Restarted(hello) => {
            // The page keeps its origin; requests wait while the new backend starts, then go
            // to its (new) internal port.
            with(|u| {
                u.record.stage("restarted");
                u.bridge.on_hello(&hello);
                if let Some(s) = &u.server {
                    s.gate().hold();
                    s.release_origin(&hello.origin);
                }
            });
        }
        Step::Exited => {
            close_gate();
            end_with(End::Exited)
        }
        Step::Fatal(e) => {
            close_gate();
            end_with(End::Fatal(e.code, e.reason))
        }
    }
}

fn close_gate() {
    with(|u| u.server.as_ref().map(|s| s.gate().close()));
}

fn end_with(end: End) {
    with(|u| {
        u.end = Some(end);
        u.to_sup = None;
    });
    unsafe { PostQuitMessage(0) };
}

fn on_event(name: &str, data: Value) {
    match name {
        "quit" => {}
        "toast" => {
            let event = json!({"t": "event", "name": name, "data": data});
            with(|u| u.tray.as_ref().map(|t| t.handle_event(&event)));
        }
        "title" => {
            if let Some(title) = data.get("title").and_then(Value::as_str) {
                let main = with(|u| {
                    u.title = title.to_string();
                    u.main
                })
                .flatten();
                if let Some(h) = main {
                    unsafe {
                        let _ = SetWindowTextW(h, &HSTRING::from(title));
                    }
                }
            }
        }
        "dialog" => {
            let owner = with(|u| u.main.map(|h| h.0 as isize)).flatten().unwrap_or(0);
            if let Some((id, value)) = dialog::answer(&data, |req| dialogs::show(owner, req)) {
                send_call(id, "__dialog_result".into(), vec![value]);
            }
        }
        "window_open" | "window_close" | "window_raise" => wins::on_window_event(name, &data),
        _ => post_page(&shim::event_message(name, &data)),
    }
}

fn send_call(id: String, name: String, args: Vec<Value>) -> bool {
    with(|u| u.to_sup.as_ref().map(|tx| tx.send(ToSup::Call { id, name, args }).is_ok()))
        .flatten()
        .unwrap_or(false)
}

// --- UI thread: the page ------------------------------------------------------------------

fn post_page(json_text: &str) {
    if let Some(wv) = with(|u| u.webview.clone()).flatten() {
        unsafe {
            let _ = wv.PostWebMessageAsJson(&HSTRING::from(json_text));
        }
    }
}

/// To the pop-out window `wid`; gone or not loaded yet: dropped.
fn post_to(wid: &str, json_text: &str) {
    if let Some(wv) = with(|u| u.extras.get(wid).and_then(|e| e.webview.clone())).flatten() {
        unsafe {
            let _ = wv.PostWebMessageAsJson(&HSTRING::from(json_text));
        }
    }
}

fn on_page_message(wid: Option<&str>, text: &str) {
    let actions = with(|u| u.bridge.on_page_message_from(wid, text)).unwrap_or_default();
    run_actions(actions);
}

/// Carry out bridge actions outside the state borrow (native ops call into Win32).
fn run_actions(actions: Vec<Action>) {
    for action in actions {
        match action {
            Action::Page(msg) => post_page(&msg),
            Action::PageTo { wid, msg } => post_to(&wid, &msg),
            Action::Native { id, op } => match shim::untag_id(&id) {
                (Some(wid), orig) => {
                    let value = native_op(op, with(|u| u.extras.get(wid).map(|e| e.hwnd)).flatten());
                    post_to(wid, &shim::result_message(orig, Ok(value)));
                }
                (None, _) => {
                    let value = native_op(op, with(|u| u.main).flatten());
                    post_page(&shim::result_message(&id, Ok(value)));
                }
            },
            Action::Backend { id, name, args } => {
                let page_call = id.starts_with(shim::PAGE_ID_PREFIX);
                if !send_call(id.clone(), name, args) && page_call {
                    post_page(&shim::result_message(&id, Err("backend is not running".into())));
                }
            }
        }
    }
}

/// The first (about:blank) navigation finished: install the shim and the page handlers,
/// lift the splash above the WebView, then load the backend's page once it is known.
fn on_page_host(webview: ICoreWebView2) -> windows::core::Result<()> {
    hook_page(&webview, None)?;
    unsafe {
        let mut token = Default::default();
        let nav = NavigationCompletedEventHandler::create(Box::new(|_, _| {
            on_page_painted();
            Ok(())
        }));
        webview.add_NavigationCompleted(&nav, &mut token)?;
    }
    let splash = with(|u| {
        u.webview = Some(webview);
        u.page_host_ready = true;
        u.splash_hwnd
    })
    .flatten();
    if let Some(s) = splash {
        unsafe {
            let _ = SetWindowPos(s, Some(HWND_TOP), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
        }
    }
    navigate_when_ready();
    Ok(())
}

/// The shim, page calls and the Back-to-about:blank guard on a window's WebView2: the
/// main window (`wid` None) or a pop-out, whose page loads right after this.
fn hook_page(webview: &ICoreWebView2, wid: Option<String>) -> windows::core::Result<()> {
    unsafe {
        let done = AddScriptToExecuteOnDocumentCreatedCompletedHandler::create(Box::new(|_, _| Ok(())));
        webview.AddScriptToExecuteOnDocumentCreated(&HSTRING::from(shim::SHIM_JS), &done)?;
        let mut token = Default::default();
        let msg_wid = wid.clone();
        let msg = WebMessageReceivedEventHandler::create(Box::new(
            move |_, args: Option<ICoreWebView2WebMessageReceivedEventArgs>| {
                if let Some(a) = args {
                    let mut p = PWSTR::null();
                    if a.TryGetWebMessageAsString(&mut p).is_ok() {
                        on_page_message(msg_wid.as_deref(), &take_pwstr(p));
                    }
                }
                Ok(())
            },
        ));
        webview.add_WebMessageReceived(&msg, &mut token)?;
        let popout = wid.is_some();
        let starting = NavigationStartingEventHandler::create(Box::new(
            move |_, args: Option<ICoreWebView2NavigationStartingEventArgs>| {
                if let Some(a) = args {
                    let mut p = PWSTR::null();
                    let navigated = popout || with(|u| u.navigated).unwrap_or(false);
                    if a.Uri(&mut p).is_ok() && start::blocks_navigation(navigated, &take_pwstr(p)) {
                        a.SetCancel(true)?;
                    }
                }
                Ok(())
            },
        ));
        webview.add_NavigationStarting(&starting, &mut token)?;
    }
    Ok(())
}

fn navigate_when_ready() {
    let go = with(|u| {
        let early = u.server.is_some();
        if u.url.is_some() && start::should_navigate(u.page_host_ready, u.navigated, early, u.hello_seen) {
            u.navigated = true;
            u.webview.clone().zip(u.url.clone())
        } else {
            None
        }
    })
    .flatten();
    if let Some((wv, url)) = go {
        unsafe {
            let _ = wv.Navigate(&HSTRING::from(url));
        }
    }
}

fn on_page_painted() {
    let splash = with(|u| {
        if !u.navigated {
            return None;
        }
        if u.painted_ms.is_none() {
            u.painted_ms = Some(ms_since(u.t0));
            u.record.stage("painted");
        }
        u.splash.on_painted();
        u.splash_hwnd.take()
    })
    .flatten();
    if let Some(s) = splash {
        unsafe {
            let _ = DestroyWindow(s);
        }
    }
    write_ready_once();
}

/// `ready-<pid>.json` once the page has painted (a hidden start: once hello came). Under
/// `run_native` it also waits for hello: Python lets go at this file, and a backend that
/// never says hello must still reach Python's fallback.
fn write_ready_once() {
    with(|u| {
        if u.ready_written || (u.main.is_some() && u.painted_ms.is_none()) {
            return;
        }
        if (u.ready_needs_hello || u.main.is_none()) && !u.hello_seen {
            return;
        }
        u.ready_written = true;
        let painted = u.painted_ms.unwrap_or_else(|| ms_since(u.t0));
        let _ = start::on_ready(&u.app_data, u.ready_needs_hello, std::process::id(), u.window_ms, painted);
    });
}

fn on_tray(event: tray::TrayEvent) {
    match event {
        tray::TrayEvent::Menu(tray::MenuAction::Open) => show_main(),
        tray::TrayEvent::Menu(tray::MenuAction::ExitAll) => end_with(End::Exited),
        tray::TrayEvent::ToastClicked(target) => {
            show_main();
            if let Some(t) = target {
                post_page(&shim::event_message("toast_clicked", &json!({"kind": t.kind, "id": t.id})));
            }
        }
    }
}
