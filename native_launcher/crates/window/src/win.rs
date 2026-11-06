//! The Win32 window and its WebView2, ported from `win_frameless.py`:
//! WS_OVERLAPPEDWINDOW kept (taskbar animation, Aero Snap, snap layouts), only the top
//! caption stripped in WM_NCCALCSIZE, maximized client = monitor work area, DPI-scaled
//! minimum track size, and the dark #0a0a0a background until the page paints.

use std::cell::RefCell;
use std::path::PathBuf;

use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::*;
use windows::core::{w, Interface, BOOL, HSTRING, PCWSTR};
use windows::Win32::Foundation::*;
use windows::Win32::Graphics::Dwm::DwmSetWindowAttribute;
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::System::Com::{CoInitializeEx, COINIT_APARTMENTTHREADED};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::HiDpi::*;
use windows::Win32::UI::Input::KeyboardAndMouse::ReleaseCapture;
use windows::Win32::UI::WindowsAndMessaging::*;

use crate::config::{browser_env_defaults, options_browser_args};
use crate::hittest::{hit_test, min_track_size, HitConfig, HTCAPTION, HTLEFT};

const CLASS_NAME: PCWSTR = w!("DuckyLauncherWindow");
const DWMWA_BORDER_COLOR: u32 = 34;
const DWMWA_CAPTION_COLOR: u32 = 35;
const DWMWA_COLOR_NONE: u32 = 0xFFFF_FFFE;
const BG: COREWEBVIEW2_COLOR = COREWEBVIEW2_COLOR { A: 255, R: 10, G: 10, B: 10 };

pub struct WindowOptions {
    pub title: String,
    /// Logical size; the window is centred on the primary work area.
    pub width: i32,
    pub height: i32,
    /// WebView2 user data folder (`config::profile_dir`).
    pub profile_dir: PathBuf,
    /// Page to open (`config::start_url`).
    pub url: String,
    pub debug_port: Option<u16>,
    pub hit: HitConfig,
    /// The page finished its first navigation (ok = it loaded).
    pub on_painted: Option<Box<dyn FnOnce(bool)>>,
    /// WebView2 could not start (the launcher maps this to a start error).
    pub on_webview_error: Option<Box<dyn FnOnce(windows::core::Error)>>,
}

struct State {
    hit: HitConfig,
    controller: Option<ICoreWebView2Controller>,
    webview: Option<ICoreWebView2>,
    on_painted: Option<Box<dyn FnOnce(bool)>>,
    /// Last WM_SIZE kind, to re-frame only on maximize/restore changes.
    size_kind: u32,
}

thread_local! {
    static STATE: RefCell<Option<State>> = const { RefCell::new(None) };
}

fn with_state<R>(f: impl FnOnce(&mut State) -> R) -> Option<R> {
    STATE.with(|s| s.borrow_mut().as_mut().map(f))
}

/// Set the env defaults `webview_app.py` sets, only where the variable is unset.
pub fn apply_browser_env_defaults() {
    for (k, v) in browser_env_defaults() {
        if std::env::var_os(k).is_none() {
            std::env::set_var(k, v);
        }
    }
}

/// Create and show the window now (dark, before WebView2 is up), then start WebView2
/// on it. Run [`run_message_loop`] on this same thread afterwards.
pub fn create(mut opts: WindowOptions) -> windows::core::Result<HWND> {
    unsafe {
        let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        let instance: HINSTANCE = GetModuleHandleW(None)?.into();
        let wc = WNDCLASSEXW {
            cbSize: std::mem::size_of::<WNDCLASSEXW>() as u32,
            style: CS_HREDRAW | CS_VREDRAW,
            lpfnWndProc: Some(wndproc),
            hInstance: instance,
            hCursor: LoadCursorW(None, IDC_ARROW)?,
            hbrBackground: CreateSolidBrush(COLORREF(0x000A_0A0A)),
            lpszClassName: CLASS_NAME,
            ..Default::default()
        };
        RegisterClassExW(&wc);

        let dpi = GetDpiForSystem().max(96);
        let w = crate::hittest::scale(opts.width, dpi);
        let h = crate::hittest::scale(opts.height, dpi);
        let mut work = RECT::default();
        let _ = SystemParametersInfoW(
            SPI_GETWORKAREA,
            0,
            Some(&mut work as *mut RECT as *mut _),
            SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS(0),
        );
        let x = work.left + ((work.right - work.left - w) / 2).max(0);
        let y = work.top + ((work.bottom - work.top - h) / 2).max(0);

        STATE.with(|s| {
            *s.borrow_mut() = Some(State {
                hit: opts.hit,
                controller: None,
                webview: None,
                on_painted: opts.on_painted.take(),
                size_kind: SIZE_RESTORED,
            })
        });

        let title = HSTRING::from(opts.title.as_str());
        let hwnd = CreateWindowExW(
            WINDOW_EX_STYLE::default(),
            CLASS_NAME,
            &title,
            WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
            x,
            y,
            w,
            h,
            None,
            None,
            Some(instance),
            None,
        )?;
        let none = DWMWA_COLOR_NONE;
        for attr in [DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR] {
            let _ = DwmSetWindowAttribute(
                hwnd,
                windows::Win32::Graphics::Dwm::DWMWINDOWATTRIBUTE(attr as i32),
                &none as *const u32 as *const _,
                4,
            );
        }
        force_frame_changed(hwnd);
        let _ = ShowWindow(hwnd, SW_SHOW);
        let _ = UpdateWindow(hwnd);

        if let Err(e) = start_webview(hwnd, &opts) {
            if let Some(cb) = opts.on_webview_error.take() {
                cb(e.clone());
            }
            return Err(e);
        }
        Ok(hwnd)
    }
}

