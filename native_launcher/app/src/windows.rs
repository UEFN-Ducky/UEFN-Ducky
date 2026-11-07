//! The launcher's own windows: the main window and its splash, the wake window, the
//! backend's pop-out windows (`window_open` / `window_close` / `window_raise`), and the
//! native window ops the page asks for. A child of `ui` (it reads the UI state).

use std::collections::HashMap;

use serde_json::{json, Value};
use shim::NativeOp;
use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2;
use windows::core::{w, HSTRING, PCWSTR};
use windows::Win32::Foundation::*;
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::HiDpi::GetDpiForWindow;
use windows::Win32::UI::WindowsAndMessaging::*;

use super::{drain_backend, hook_page, ms_since, on_page_host, with, TITLE, WM_WAKE};

/// A pop-out window the backend opened.
pub(super) struct Extra {
    pub(super) hwnd: HWND,
    /// Set once its about:blank loaded and the shim is in.
    pub(super) webview: Option<ICoreWebView2>,
}

/// The pop-out windows by the backend's window id.
#[derive(Default)]
pub(super) struct Registry {
    by_wid: HashMap<String, Extra>,
}

impl Registry {
    /// Does `window_open` for `wid` need a new window? No while its window is `alive`; a
    /// window the person closed (Alt+F4) is forgotten and made again.
    fn wants_new(&mut self, wid: &str, alive: impl Fn(&Extra) -> bool) -> bool {
        match self.by_wid.get(wid) {
            Some(e) if alive(e) => false,
            Some(_) => {
                self.by_wid.remove(wid);
                true
            }
            None => true,
        }
    }

    fn insert(&mut self, wid: &str, e: Extra) {
        self.by_wid.insert(wid.to_string(), e);
    }

    fn remove(&mut self, wid: &str) -> Option<Extra> {
        self.by_wid.remove(wid)
    }

    pub(super) fn get(&self, wid: &str) -> Option<&Extra> {
        self.by_wid.get(wid)
    }
}

/// `window_open` / `window_close` / `window_raise` from the backend.
pub(super) fn on_window_event(name: &str, data: &Value) {
    let Some(wid) = data.get("wid").and_then(Value::as_str).filter(|w| !w.is_empty()) else {
        return;
    };
    match name {
        "window_open" => open_extra(wid, data),
        "window_close" => {
            if let Some(e) = with(|u| u.extras.remove(wid)).flatten() {
                unsafe {
                    let _ = DestroyWindow(e.hwnd);
                }
            }
        }
        _ => raise_extra(wid),
    }
}

fn open_extra(wid: &str, data: &Value) {
    let alive = |e: &Extra| unsafe { IsWindow(Some(e.hwnd)).as_bool() };
    if !with(|u| u.extras.wants_new(wid, alive)).unwrap_or(false) {
        return raise_extra(wid);
    }
    let Some(url) = data.get("url").and_then(Value::as_str).map(str::to_string) else {
        return;
    };
    let title = data.get("title").and_then(Value::as_str).unwrap_or(TITLE).to_string();
    let key = wid.to_string();
    let opts = window::win::WindowOptions {
        title,
        width: bounds::DEFAULT_WIDTH,
        height: bounds::DEFAULT_HEIGHT,
        // Same folder and browser args as the main window: one WebView2 browser process.
        profile_dir: window::profile_dir(None, |k| std::env::var(k).ok()),
        url: "about:blank".into(),
        debug_port: window::debug_port_from_env(std::env::var(window::config::DEBUG_PORT_ENV).ok().as_deref()),
        hit: Default::default(),
        on_painted: Some(Box::new(move |_ok, hwnd| {
            if let Some(wv) = window::win::webview(hwnd) {
                let _ = hook_page(&wv, Some(key.clone()));
                with(|u| u.extras.by_wid.get_mut(&key).map(|e| e.webview = Some(wv.clone())));
                unsafe {
                    let _ = wv.Navigate(&HSTRING::from(url));
                }
            }
        })),
        on_webview_error: None,
        quit_on_close: false,
    };
    let Ok(hwnd) = window::win::create(opts) else {
        return;
    };
    let num = |k: &str| data.get(k).and_then(Value::as_f64).map(|f| f.round() as i32);
    if let (Some(w), Some(h)) = (num("w"), num("h")) {
        let (x, y) = (num("x"), num("y"));
        let flags = if x.is_some() && y.is_some() { SWP_NOZORDER } else { SWP_NOZORDER | SWP_NOMOVE };
        unsafe {
            let _ = SetWindowPos(hwnd, None, x.unwrap_or(0), y.unwrap_or(0), w, h, flags);
        }
    }
    with(|u| u.extras.insert(wid, Extra { hwnd, webview: None }));
}

