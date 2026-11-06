//! The built `Ducky-Launcher` binary: `--version` prints the app version and opens no window.

use std::process::Command;

#[test]
fn version_prints_the_app_version_and_exits_0() {
    let out = Command::new(env!("CARGO_BIN_EXE_Ducky-Launcher"))
        .arg("--version")
        .output()
        .expect("run Ducky-Launcher --version");
    assert!(out.status.success(), "exit {:?}", out.status.code());
    let text = String::from_utf8(out.stdout).expect("utf-8");
    assert_eq!(text.trim(), ducky_launcher::APP_VERSION);
    assert!(text.trim().split('.').count() >= 3, "app version looks like 1.2.x: {text:?}");
}

#[test]
fn version_matches_the_python_app_version() {
    let init = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../ducky_app/frontend/__init__.py");
    let Ok(text) = std::fs::read_to_string(init) else { return };
    if std::env::var("DUCKY_APP_VERSION").map(|v| !v.is_empty()).unwrap_or(false) {
        return; // the release build pinned it
    }
    assert!(text.contains(&format!("__version__ = \"{}\"", ducky_launcher::APP_VERSION)));
}