fn start_webview(hwnd: HWND, opts: &WindowOptions) -> windows::core::Result<()> {
    apply_browser_env_defaults();
    let options = CoreWebView2EnvironmentOptions::default();
    unsafe { options.set_additional_browser_arguments(options_browser_args(opts.debug_port)) };
    let options: ICoreWebView2EnvironmentOptions = options.into();
    let user_data = HSTRING::from(opts.profile_dir.as_os_str());
    let url = opts.url.clone();

    let env_done = CreateCoreWebView2EnvironmentCompletedHandler::create(Box::new(
        move |res: windows::core::Result<()>, env: Option<ICoreWebView2Environment>| {
            res?;
            let env = env.ok_or_else(|| windows::core::Error::from(E_POINTER))?;
            let ctrl_done = CreateCoreWebView2ControllerCompletedHandler::create(Box::new(
                move |res: windows::core::Result<()>, ctrl: Option<ICoreWebView2Controller>| {
                    res?;
                    let ctrl = ctrl.ok_or_else(|| windows::core::Error::from(E_POINTER))?;
                    attach_controller(hwnd, ctrl, &url)
                },
            ));
            unsafe { env.CreateCoreWebView2Controller(hwnd, &ctrl_done) }
        },
    ));
    unsafe { CreateCoreWebView2EnvironmentWithOptions(PCWSTR::null(), &user_data, &options, &env_done) }
}

fn attach_controller(hwnd: HWND, ctrl: ICoreWebView2Controller, url: &str) -> windows::core::Result<()> {
    unsafe {
        if let Ok(c2) = ctrl.cast::<ICoreWebView2Controller2>() {
            let _ = c2.SetDefaultBackgroundColor(BG);
        }
        let mut rc = RECT::default();
        let _ = GetClientRect(hwnd, &mut rc);
        ctrl.SetBounds(rc)?;
        ctrl.SetIsVisible(true)?;
        let webview = ctrl.CoreWebView2()?;
        let nav = NavigationCompletedEventHandler::create(Box::new(
            move |_sender, args: Option<ICoreWebView2NavigationCompletedEventArgs>| {
                let mut ok = BOOL::default();
                if let Some(a) = args {
                    let _ = a.IsSuccess(&mut ok);
                }
                if let Some(Some(cb)) = with_state(|s| s.on_painted.take()) {
                    cb(ok.as_bool());
                }
                Ok(())
            },
        ));
        let mut token = Default::default();
        webview.add_NavigationCompleted(&nav, &mut token)?;
        webview.Navigate(&HSTRING::from(url))?;
        with_state(|s| {
            s.controller = Some(ctrl);
            s.webview = Some(webview);
        });
    }
    Ok(())
}

/// Pump messages until the window closes. Returns the WM_QUIT code.
pub fn run_message_loop() -> i32 {
    let mut msg = MSG::default();
    unsafe {
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    msg.wParam.0 as i32
}

/// The page's WebView2, once created (for the bridge and ExecuteScript).
pub fn webview() -> Option<ICoreWebView2> {
    with_state(|s| s.webview.clone()).flatten()
}

/// Change the hit-test regions (e.g. the page reports its caption / max button).
pub fn set_hit_config(hit: HitConfig) {
    with_state(|s| s.hit = hit);
}

/// Hand a caption drag to the OS (`begin_native_drag` in `win_frameless.py`).
pub fn begin_drag(hwnd: HWND) -> bool {
    post_nc_button(hwnd, HTCAPTION)
}

/// Hand an edge resize to the OS; `edge` is n/s/e/w/nw/ne/sw/se.
pub fn begin_resize(hwnd: HWND, edge: &str) -> bool {
    let wmsz = match edge.to_ascii_lowercase().as_str() {
        "w" => 1,
        "e" => 2,
        "n" => 3,
        "nw" => 4,
        "ne" => 5,
        "s" => 6,
        "sw" => 7,
        "se" => 8,
        _ => return false,
    };
    if unsafe { IsZoomed(hwnd).as_bool() } {
        return false;
    }
    post_nc_button(hwnd, HTLEFT + wmsz - 1)
}

fn post_nc_button(hwnd: HWND, ht: u32) -> bool {
    unsafe {
        let mut pt = POINT::default();
        if GetCursorPos(&mut pt).is_err() {
            return false;
        }
        let lparam = (((pt.y as u32) & 0xFFFF) << 16) | ((pt.x as u32) & 0xFFFF);
        let _ = ReleaseCapture();
        PostMessageW(Some(hwnd), WM_NCLBUTTONDOWN, WPARAM(ht as usize), LPARAM(lparam as isize)).is_ok()
    }
}

fn force_frame_changed(hwnd: HWND) {
    unsafe {
        let _ = SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER,
        );
    }
}