fn raise_extra(wid: &str) {
    if let Some(h) = with(|u| u.extras.get(wid).map(|e| e.hwnd)).flatten() {
        unsafe {
            let _ = ShowWindow(h, if IsIconic(h).as_bool() { SW_RESTORE } else { SW_SHOW });
            let _ = SetForegroundWindow(h);
        }
    }
}

/// The window op on `h` (the main window or the pop-out that asked).
pub(super) fn native_op(op: NativeOp, h: Option<HWND>) -> Value {
    let Some(h) = h else {
        return Value::Null;
    };
    unsafe {
        match op {
            NativeOp::GetWindowBounds => {
                let mut r = RECT::default();
                let _ = GetWindowRect(h, &mut r);
                let scale = GetDpiForWindow(h).max(96) as f64 / 96.0;
                json!({"x": r.left, "y": r.top, "width": r.right - r.left, "height": r.bottom - r.top, "scale": scale})
            }
            NativeOp::SetWindowBounds { x, y, width, height } => {
                let _ = SetWindowPos(h, None, x, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE);
                Value::Null
            }
            NativeOp::UsesNativeWindowChrome => json!(true),
            NativeOp::BeginMove => json!(window::win::begin_drag(h)),
            NativeOp::BeginResize { edge, double_click } => json!(window::win::begin_resize(h, &edge, double_click)),
            NativeOp::ShowMenu => json!(window::win::show_menu(h)),
            NativeOp::IsMaximized => json!(IsZoomed(h).as_bool()),
            NativeOp::ToggleMaximize => {
                let _ = ShowWindow(h, if IsZoomed(h).as_bool() { SW_RESTORE } else { SW_MAXIMIZE });
                json!(IsZoomed(h).as_bool())
            }
            NativeOp::Minimize => {
                let _ = ShowWindow(h, SW_MINIMIZE);
                Value::Null
            }
            NativeOp::Hide => {
                let _ = ShowWindow(h, SW_HIDE);
                Value::Null
            }
        }
    }
}

pub(super) fn create_msg_window() -> windows::core::Result<HWND> {
    unsafe {
        let instance: HINSTANCE = GetModuleHandleW(None)?.into();
        let class = w!("DuckyLauncherWake");
        let wc = WNDCLASSW { lpfnWndProc: Some(msg_proc), hInstance: instance, lpszClassName: class, ..Default::default() };
        RegisterClassW(&wc);
        CreateWindowExW(Default::default(), class, PCWSTR::null(), Default::default(), 0, 0, 0, 0, Some(HWND_MESSAGE), None, Some(instance), None)
    }
}

unsafe extern "system" fn msg_proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    if msg == WM_WAKE {
        drain_backend();
        return LRESULT(0);
    }
    DefWindowProcW(hwnd, msg, wp, lp)
}

/// The main window (dark at once), its splash child, saved bounds; WebView2 starts on
/// about:blank so the shim is in place before the real page loads.
pub(super) fn create_main() -> windows::core::Result<HWND> {
    let (title, show_splash) = with(|u| (u.title.clone(), u.splash.visible())).unwrap_or_default();
    let opts = window::win::WindowOptions {
        title,
        width: bounds::DEFAULT_WIDTH,
        height: bounds::DEFAULT_HEIGHT,
        profile_dir: window::profile_dir(None, |k| std::env::var(k).ok()),
        url: "about:blank".into(),
        debug_port: window::debug_port_from_env(std::env::var(window::config::DEBUG_PORT_ENV).ok().as_deref()),
        hit: Default::default(),
        on_painted: Some(Box::new(|_ok, hwnd| {
            if let Some(wv) = window::win::webview(hwnd) {
                let _ = on_page_host(wv);
            }
        })),
        on_webview_error: None,
        quit_on_close: true,
    };
    let hwnd = window::win::create(opts)?;
    unsafe {
        let saved = bounds::get_bounds(&bounds::default_path(), bounds::MAIN_KEY);
        let b = bounds::initial_bounds(saved, &bounds::work_areas());
        let _ = SetWindowPos(hwnd, None, b.x, b.y, b.width, b.height, SWP_NOZORDER | SWP_NOACTIVATE);
        let prev = SetWindowLongPtrW(hwnd, GWLP_WNDPROC, main_proc as *const () as isize);
        let splash = if show_splash { create_splash(hwnd).ok() } else { None };
        with(|u| {
            u.main = Some(hwnd);
            u.main_prev_proc = prev;
            u.splash_hwnd = splash;
            u.window_ms = ms_since(u.t0);
        });
    }
    Ok(hwnd)
}

