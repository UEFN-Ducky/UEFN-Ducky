//! The tray icon: the same menu, AppUserModelID and toasts as the Python app
//! (`frontend/tray_icon.py`, `frontend/notifications.py`, `ui_web/win_frameless.py`).
//!
//! The model (menu, toast text, flags) is plain Rust and tested everywhere; the
//! Shell_NotifyIcon part lives in [`win`] and runs on the launcher's UI thread.

use serde_json::Value;

/// `ensure_app_user_model_id(app_id="UEFN.Ducky")` in `win_frameless.py`: one taskbar
/// entry and one toast sender for every Ducky window.
pub const AUMID: &str = "UEFN.Ducky";
/// The tray icon's name in `TrayIconController` (pystray `Icon("uefn_ducky", ...)`).
pub const ICON_NAME: &str = "uefn_ducky";
/// A toast with no title says this (`tray_icon.py` and `notifications.alert`).
pub const DEFAULT_TITLE: &str = "UEFN Ducky";

/// What a menu item does.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MenuAction {
    /// Show (or create) the main window. Also the left-click action.
    Open,
    /// Close every Ducky window and the backend.
    ExitAll,
}

impl MenuAction {
    /// The menu command id handed to Windows (never 0: TrackPopupMenu returns 0 for "none").
    pub fn command_id(self) -> usize {
        match self {
            MenuAction::Open => 1,
            MenuAction::ExitAll => 2,
        }
    }

    pub fn from_command_id(id: usize) -> Option<MenuAction> {
        MENU.iter().map(|item| item.action).find(|a| a.command_id() == id)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MenuItem {
    pub label: &'static str,
    pub action: MenuAction,
    /// Bold in the menu and run on a left click (pystray `default=True`).
    pub default: bool,
}

/// The tray menu, in order: the same items as `TrayIconController.__init__`.
pub const MENU: [MenuItem; 2] = [
    MenuItem { label: "Open UEFN Ducky", action: MenuAction::Open, default: true },
    MenuItem { label: "Exit all", action: MenuAction::ExitAll, default: false },
];

/// The action a left click runs.
pub fn default_action() -> MenuAction {
    MENU.iter().find(|item| item.default).map(|item| item.action).unwrap_or(MenuAction::Open)
}

/// What the tray reports back to the launcher.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum TrayEvent {
    Menu(MenuAction),
    /// The latest toast was clicked; its target (chat or workflow), if it had one.
    ToastClicked(Option<ToastTarget>),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ToastTarget {
    pub kind: String,
    pub id: String,
}

/// One Windows notification, from a backend `{"t":"event","name":"toast","data":{...}}`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Toast {
    pub title: String,
    pub message: String,
    pub target: Option<ToastTarget>,
}

// Shell_NotifyIcon balloon flags, the same set `TrayIconController.toast` sends.
pub const NIIF_USER: u32 = 0x4;
pub const NIIF_LARGE_ICON: u32 = 0x20;
pub const NIIF_RESPECT_QUIET_TIME: u32 = 0x80;
/// A balloon click comes back to the icon's window as this (WM_USER + 5).
pub const NIN_BALLOONUSERCLICK: u32 = 0x405;
/// `szInfoTitle` / `szInfo` / `szTip` sizes in UTF-16 units, terminator included.
pub const TITLE_UNITS: usize = 64;
pub const MESSAGE_UNITS: usize = 256;
pub const TIP_UNITS: usize = 128;

/// Quiet hours and Focus are respected; with the icon's own handle it is a large icon.
pub fn info_flags(has_icon: bool) -> u32 {
    let mut flags = NIIF_RESPECT_QUIET_TIME;
    if has_icon {
        flags |= NIIF_USER | NIIF_LARGE_ICON;
    }
    flags
}

fn text(data: &Value, keys: &[&str]) -> String {
    keys.iter()
        .filter_map(|k| data.get(*k).and_then(Value::as_str))
        .map(str::trim)
        .find(|s| !s.is_empty())
        .unwrap_or("")
        .to_string()
}

impl Toast {
    /// The toast an event asks for, or None when the event is not a toast. `data` takes the
    /// fields `notifications._queue_toast` uses (`title`, `text`, `kind`, `id`); `message`
    /// and `body` are accepted for `text`. Unknown fields are ignored.
    pub fn from_event(event: &Value) -> Option<Toast> {
        if event.get("t").and_then(Value::as_str) != Some("event")
            || event.get("name").and_then(Value::as_str) != Some("toast")
        {
            return None;
        }
        let empty = Value::Null;
        let data = event.get("data").unwrap_or(&empty);
        let title = text(data, &["title"]);
        let message = text(data, &["text", "message", "body"]);
        let kind = text(data, &["kind"]);
        let id = text(data, &["id"]);
        Some(Toast {
            title: if title.is_empty() { DEFAULT_TITLE.to_string() } else { title },
            message: if message.is_empty() { " ".to_string() } else { message },
            target: if kind.is_empty() || id.is_empty() { None } else { Some(ToastTarget { kind, id }) },
        })
    }
}

/// `s` as a NUL-terminated UTF-16 buffer of `units` (clipped, never splitting a pair).
pub fn wide_fixed(s: &str, units: usize) -> Vec<u16> {
    let mut out = vec![0u16; units];
    let mut n = 0;
    for ch in s.chars() {
        let mut buf = [0u16; 2];
        let enc = ch.encode_utf16(&mut buf);
        if n + enc.len() > units.saturating_sub(1) {
            break;
        }
        out[n..n + enc.len()].copy_from_slice(enc);
        n += enc.len();
    }
    out
}

#[cfg(windows)]
pub mod win;

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn menu_ids_round_trip() {
        for item in MENU {
            assert_eq!(MenuAction::from_command_id(item.action.command_id()), Some(item.action));
            assert_ne!(item.action.command_id(), 0);
        }
        assert_eq!(MenuAction::from_command_id(0), None);
        assert_eq!(default_action(), MenuAction::Open);
    }

    #[test]
    fn toast_from_event() {
        let ev = json!({"t":"event","name":"toast","data":{"title":"Chat","text":"Finished.","kind":"chat","id":"c1","extra":1}});
        let toast = Toast::from_event(&ev).unwrap();
        assert_eq!(toast.title, "Chat");
        assert_eq!(toast.message, "Finished.");
        assert_eq!(toast.target, Some(ToastTarget { kind: "chat".into(), id: "c1".into() }));

        let bare = Toast::from_event(&json!({"t":"event","name":"toast","data":{}})).unwrap();
        assert_eq!(bare.title, DEFAULT_TITLE);
        assert_eq!(bare.message, " ");
        assert_eq!(bare.target, None);

        assert!(Toast::from_event(&json!({"t":"event","name":"title","data":{}})).is_none());
        assert!(Toast::from_event(&json!({"t":"result","name":"toast"})).is_none());
    }

    #[test]
    fn flags_match_python() {
        assert_eq!(info_flags(false), 0x80);
        assert_eq!(info_flags(true), 0x80 | 0x4 | 0x20);
    }

    #[test]
    fn wide_fixed_clips_and_terminates() {
        let w = wide_fixed("abc", 4);
        assert_eq!(w, vec![97, 98, 99, 0]);
        let w = wide_fixed("abcdef", 4);
        assert_eq!(w, vec![97, 98, 99, 0]);
        // A surrogate pair that would not fit whole is dropped, not split.
        let w = wide_fixed("ab\u{1F986}", 4);
        assert_eq!(w, vec![97, 98, 0, 0]);
        assert_eq!(wide_fixed(&"x".repeat(500), TITLE_UNITS).len(), TITLE_UNITS);
    }
}
