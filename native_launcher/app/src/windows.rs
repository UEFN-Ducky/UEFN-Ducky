//! The launcher's own windows: the main window and its splash, the wake window, the
//! backend's pop-out windows (`window_open` / `window_close` / `window_raise`), and the
//! native window ops the page asks for, and the right-click menu over typed text. A child
//! of `ui` (it reads the UI state).

use std::cell::{Cell, RefCell};
use std::collections::HashMap;

use serde_json::{json, Value};
use shim::NativeOp;
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{take_pwstr, ContextMenuRequestedEventHandler, ExecuteScriptCompletedHandler};
use windows::core::{w, Interface, BOOL, HSTRING, PCWSTR, PWSTR};
use windows::Win32::Foundation::*;
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::HiDpi::GetDpiForWindow;
use windows::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};
use windows::Win32::UI::WindowsAndMessaging::*;

use crate::trim;

use super::{drain_backend, hook_page, ms_since, note_hidden, on_page_host, with, TITLE, WM_WAKE};

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
    // The backend finds this window by this number (pane_host.wid_mark) to put browser panes in it.
    if let Some(mark) = wid.rsplit('-').next().and_then(|hex| isize::from_str_radix(hex, 16).ok()) {
        unsafe {
            let _ = SetPropW(hwnd, w!("DuckyPopoutWid"), Some(HANDLE(mark as *mut _)));
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

// --- the right-click menu over typed text (webview_native_menu.py) ------------------------

/// Browser-page commands that make no sense in Ducky's own text boxes.
const MENU_DROP: &[&str] = &[
    "back", "forward", "reload", "saveAs", "print", "createQrCode", "inspectElement",
    "share", "webCapture", "openLinkInNewWindow", "saveLinkAs", "copyLinkLocation",
];

/// One row of WebView2's menu.
enum MenuRow {
    Command { name: String, label: String, id: i32, enabled: bool },
    Separator,
    Other,
}

/// The rows the page shows, in WebView2's order (`webview_native_menu.menu_items`):
/// commands minus [`MENU_DROP`], `&` accelerators removed, separators between groups only.
fn menu_rows(items: &[MenuRow]) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    for item in items {
        match item {
            MenuRow::Separator => {
                if out.last().is_some_and(|r| r.get("separator").is_none()) {
                    out.push(json!({"separator": true}));
                }
            }
            MenuRow::Command { name, label, id, enabled } if !MENU_DROP.contains(&name.as_str()) => {
                let label = label.replace("&&", "\0").replace('&', "").replace('\0', "&");
                if !label.trim().is_empty() {
                    out.push(json!({"id": id, "label": label, "enabled": enabled}));
                }
            }
            _ => {}
        }
    }
    while out.last().is_some_and(|r| r.get("separator").is_some()) {
        out.pop();
    }
    out
}

thread_local! {
    /// The one menu the page has open: its token, WebView2's event and its deferral.
    static MENU: RefCell<Option<(String, ICoreWebView2ContextMenuRequestedEventArgs, ICoreWebView2Deferral)>> =
        const { RefCell::new(None) };
    static MENU_SEQ: Cell<u64> = const { Cell::new(0) };
}

unsafe fn read_menu(items: &ICoreWebView2ContextMenuItemCollection) -> windows::core::Result<Vec<MenuRow>> {
    let mut n = 0u32;
    items.Count(&mut n)?;
    let mut out = Vec::new();
    for i in 0..n {
        let item = items.GetValueAtIndex(i)?;
        let mut kind = COREWEBVIEW2_CONTEXT_MENU_ITEM_KIND::default();
        item.Kind(&mut kind)?;
        out.push(match kind {
            COREWEBVIEW2_CONTEXT_MENU_ITEM_KIND_SEPARATOR => MenuRow::Separator,
            COREWEBVIEW2_CONTEXT_MENU_ITEM_KIND_COMMAND => {
                let (mut name, mut label) = (PWSTR::null(), PWSTR::null());
                let (mut id, mut enabled) = (0i32, BOOL::default());
                item.Name(&mut name)?;
                item.Label(&mut label)?;
                item.CommandId(&mut id)?;
                item.IsEnabled(&mut enabled)?;
                MenuRow::Command { name: take_pwstr(name), label: take_pwstr(label), id, enabled: enabled.as_bool() }
            }
            _ => MenuRow::Other,
        });
    }
    Ok(out)
}

/// Right-click on text you can edit: WebView2's own menu goes to the page (Ducky's
/// `NativeTextMenu`), the pick comes back as `native_menu_pick` ([`menu_pick`]).
/// `hide_others`: no browser menu (Back, Reload, Inspect) anywhere else.
/// ponytail: no spelling fixes (Python's win_spellcheck); add an ISpellChecker call if asked.
pub(super) fn hook_menu(webview: &ICoreWebView2, hide_others: bool) -> windows::core::Result<()> {
    let wv11: ICoreWebView2_11 = webview.cast()?;
    let handler = ContextMenuRequestedEventHandler::create(Box::new(
        move |sender: Option<ICoreWebView2>, args: Option<ICoreWebView2ContextMenuRequestedEventArgs>| {
            let (Some(page), Some(args)) = (sender, args) else {
                return Ok(());
            };
            unsafe {
                let mut editable = BOOL::default();
                args.ContextMenuTarget()?.IsEditable(&mut editable)?;
                let rows = if editable.as_bool() { menu_rows(&read_menu(&args.MenuItems()?)?) } else { Vec::new() };
                if !rows.iter().any(|r| r.get("id").is_some()) {
                    if hide_others {
                        args.SetHandled(true)?;
                    }
                    return Ok(());
                }
                let deferral = args.GetDeferral()?;
                args.SetHandled(true)?;
                let token = format!("m{}", MENU_SEQ.with(|s| s.replace(s.get() + 1)));
                // One menu at a time: one left open gives WebView2 its event back.
                if let Some((_, _, old)) = MENU.with(|m| m.borrow_mut().replace((token.clone(), args, deferral))) {
                    let _ = old.Complete();
                }
                let payload = json!({"type": "native_menu", "token": token, "items": rows});
                let js = format!("window.__uefnPanelPush && window.__uefnPanelPush({payload})");
                let done = ExecuteScriptCompletedHandler::create(Box::new(|_, _| Ok(())));
                page.ExecuteScript(&HSTRING::from(js), &done)?;
            }
            Ok(())
        },
    ));
    let mut token = Default::default();
    unsafe { wv11.add_ContextMenuRequested(&handler, &mut token) }
}

/// The page's answer to menu `token`: run WebView2's `command_id` (Paste...) or nothing
/// (-1). False when that menu is not the open one.
fn menu_pick(token: &str, command_id: i32) -> bool {
    let open = MENU.with(|m| {
        let mut m = m.borrow_mut();
        if m.as_ref().is_some_and(|(t, ..)| t == token) {
            m.take()
        } else {
            None
        }
    });
    let Some((_, args, deferral)) = open else {
        return false;
    };
    unsafe {
        if command_id >= 0 {
            let _ = args.SetSelectedCommandId(command_id);
        }
        let _ = deferral.Complete();
    }
    true
}

/// The window op on `h` (the main window or the pop-out that asked).
pub(super) fn native_op(op: NativeOp, h: Option<HWND>) -> Value {
    if let NativeOp::MenuPick { token, command_id } = &op {
        return json!({"ok": menu_pick(token, *command_id)});
    }
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
            NativeOp::MenuPick { .. } => Value::Null,
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
        SetTimer(Some(hwnd), TRIM_TIMER, TRIM_EVERY_MS, None);
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

const TRIM_TIMER: usize = 0x7d1;
const TRIM_EVERY_MS: u32 = 60_000;

thread_local! {
    /// The level WebView2 was last given, and the person's last input (tick) with when
    /// it was first seen.
    static TRIM: Cell<(trim::Level, u32, Option<std::time::Instant>)> = const { Cell::new((trim::Level::Normal, 0, None)) };
}

/// Give the main WebView2 the memory level for the window's state now (`hidden`: from
/// WM_SHOWWINDOW, which comes before the change). An older WebView2 is left as it is.
fn retrim(hwnd: HWND, hidden: Option<bool>) {
    let (last, tick, since) = TRIM.get();
    let mut info = LASTINPUTINFO { cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32, dwTime: 0 };
    let now_tick = if unsafe { GetLastInputInfo(&mut info) }.as_bool() { info.dwTime } else { tick };
    let since = match since {
        Some(t) if now_tick == tick => t,
        _ => std::time::Instant::now(),
    };
    let (minimized, hidden) = unsafe { (IsIconic(hwnd).as_bool(), hidden.unwrap_or(!IsWindowVisible(hwnd).as_bool())) };
    let want = trim::level(minimized, hidden, since.elapsed().as_secs());
    let mut applied = last;
    if want != last {
        if let Some(wv) = with(|u| u.webview.clone()).flatten().and_then(|w| w.cast::<ICoreWebView2_19>().ok()) {
            let target = match want {
                trim::Level::Low => COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_LOW,
                trim::Level::Normal => COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_NORMAL,
            };
            if unsafe { wv.SetMemoryUsageTargetLevel(target) }.is_ok() {
                applied = want;
            }
        }
    }
    TRIM.set((applied, now_tick, Some(since)));
}

unsafe extern "system" fn main_proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    match msg {
        WM_SIZE => {
            if let Some(s) = with(|u| u.splash_hwnd).flatten() {
                let mut rc = RECT::default();
                let _ = GetClientRect(hwnd, &mut rc);
                let _ = MoveWindow(s, 0, 0, rc.right, rc.bottom, true);
            }
            retrim(hwnd, None);
        }
        WM_SHOWWINDOW => {
            retrim(hwnd, Some(wp.0 == 0));
            note_hidden(wp.0 == 0);
        }
        WM_TIMER if wp.0 == TRIM_TIMER => {
            retrim(hwnd, None);
            // Whatever a missed show or hide left wrong is put right within a minute.
            note_hidden(!IsWindowVisible(hwnd).as_bool());
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
        let splash = CreateWindowExW(
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
        )?;
        // Repaints the "Starting: <stage> (N s)" line once a second while the backend starts.
        SetTimer(Some(splash), STATUS_TIMER, 1000, None);
        Ok(splash)
    }
}

const STATUS_TIMER: usize = 0x7d2;

unsafe extern "system" fn splash_proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    match msg {
        WM_ERASEBKGND => LRESULT(1),
        WM_PAINT => {
            let mut ps = PAINTSTRUCT::default();
            let hdc = BeginPaint(hwnd, &mut ps);
            let mut rc = RECT::default();
            let _ = GetClientRect(hwnd, &mut rc);
            splash::paint(hdc.0 as _, rc.right, rc.bottom, GetDpiForWindow(hwnd));
            if let Some(text) = supervisor::starting_status() {
                let mut wide: Vec<u16> = text.encode_utf16().collect();
                let top = rc.bottom * 2 / 3;
                let mut line = RECT { left: 0, top, right: rc.right, bottom: top + 40 };
                SetBkMode(hdc, TRANSPARENT);
                SetTextColor(hdc, COLORREF(0x0080_8080));
                DrawTextW(hdc, &mut wide, &mut line, DT_CENTER | DT_SINGLELINE | DT_VCENTER);
            }
            let _ = EndPaint(hwnd, &ps);
            LRESULT(0)
        }
        WM_TIMER if wp.0 == STATUS_TIMER => {
            let _ = InvalidateRect(Some(hwnd), None, false);
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
    // A window made just now was shown before its messages came here.
    note_hidden(false);
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

    fn cmd(name: &str, label: &str, id: i32) -> MenuRow {
        MenuRow::Command { name: name.into(), label: label.into(), id, enabled: true }
    }

    #[test]
    fn menu_rows_match_python_menu_items() {
        let rows = menu_rows(&[
            MenuRow::Separator,
            cmd("back", "&Back", 1),
            cmd("cut", "Cu&t", 50151),
            MenuRow::Separator,
            MenuRow::Separator,
            MenuRow::Other,
            cmd("paste", "&Paste && go", 50153),
            cmd("blank", "&", 9),
            cmd("inspectElement", "Inspect", 2),
            MenuRow::Separator,
        ]);
        assert_eq!(
            Value::Array(rows),
            json!([
                {"id": 50151, "label": "Cut", "enabled": true},
                {"separator": true},
                {"id": 50153, "label": "Paste & go", "enabled": true},
            ])
        );
        assert!(menu_rows(&[MenuRow::Separator, cmd("reload", "Reload", 3)]).is_empty());
    }

    #[test]
    fn a_pick_for_no_open_menu_is_refused() {
        assert!(!menu_pick("m0", 5));
        assert_eq!(native_op(NativeOp::MenuPick { token: "nope".into(), command_id: -1 }, None), json!({"ok": false}));
    }
}