unsafe extern "system" fn main_proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    match msg {
        WM_SIZE => {
            if let Some(s) = with(|u| u.splash_hwnd).flatten() {
                let mut rc = RECT::default();
                let _ = GetClientRect(hwnd, &mut rc);
                let _ = MoveWindow(s, 0, 0, rc.right, rc.bottom, true);
            }
        }
        WM_CLOSE => save_bounds(hwnd),
        WM_DESTROY => {
            with(|u| {
                u.main = None;
                u.webview = None;
            });
        }
        _ => {}
    }
    let prev = with(|u| u.main_prev_proc).unwrap_or(0);
    // SAFETY: `prev` is the window crate's WNDPROC that SetWindowLongPtrW returned.
    let prev: WNDPROC = std::mem::transmute::<isize, WNDPROC>(prev);
    CallWindowProcW(prev, hwnd, msg, wp, lp)
}

fn save_bounds(hwnd: HWND) {
    unsafe {
        if IsZoomed(hwnd).as_bool() || IsIconic(hwnd).as_bool() {
            return;
        }
        let mut r = RECT::default();
        if GetWindowRect(hwnd, &mut r).is_ok() {
            let b = bounds::Bounds { x: r.left, y: r.top, width: r.right - r.left, height: r.bottom - r.top };
            let _ = bounds::save_bounds(&bounds::default_path(), bounds::MAIN_KEY, b);
        }
    }
}

fn create_splash(parent: HWND) -> windows::core::Result<HWND> {
    unsafe {
        let instance: HINSTANCE = GetModuleHandleW(None)?.into();
        let class = w!("DuckyLauncherSplash");
        let wc = WNDCLASSW { lpfnWndProc: Some(splash_proc), hInstance: instance, lpszClassName: class, ..Default::default() };
        RegisterClassW(&wc);
        let mut rc = RECT::default();
        let _ = GetClientRect(parent, &mut rc);
        CreateWindowExW(
            Default::default(),
            class,
            PCWSTR::null(),
            WS_CHILD | WS_VISIBLE,
            0,
            0,
            rc.right,
            rc.bottom,
            Some(parent),
            None,
            Some(instance),
            None,
        )
    }
}

unsafe extern "system" fn splash_proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    match msg {
        WM_ERASEBKGND => LRESULT(1),
        WM_PAINT => {
            let mut ps = PAINTSTRUCT::default();
            let hdc = BeginPaint(hwnd, &mut ps);
            let mut rc = RECT::default();
            let _ = GetClientRect(hwnd, &mut rc);
            splash::paint(hdc.0 as _, rc.right, rc.bottom, GetDpiForWindow(hwnd));
            let _ = EndPaint(hwnd, &ps);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wp, lp),
    }
}

pub(super) fn show_main() {
    let main = with(|u| u.main).flatten();
    let h = match main {
        Some(h) => h,
        None => match create_main() {
            Ok(h) => h,
            Err(_) => return,
        },
    };
    unsafe {
        let _ = ShowWindow(h, if IsIconic(h).as_bool() { SW_RESTORE } else { SW_SHOW });
        let _ = SetForegroundWindow(h);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn extra(n: usize) -> Extra {
        Extra { hwnd: HWND(n as _), webview: None }
    }

    #[test]
    fn open_twice_raises_dead_windows_reopen_close_unknown_is_a_no_op() {
        let mut r = Registry::default();
        assert!(r.wants_new("f1", |_| true));
        r.insert("f1", extra(1));
        // Open again while it lives: raise, keep the same window.
        assert!(!r.wants_new("f1", |_| true));
        assert_eq!(r.get("f1").map(|e| e.hwnd), Some(HWND(1 as _)));
        // Closed by the person: a new one.
        assert!(r.wants_new("f1", |_| false));
        assert!(r.get("f1").is_none());
        r.insert("f1", extra(2));
        assert!(r.remove("nope").is_none());
        assert_eq!(r.remove("f1").map(|e| e.hwnd), Some(HWND(2 as _)));
        assert!(r.remove("f1").is_none());
    }
}
