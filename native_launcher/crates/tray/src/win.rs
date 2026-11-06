//! Shell_NotifyIcon on a hidden window of its own. Create it on the launcher's UI thread
//! (the one pumping messages); its events come back through the callback on that thread.

use std::cell::RefCell;
use std::ffi::c_void;
use std::ptr::{null, null_mut};
use std::rc::Rc;

use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::Shell::{
    SetCurrentProcessExplicitAppUserModelID, Shell_NotifyIconW, NOTIFYICONDATAW,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DestroyWindow,
    GetCursorPos, LoadImageW, PostMessageW, RegisterClassW, RegisterWindowMessageW,
    SetForegroundWindow, SetMenuDefaultItem, TrackPopupMenu, WNDCLASSW,
};

use crate::{
    info_flags, wide_fixed, MenuAction, Toast, ToastTarget, TrayEvent, MENU, MESSAGE_UNITS,
    NIN_BALLOONUSERCLICK, TIP_UNITS, TITLE_UNITS,
};

const NIM_ADD: u32 = 0;
const NIM_MODIFY: u32 = 1;
const NIM_DELETE: u32 = 2;
const NIF_MESSAGE: u32 = 0x1;
const NIF_ICON: u32 = 0x2;
const NIF_TIP: u32 = 0x4;
const NIF_INFO: u32 = 0x10;
const WM_NULL: u32 = 0;
const WM_CONTEXTMENU: u32 = 0x7B;
const WM_LBUTTONUP: u32 = 0x202;
const WM_RBUTTONUP: u32 = 0x205;
const WM_APP: u32 = 0x8000;
const WM_TRAY: u32 = WM_APP + 0x41;
const MF_STRING: u32 = 0;
const TPM_RIGHTBUTTON: u32 = 0x2;
const TPM_NONOTIFY: u32 = 0x80;
const TPM_RETURNCMD: u32 = 0x100;
const IMAGE_ICON: u32 = 1;
const LR_LOADFROMFILE: u32 = 0x10;
const LR_DEFAULTSIZE: u32 = 0x40;
const ICON_ID: u32 = 1;

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn copy_into(dst: &mut [u16], s: &str) {
    let src = wide_fixed(s, dst.len());
    dst.copy_from_slice(&src);
}

/// `SetCurrentProcessExplicitAppUserModelID(AUMID)`: call once before any window shows.
pub fn set_process_aumid() -> bool {
    let id = wide(crate::AUMID);
    unsafe { SetCurrentProcessExplicitAppUserModelID(id.as_ptr()) >= 0 }
}

/// An `.ico` file as an HICON (null when it can't be read).
pub fn load_icon_file(path: &std::path::Path) -> *mut c_void {
    let p = wide(&path.to_string_lossy());
    unsafe { LoadImageW(null_mut(), p.as_ptr(), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE) }
}

struct Inner {
    hwnd: HWND,
    icon: RefCell<*mut c_void>,
    tip: RefCell<String>,
    last_target: RefCell<Option<ToastTarget>>,
    on_event: Box<dyn Fn(TrayEvent)>,
    taskbar_created: u32,
}

thread_local! {
    static TRAYS: RefCell<Vec<Rc<Inner>>> = const { RefCell::new(Vec::new()) };
}

fn find(hwnd: HWND) -> Option<Rc<Inner>> {
    TRAYS.with(|t| t.borrow().iter().find(|i| i.hwnd == hwnd).cloned())
}

pub struct Tray {
    inner: Rc<Inner>,
}

impl Inner {
    fn data(&self) -> NOTIFYICONDATAW {
        let mut nid: NOTIFYICONDATAW = unsafe { std::mem::zeroed() };
        nid.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
        nid.hWnd = self.hwnd;
        nid.uID = ICON_ID;
        nid
    }

    fn add(&self) -> bool {
        let mut nid = self.data();
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
        nid.uCallbackMessage = WM_TRAY;
        nid.hIcon = *self.icon.borrow();
        copy_into(&mut nid.szTip, &self.tip.borrow());
        unsafe { Shell_NotifyIconW(NIM_ADD, &nid) != 0 }
    }

    fn show_menu(&self) {
        unsafe {
            let menu = CreatePopupMenu();
            if menu.is_null() {
                return;
            }
            for item in MENU {
                let label = wide(item.label);
                AppendMenuW(menu, MF_STRING, item.action.command_id(), label.as_ptr());
                if item.default {
                    SetMenuDefaultItem(menu, item.action.command_id() as u32, 0);
                }
            }
            let mut pt = POINT { x: 0, y: 0 };
            GetCursorPos(&mut pt);
            // Without this the menu never closes when the user clicks elsewhere.
            SetForegroundWindow(self.hwnd);
            let cmd = TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pt.x,
                pt.y,
                0,
                self.hwnd,
                null(),
            );
            PostMessageW(self.hwnd, WM_NULL, 0, 0);
            DestroyMenu(menu);
            if let Some(action) = MenuAction::from_command_id(cmd as usize) {
                (self.on_event)(TrayEvent::Menu(action));
            }
        }
    }
}

