//! The tray must match the Python app: read its sources and compare.

use std::path::PathBuf;

fn frontend(rel: &str) -> String {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../ducky_app/frontend").join(rel);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

#[test]
fn aumid_equals_python_constant() {
    let src = frontend("ui_web/win_frameless.py");
    let wanted = format!("def ensure_app_user_model_id(app_id: str = \"{}\")", tray::AUMID);
    assert!(src.contains(&wanted), "win_frameless.py no longer uses AUMID {:?}", tray::AUMID);
}

#[test]
fn menu_equals_python_menu() {
    let src = frontend("tray_icon.py");
    let items: Vec<&str> = src.lines().filter(|l| l.contains("pystray.MenuItem(")).map(str::trim).collect();
    assert_eq!(items.len(), tray::MENU.len(), "menu size differs from tray_icon.py: {items:?}");
    for (line, item) in items.iter().zip(tray::MENU.iter()) {
        assert!(line.contains(&format!("\"{}\"", item.label)), "{line} != {:?}", item.label);
        assert_eq!(line.contains("default=True"), item.default, "default flag differs on {line}");
    }
    assert!(src.contains(&format!("\"{}\"", tray::ICON_NAME)));
}

#[test]
fn toast_constants_equal_python() {
    let src = frontend("tray_icon.py");
    for (name, value) in [
        ("_NIIF_USER", tray::NIIF_USER),
        ("_NIIF_LARGE_ICON", tray::NIIF_LARGE_ICON),
        ("_NIIF_RESPECT_QUIET_TIME", tray::NIIF_RESPECT_QUIET_TIME),
        ("_NIN_BALLOONUSERCLICK", tray::NIN_BALLOONUSERCLICK),
    ] {
        assert!(src.contains(&format!("{name} = 0x{value:x}")), "{name} differs from tray_icon.py");
    }
    assert!(src.contains(&format!("or \"{}\"", tray::DEFAULT_TITLE)));
}
