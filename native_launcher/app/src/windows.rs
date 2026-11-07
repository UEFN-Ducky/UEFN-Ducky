//! The launcher's own windows: the main window and its splash, the wake window, and the
//! native window ops the page asks for. A child of `ui` (it reads the UI state).

use serde_json::{json, Value};
use shim::NativeOp;
use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::*;
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::HiDpi::GetDpiForWindow;
use windows::Win32::UI::WindowsAndMessaging::*;

use super::{drain_backend, ms_since, on_page_host, with, WM_WAKE};

pub(super) fn native_op(op: NativeOp) -> Value {
    let Some(h) = with(|u| u.main).flatten() else {
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
        on_painted: Some(Box::new(|_ok| {
            if let Some(wv) = window::win::webview() {
                let _ = on_page_host(wv);
            }
        })),
        on_webview_error: None,
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