unsafe extern "system" fn wndproc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if let Some(inner) = find(hwnd) {
        if msg == WM_TRAY {
            match (lparam as u32) & 0xFFFF {
                WM_LBUTTONUP => (inner.on_event)(TrayEvent::Menu(crate::default_action())),
                WM_RBUTTONUP | WM_CONTEXTMENU => inner.show_menu(),
                NIN_BALLOONUSERCLICK => {
                    let target = inner.last_target.borrow().clone();
                    (inner.on_event)(TrayEvent::ToastClicked(target));
                }
                _ => {}
            }
            return 0;
        }
        if inner.taskbar_created != 0 && msg == inner.taskbar_created {
            // Explorer restarted: the icon is gone until it is added again.
            inner.add();
            return 0;
        }
    }
    DefWindowProcW(hwnd, msg, wparam, lparam)
}

impl Tray {
    /// Add the icon. `icon` is an HICON (see [`load_icon_file`]); `on_event` gets menu
    /// picks, left clicks (the default item) and toast clicks.
    pub fn new(icon: *mut c_void, tooltip: &str, on_event: Box<dyn Fn(TrayEvent)>) -> Option<Tray> {
        unsafe {
            let instance = GetModuleHandleW(null());
            let class = wide("DuckyLauncherTray");
            let wc = WNDCLASSW {
                style: 0,
                lpfnWndProc: Some(wndproc),
                cbClsExtra: 0,
                cbWndExtra: 0,
                hInstance: instance,
                hIcon: null_mut(),
                hCursor: null_mut(),
                hbrBackground: null_mut(),
                lpszMenuName: null(),
                lpszClassName: class.as_ptr(),
            };
            RegisterClassW(&wc); // 0 on a second tray: the class is already there
            let title = wide(crate::ICON_NAME);
            // A hidden top-level window, not HWND_MESSAGE: only top-level windows hear
            // TaskbarCreated when Explorer restarts.
            let hwnd = CreateWindowExW(
                0,
                class.as_ptr(),
                title.as_ptr(),
                0,
                0,
                0,
                0,
                0,
                null_mut(),
                null_mut(),
                instance,
                null(),
            );
            if hwnd.is_null() {
                return None;
            }
            let created = wide("TaskbarCreated");
            let inner = Rc::new(Inner {
                hwnd,
                icon: RefCell::new(icon),
                tip: RefCell::new(tooltip.to_string()),
                last_target: RefCell::new(None),
                on_event,
                taskbar_created: RegisterWindowMessageW(created.as_ptr()),
            });
            TRAYS.with(|t| t.borrow_mut().push(inner.clone()));
            if !inner.add() {
                TRAYS.with(|t| t.borrow_mut().retain(|i| i.hwnd != hwnd));
                DestroyWindow(hwnd);
                return None;
            }
            Some(Tray { inner })
        }
    }

    /// Show a Windows notification from the icon (one at a time per icon; a click on it
    /// reports this toast's target).
    pub fn toast(&self, toast: &Toast) -> bool {
        *self.inner.last_target.borrow_mut() = toast.target.clone();
        let icon = *self.inner.icon.borrow();
        let mut nid = self.inner.data();
        nid.uFlags = NIF_INFO;
        copy_into(&mut nid.szInfoTitle, &toast.title);
        copy_into(&mut nid.szInfo, &toast.message);
        debug_assert_eq!(nid.szInfoTitle.len(), TITLE_UNITS);
        debug_assert_eq!(nid.szInfo.len(), MESSAGE_UNITS);
        nid.dwInfoFlags = info_flags(!icon.is_null());
        if !icon.is_null() {
            nid.hBalloonIcon = icon;
        }
        unsafe { Shell_NotifyIconW(NIM_MODIFY, &nid) != 0 }
    }

    /// The toast a backend event asks for, if it is one; true when it showed.
    pub fn handle_event(&self, event: &serde_json::Value) -> bool {
        Toast::from_event(event).map(|t| self.toast(&t)).unwrap_or(false)
    }

    /// Swap the icon (connection state) without re-adding it.
    pub fn set_icon(&self, icon: *mut c_void) -> bool {
        *self.inner.icon.borrow_mut() = icon;
        let mut nid = self.inner.data();
        nid.uFlags = NIF_ICON;
        nid.hIcon = icon;
        unsafe { Shell_NotifyIconW(NIM_MODIFY, &nid) != 0 }
    }

    pub fn set_tooltip(&self, tooltip: &str) -> bool {
        *self.inner.tip.borrow_mut() = tooltip.to_string();
        let mut nid = self.inner.data();
        nid.uFlags = NIF_TIP;
        copy_into(&mut nid.szTip, tooltip);
        debug_assert_eq!(nid.szTip.len(), TIP_UNITS);
        unsafe { Shell_NotifyIconW(NIM_MODIFY, &nid) != 0 }
    }
}

impl Drop for Tray {
    fn drop(&mut self) {
        let hwnd = self.inner.hwnd;
        unsafe {
            let nid = self.inner.data();
            Shell_NotifyIconW(NIM_DELETE, &nid);
            TRAYS.with(|t| t.borrow_mut().retain(|i| i.hwnd != hwnd));
            DestroyWindow(hwnd);
        }
    }
}
