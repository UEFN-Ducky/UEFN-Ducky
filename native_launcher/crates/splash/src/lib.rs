//! The launcher's splash: the theme background, painted in the main window's client area
//! from the first frame until the page has painted (the page then shows the app's outline).
//! No logo: the window is Ducky's own from its first frame.

mod first_paint;
pub use first_paint::{first_paint, FirstPaint};

/// Theme background, the same `#0a0a0a` as `webview_app.py` / critical.css.
pub const BACKGROUND_RGB: (u8, u8, u8) = (0x0A, 0x0A, 0x0A);
/// The autostart flag (`frontend/autostart.py` `BACKGROUND_FLAG`).
pub const BACKGROUND_FLAG: &str = "--background";
/// Set by an update hand-over that starts hidden (`frontend/autostart.py` `START_HIDDEN_ENV`).
pub const START_HIDDEN_ENV: &str = "UEFN_DUCKY_START_HIDDEN";

/// No splash when started hidden: `--background` (sign-in start) or the hidden hand-over.
pub fn should_show<S: AsRef<str>>(argv: &[S], start_hidden_env: Option<&str>) -> bool {
    let background = argv.iter().skip(1).any(|a| a.as_ref() == BACKGROUND_FLAG);
    !(background || start_hidden_env == Some("1"))
}

/// Splash lifetime: shown from the first frame (unless started hidden), gone for good
/// once the page has painted.
#[derive(Debug)]
pub struct Splash {
    visible: bool,
}

impl Splash {
    pub fn new(show: bool) -> Self {
        Splash { visible: show }
    }

    /// From the process's own argv and environment.
    pub fn from_env() -> Self {
        let argv: Vec<String> = std::env::args().collect();
        let hidden = std::env::var(START_HIDDEN_ENV).ok();
        Splash::new(should_show(&argv, hidden.as_deref()))
    }

    pub fn visible(&self) -> bool {
        self.visible
    }

    /// The page has painted. True only the first time while visible: the caller then
    /// stops painting the splash (WebView2 covers the client area from here on).
    pub fn on_painted(&mut self) -> bool {
        std::mem::replace(&mut self.visible, false)
    }
}

/// Paint the splash into a window DC: the theme background only, no logo, so the window
/// reads as Ducky's own (dark, like the page about to cover it) instead of a logo screen.
/// Call from WM_PAINT (between BeginPaint/EndPaint) while `Splash::visible()`.
///
/// # Safety
/// `hdc` must be a valid device context for the window being painted.
#[cfg(windows)]
pub unsafe fn paint(hdc: windows_sys::Win32::Graphics::Gdi::HDC, client_w: i32, client_h: i32, _dpi: u32) {
    use windows_sys::Win32::Foundation::RECT;
    use windows_sys::Win32::Graphics::Gdi::{CreateSolidBrush, DeleteObject, FillRect};

    let (r, g, b) = BACKGROUND_RGB;
    let brush = CreateSolidBrush(r as u32 | (g as u32) << 8 | (b as u32) << 16);
    let all = RECT { left: 0, top: 0, right: client_w, bottom: client_h };
    FillRect(hdc, &all, brush);
    DeleteObject(brush);
}