fn dpi_of(hwnd: HWND) -> u32 {
    unsafe { GetDpiForWindow(hwnd) }.max(96)
}

fn resize_webview(hwnd: HWND) {
    let mut rc = RECT::default();
    unsafe {
        let _ = GetClientRect(hwnd, &mut rc);
    }
    with_state(|s| {
        if let Some(c) = &s.controller {
            unsafe {
                let _ = c.SetBounds(rc);
            }
        }
    });
}

unsafe extern "system" fn wndproc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match msg {
        WM_NCCALCSIZE if lparam.0 != 0 => {
            // Both forms start with the proposed RECT; strip only the top caption.
            let rect = &mut *(lparam.0 as *mut RECT);
            if IsZoomed(hwnd).as_bool() {
                let hmon = MonitorFromRect(rect, MONITOR_DEFAULTTONEAREST);
                let mut mi = MONITORINFO { cbSize: std::mem::size_of::<MONITORINFO>() as u32, ..Default::default() };
                if GetMonitorInfoW(hmon, &mut mi).as_bool() {
                    *rect = mi.rcWork;
                    return LRESULT(0);
                }
            } else {
                let top = rect.top;
                DefWindowProcW(hwnd, msg, wparam, lparam);
                rect.top = top;
                return LRESULT(0);
            }
        }
        WM_NCHITTEST => {
            let sx = (lparam.0 & 0xFFFF) as i16 as i32;
            let sy = ((lparam.0 >> 16) & 0xFFFF) as i16 as i32;
            let mut wr = RECT::default();
            if GetWindowRect(hwnd, &mut wr).is_ok() {
                let mut hit = with_state(|s| s.hit).unwrap_or_default();
                hit.dpi = dpi_of(hwnd);
                hit.maximized = IsZoomed(hwnd).as_bool();
                let ht = hit_test(sx - wr.left, sy - wr.top, wr.right - wr.left, wr.bottom - wr.top, &hit);
                return LRESULT(ht as isize);
            }
        }
        WM_GETMINMAXINFO => {
            let r = DefWindowProcW(hwnd, msg, wparam, lparam);
            let info = &mut *(lparam.0 as *mut MINMAXINFO);
            let (w, h) = min_track_size(dpi_of(hwnd));
            info.ptMinTrackSize.x = w;
            info.ptMinTrackSize.y = h;
            return r;
        }
        WM_DPICHANGED => {
            let r = &*(lparam.0 as *const RECT);
            let _ = SetWindowPos(
                hwnd,
                None,
                r.left,
                r.top,
                r.right - r.left,
                r.bottom - r.top,
                SWP_NOZORDER | SWP_NOACTIVATE,
            );
            return LRESULT(0);
        }
        WM_NCACTIVATE => {
            // Keep DWM from repainting a caption we no longer have (same as pywebview).
            return DefWindowProcW(hwnd, msg, wparam, LPARAM(-1));
        }
        WM_SIZE => {
            if wparam.0 as u32 != SIZE_MINIMIZED {
                resize_webview(hwnd);
            }
            let kind = wparam.0 as u32;
            let prev = with_state(|s| std::mem::replace(&mut s.size_kind, kind)).unwrap_or(kind);
            if prev != kind && (kind == SIZE_MAXIMIZED || kind == SIZE_RESTORED) {
                // Re-run WM_NCCALCSIZE after maximize/restore.
                force_frame_changed(hwnd);
            }
            return LRESULT(0);
        }
        WM_MOVE | WM_MOVING => {
            with_state(|s| {
                if let Some(c) = &s.controller {
                    let _ = c.NotifyParentWindowPositionChanged();
                }
            });
        }
        WM_DESTROY => {
            with_state(|s| {
                if let Some(c) = s.controller.take() {
                    let _ = c.Close();
                }
                s.webview = None;
            });
            PostQuitMessage(0);
            return LRESULT(0);
        }
        _ => {}
    }
    DefWindowProcW(hwnd, msg, wparam, lparam)
}
