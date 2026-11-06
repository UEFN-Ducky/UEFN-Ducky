//! Ducky-Launcher's window: a frameless Win32 window hosting WebView2 on the same
//! profile folder, origin and browser arguments as the pywebview window
//! (`frontend/ui_web/webview_app.py`, `frontend/ui_web/win_frameless.py`).
//!
//! The pure parts (profile path, start URL, browser args, version compare, hit-test)
//! build and test on any OS; the Win32 host is Windows-only.

pub mod config;
pub mod hittest;
pub mod version;

#[cfg(windows)]
pub mod win;

pub use config::{
    browser_env_defaults, debug_port_from_env, fallback_profile_dir, options_browser_args,
    profile_dir, start_url, UrlError,
};
pub use hittest::{hit_test, min_track_size, resize_border_px, HitConfig, Rect};
pub use version::{version_at_least, webview2_version, webview2_version_ok, MIN_WEBVIEW2_VERSION};

/// Launcher exit code when WebView2 is missing or older than [`MIN_WEBVIEW2_VERSION`].
pub const EXIT_WEBVIEW2: i32 = 20;

/// `Ok(version)` when WebView2 is usable, else `Err(EXIT_WEBVIEW2)`.
pub fn check_webview2() -> Result<String, i32> {
    match webview2_version() {
        Some(v) if version_at_least(&v, MIN_WEBVIEW2_VERSION) => Ok(v),
        _ => Err(EXIT_WEBVIEW2),
    }
}
