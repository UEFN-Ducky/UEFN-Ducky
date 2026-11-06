//! WebView2 runtime version check. A false [`webview2_version_ok`] maps to exit 20.

/// The floor pywebview's WinForms backend checks for (`86.0.622.0`).
pub const MIN_WEBVIEW2_VERSION: &str = "86.0.622.0";

fn parts(v: &str) -> Option<Vec<u64>> {
    // The runtime reports e.g. "130.0.2849.80" or "131.0.2903.5 canary".
    let first = v.split_whitespace().next()?;
    first.split('.').map(|p| p.parse::<u64>().ok()).collect()
}

/// True when `found` is a well-formed version at or above `min` (missing parts are 0).
pub fn version_at_least(found: &str, min: &str) -> bool {
    let (Some(a), Some(b)) = (parts(found), parts(min)) else {
        return false;
    };
    let n = a.len().max(b.len());
    for i in 0..n {
        let (x, y) = (a.get(i).copied().unwrap_or(0), b.get(i).copied().unwrap_or(0));
        if x != y {
            return x > y;
        }
    }
    true
}

/// The installed WebView2 runtime version, or None when it is missing.
#[cfg(windows)]
pub fn webview2_version() -> Option<String> {
    use webview2_com::Microsoft::Web::WebView2::Win32::GetAvailableCoreWebView2BrowserVersionString;
    use windows::core::{PCWSTR, PWSTR};

    let mut info = PWSTR::null();
    unsafe { GetAvailableCoreWebView2BrowserVersionString(PCWSTR::null(), &mut info) }.ok()?;
    if info.is_null() {
        return None;
    }
    let v = webview2_com::take_pwstr(info);
    (!v.trim().is_empty()).then_some(v)
}

#[cfg(not(windows))]
pub fn webview2_version() -> Option<String> {
    None
}

/// WebView2 is installed and new enough. False means the launcher exits with 20.
pub fn webview2_version_ok() -> bool {
    webview2_version().is_some_and(|v| version_at_least(&v, MIN_WEBVIEW2_VERSION))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compares_numerically_not_as_text() {
        assert!(version_at_least("130.0.2849.80", MIN_WEBVIEW2_VERSION));
        assert!(version_at_least("100.0.0.0", "86.0.622.0"));
        assert!(!version_at_least("9.0.0.0", "86.0.622.0"));
        assert!(!version_at_least("86.0.621.99", "86.0.622.0"));
    }

    #[test]
    fn equal_and_short_versions() {
        assert!(version_at_least("86.0.622.0", "86.0.622.0"));
        assert!(version_at_least("86.0.622", "86.0.622.0"));
        assert!(!version_at_least("86", "86.0.622.0"));
    }

    #[test]
    fn channel_suffix_and_garbage() {
        assert!(version_at_least("131.0.2903.5 canary", MIN_WEBVIEW2_VERSION));
        assert!(!version_at_least("", MIN_WEBVIEW2_VERSION));
        assert!(!version_at_least("abc", MIN_WEBVIEW2_VERSION));
        assert!(!version_at_least("130.x.1", MIN_WEBVIEW2_VERSION));
    }

    #[test]
    fn check_maps_to_exit_20_or_version() {
        match crate::check_webview2() {
            Ok(v) => assert!(version_at_least(&v, MIN_WEBVIEW2_VERSION)),
            Err(code) => assert_eq!(code, crate::EXIT_WEBVIEW2),
        }
        assert_eq!(webview2_version_ok(), crate::check_webview2().is_ok());
    }
}
