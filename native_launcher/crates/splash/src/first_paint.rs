//! What the window shows first: the cached page at once, or the splash.
//!
//! With the web UI in WebView2's cache the launcher navigates straight away, before the
//! backend has said hello: the launcher's proxy holds every API call until the backend
//! is ready, so the page paints from cache while Python is still starting. Without a
//! cache there is nothing to paint until the backend serves the page, so the splash stays.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FirstPaint {
    /// Navigate the WebView now; API calls wait in the proxy for the backend.
    Navigate,
    /// Keep the logo splash; navigate when the backend is ready.
    KeepSplash,
}

pub fn first_paint(cache_present: bool, backend_ready: bool) -> FirstPaint {
    if cache_present || backend_ready {
        FirstPaint::Navigate
    } else {
        FirstPaint::KeepSplash
    }
}
