//! The Linux tray: Windows' menu, and the no-tray fallback when no tray host is on the bus.
#![cfg(target_os = "linux")]

use tray::sni::{close_action, menu_items, spawn, CloseAction};
use tray::MenuAction;

#[test]
fn same_menu_as_windows() {
    assert_eq!(menu_items(), vec![("Open UEFN Ducky", MenuAction::Open), ("Exit all", MenuAction::ExitAll)]);
}

#[test]
fn no_tray_host_means_close_minimises() {
    // No session bus here: nothing hosts a tray icon.
    std::env::set_var("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent/ducky-test-bus");
    let shown = spawn(|_| {}).is_ok();
    assert!(!shown, "a tray icon with no bus to show it on");
    assert_eq!(close_action(shown), CloseAction::Minimise);
    assert_eq!(close_action(true), CloseAction::HideToTray);
}
