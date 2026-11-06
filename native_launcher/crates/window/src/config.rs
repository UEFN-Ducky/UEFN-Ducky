//! Profile folder, start URL and browser arguments, matching the pywebview window.

use std::fmt;
use std::path::PathBuf;

/// `%LOCALAPPDATA%/<this>` is Ducky's app data folder (`frontend/app_paths.py`).
pub const APP_DATA_DIR_NAME: &str = "UEFN-Ducky";
/// WebView2 user data folder name under the app data folder (`webview_app.py` storage_path).
pub const WEBVIEW_DIR_NAME: &str = "webview";
/// Not-yet-rendered regions paint the dark shell color (#0a0a0a), as in `webview_app.py`.
pub const DEFAULT_BACKGROUND_COLOR: &str = "FF0A0A0A";
/// `webview_app.py` sets this as the default WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS.
pub const DEFAULT_ENV_BROWSER_ARGS: &str =
    "--auto-select-desktop-capture-source=creen --disable-features=WebRtcHideLocalIpsWithMdns";
/// pywebview's edgechromium backend always puts this in the environment options.
pub const PYWEBVIEW_OPTIONS_ARGS: &str = "--disable-features=ElasticOverscroll";
/// Env var that opens a DevTools port, as in `webview_app.py`.
pub const DEBUG_PORT_ENV: &str = "UEFN_DUCKY_WEBVIEW_DEBUG_PORT";

/// The WebView2 profile: the backend's hello `profile_dir` when it sent one, else the
/// same folder `webview_app.py` computes (`default_app_data_dir() / "webview"`).
pub fn profile_dir(hello_profile_dir: Option<&str>, env: impl Fn(&str) -> Option<String>) -> PathBuf {
    match hello_profile_dir.map(str::trim) {
        Some(p) if !p.is_empty() => PathBuf::from(p),
        _ => fallback_profile_dir(env),
    }
}

/// `default_app_data_dir() / "webview"` from `frontend/settings.py`.
pub fn fallback_profile_dir(env: impl Fn(&str) -> Option<String>) -> PathBuf {
    let get = |k: &str| env(k).map(|v| v.trim().to_string()).filter(|v| !v.is_empty());
    if let Some(base) = get("LOCALAPPDATA").or_else(|| get("APPDATA")) {
        return PathBuf::from(base).join(APP_DATA_DIR_NAME).join(WEBVIEW_DIR_NAME);
    }
    let home = get("USERPROFILE").or_else(|| get("HOME")).unwrap_or_else(|| ".".into());
    PathBuf::from(home).join(".uefn-ducky").join(WEBVIEW_DIR_NAME)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UrlError {
    /// Only the backend's own loopback server is loaded (same rule as `win_frameless.py`).
    NotLoopback(String),
    BadPort(String),
}

impl fmt::Display for UrlError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            UrlError::NotLoopback(o) => write!(f, "origin is not the local backend: {o}"),
            UrlError::BadPort(o) => write!(f, "origin has no valid port: {o}"),
        }
    }
}

impl std::error::Error for UrlError {}

/// The page to open: the hello `origin` (`http://127.0.0.1:N`), with a trailing `/`.
pub fn start_url(origin: &str) -> Result<String, UrlError> {
    let origin = origin.trim().trim_end_matches('/');
    let rest = origin
        .strip_prefix("http://127.0.0.1:")
        .or_else(|| origin.strip_prefix("http://localhost:"))
        .ok_or_else(|| UrlError::NotLoopback(origin.to_string()))?;
    match rest.parse::<u16>() {
        Ok(p) if p > 0 && !rest.starts_with('+') => Ok(format!("{origin}/")),
        _ => Err(UrlError::BadPort(origin.to_string())),
    }
}

/// `UEFN_DUCKY_WEBVIEW_DEBUG_PORT`: digits in 1024..=65535, else no debug port.
pub fn debug_port_from_env(value: Option<&str>) -> Option<u16> {
    let v = value?.trim();
    if v.is_empty() || !v.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    v.parse::<u16>().ok().filter(|p| *p >= 1024)
}

