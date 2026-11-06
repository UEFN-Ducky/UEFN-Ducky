//! Launcher exit codes (the contract `frontend/native_switch.py` reads).

/// Ready, or the launch was handed to a running Ducky.
pub const OK: u8 = 0;
/// WebView2 runtime missing or too old.
pub const WEBVIEW2_MISSING: u8 = 20;
/// The backend's hello carried a protocol outside `[1, 1]`.
pub const PROTOCOL_MISMATCH: u8 = 21;
/// Crash loop: `auto` uses the Python window for a while.
pub const CRASH_LOOP: u8 = 22;
/// No hello from the backend within 15 s.
pub const NO_HELLO: u8 = 23;
/// Any other start error.
pub const START_ERROR: u8 = 24;