/// AdditionalBrowserArguments on the environment options, as pywebview builds them.
pub fn options_browser_args(debug_port: Option<u16>) -> String {
    let mut args = PYWEBVIEW_OPTIONS_ARGS.to_string();
    if let Some(port) = debug_port {
        args.push_str(&format!(" --remote-debugging-port={port}"));
    }
    args
}

/// Env vars `webview_app.py` sets with `os.environ.setdefault` before WebView2 starts.
/// Apply each only when the variable is not already set.
pub fn browser_env_defaults() -> [(&'static str, &'static str); 2] {
    [
        ("WEBVIEW2_DEFAULT_BACKGROUND_COLOR", DEFAULT_BACKGROUND_COLOR),
        ("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", DEFAULT_ENV_BROWSER_ARGS),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn env(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let m: HashMap<String, String> =
            pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        move |k| m.get(k).cloned()
    }

    #[test]
    fn hello_profile_dir_wins() {
        let e = env(&[("LOCALAPPDATA", r"C:\L")]);
        assert_eq!(profile_dir(Some(r"D:\prof"), e), PathBuf::from(r"D:\prof"));
    }

    #[test]
    fn empty_hello_profile_falls_back_to_localappdata() {
        let e = env(&[("LOCALAPPDATA", r"C:\L"), ("APPDATA", r"C:\R")]);
        let want = PathBuf::from(r"C:\L").join("UEFN-Ducky").join("webview");
        assert_eq!(profile_dir(Some("  "), &e), want);
        assert_eq!(profile_dir(None, &e), want);
    }

    #[test]
    fn fallback_uses_appdata_then_home() {
        let e = env(&[("APPDATA", r"C:\R")]);
        assert_eq!(fallback_profile_dir(e), PathBuf::from(r"C:\R").join("UEFN-Ducky").join("webview"));
        let e = env(&[("USERPROFILE", r"C:\U")]);
        assert_eq!(fallback_profile_dir(e), PathBuf::from(r"C:\U").join(".uefn-ducky").join("webview"));
    }

    #[test]
    fn start_url_is_hello_origin() {
        assert_eq!(start_url("http://127.0.0.1:8765").unwrap(), "http://127.0.0.1:8765/");
        assert_eq!(start_url("http://127.0.0.1:8765/").unwrap(), "http://127.0.0.1:8765/");
        assert_eq!(start_url("http://localhost:9000").unwrap(), "http://localhost:9000/");
    }

    #[test]
    fn start_url_refuses_other_hosts_and_bad_ports() {
        assert!(matches!(start_url("https://evil.example:1"), Err(UrlError::NotLoopback(_))));
        assert!(matches!(start_url("http://127.0.0.1.evil:1"), Err(UrlError::NotLoopback(_))));
        assert!(matches!(start_url("http://127.0.0.1:1@evil"), Err(UrlError::BadPort(_))));
        assert!(matches!(start_url("http://127.0.0.1:"), Err(UrlError::BadPort(_))));
        assert!(matches!(start_url("http://127.0.0.1:0"), Err(UrlError::BadPort(_))));
        assert!(matches!(start_url("http://127.0.0.1:70000"), Err(UrlError::BadPort(_))));
        assert!(matches!(start_url("http://127.0.0.1:80/x"), Err(UrlError::BadPort(_))));
    }

    #[test]
    fn browser_args_match_pywebview() {
        assert_eq!(options_browser_args(None), "--disable-features=ElasticOverscroll");
        assert_eq!(
            options_browser_args(Some(9333)),
            "--disable-features=ElasticOverscroll --remote-debugging-port=9333"
        );
        let d = browser_env_defaults();
        assert_eq!(d[0], ("WEBVIEW2_DEFAULT_BACKGROUND_COLOR", "FF0A0A0A"));
        assert_eq!(
            d[1].1,
            "--auto-select-desktop-capture-source=creen --disable-features=WebRtcHideLocalIpsWithMdns"
        );
    }

    #[test]
    fn debug_port_rules() {
        assert_eq!(debug_port_from_env(Some("9333")), Some(9333));
        assert_eq!(debug_port_from_env(Some("80")), None);
        assert_eq!(debug_port_from_env(Some("-1")), None);
        assert_eq!(debug_port_from_env(Some("99999")), None);
        assert_eq!(debug_port_from_env(Some("")), None);
        assert_eq!(debug_port_from_env(None), None);
    }
}
