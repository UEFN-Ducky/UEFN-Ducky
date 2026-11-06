//! The launcher's splash: the duck logo centred on the theme background, painted in the
//! main window's client area from the first frame until the page has painted.
//!
//! The logo is `assets/OnlineMCPIcon.png` (the one `frontend/early_splash.py` shows),
//! pre-scaled to 220 px and composited onto the theme colour at build time
//! (`assets/logo_220.bgra`, top-down BGRA), so drawing it needs no image decoder.

/// Theme background, the same `#0a0a0a` as `webview_app.py` / critical.css.
pub const BACKGROUND_RGB: (u8, u8, u8) = (0x0A, 0x0A, 0x0A);
/// Logo edge at 96 DPI, the same 220 px as `early_splash.py`.
pub const LOGO_PX: i32 = 220;
/// Smallest margin kept around the logo at 96 DPI.
pub const PAD_PX: i32 = 10;
/// The autostart flag (`frontend/autostart.py` `BACKGROUND_FLAG`).
pub const BACKGROUND_FLAG: &str = "--background";
/// Set by an update hand-over that starts hidden (`frontend/autostart.py` `START_HIDDEN_ENV`).
pub const START_HIDDEN_ENV: &str = "UEFN_DUCKY_START_HIDDEN";

/// 220x220 top-down BGRA pixels, already on the theme background.
pub static LOGO_BGRA: &[u8] = include_bytes!("../../../assets/logo_220.bgra");

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

impl Rect {
    pub fn width(&self) -> i32 {
        self.right - self.left
    }
    pub fn height(&self) -> i32 {
        self.bottom - self.top
    }
}

/// No splash when started hidden: `--background` (sign-in start) or the hidden hand-over.
pub fn should_show<S: AsRef<str>>(argv: &[S], start_hidden_env: Option<&str>) -> bool {
    let background = argv.iter().skip(1).any(|a| a.as_ref() == BACKGROUND_FLAG);
    !(background || start_hidden_env == Some("1"))
}

/// Where the logo goes in a `client_w` x `client_h` client area at `dpi`: centred,
/// 220 px scaled by DPI, shrunk to keep the pad in a small window, never negative.
pub fn logo_rect(client_w: i32, client_h: i32, dpi: u32) -> Rect {
    let dpi = if dpi == 0 { 96 } else { dpi } as i64;
    let want = (LOGO_PX as i64 * dpi / 96) as i32;
    let pad = (PAD_PX as i64 * dpi / 96) as i32;
    let cw = client_w.max(0);
    let ch = client_h.max(0);
    let size = want.min(cw - 2 * pad).min(ch - 2 * pad).max(0);
    let left = (cw - size) / 2;
    let top = (ch - size) / 2;
    Rect { left, top, right: left + size, bottom: top + size }
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
    /// stops painting the logo (WebView2 covers the client area from here on).
    pub fn on_painted(&mut self) -> bool {
        std::mem::replace(&mut self.visible, false)
    }
}

/// Paint the splash into a window DC: theme background plus the logo. Call from
/// WM_PAINT (between BeginPaint/EndPaint) while `Splash::visible()`.
///
/// # Safety
/// `hdc` must be a valid device context for the window being painted.
#[cfg(windows)]
pub unsafe fn paint(hdc: windows_sys::Win32::Graphics::Gdi::HDC, client_w: i32, client_h: i32, dpi: u32) {
    use windows_sys::Win32::Foundation::RECT;
    use windows_sys::Win32::Graphics::Gdi::{
        CreateSolidBrush, DeleteObject, FillRect, SetBrushOrgEx, SetStretchBltMode, StretchDIBits, BITMAPINFO,
        BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, HALFTONE, SRCCOPY,
    };

    let (r, g, b) = BACKGROUND_RGB;
    let brush = CreateSolidBrush(r as u32 | (g as u32) << 8 | (b as u32) << 16);
    let all = RECT { left: 0, top: 0, right: client_w, bottom: client_h };
    FillRect(hdc, &all, brush);
    DeleteObject(brush);

    let dst = logo_rect(client_w, client_h, dpi);
    if dst.width() <= 0 || LOGO_BGRA.len() != (LOGO_PX * LOGO_PX * 4) as usize {
        return;
    }
    let mut bmi: BITMAPINFO = std::mem::zeroed();
    bmi.bmiHeader = BITMAPINFOHEADER {
        biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
        biWidth: LOGO_PX,
        biHeight: -LOGO_PX, // top-down
        biPlanes: 1,
        biBitCount: 32,
        biCompression: BI_RGB,
        ..std::mem::zeroed()
    };
    if dst.width() != LOGO_PX {
        SetStretchBltMode(hdc, HALFTONE);
        SetBrushOrgEx(hdc, 0, 0, std::ptr::null_mut());
    }
    StretchDIBits(
        hdc,
        dst.left,
        dst.top,
        dst.width(),
        dst.height(),
        0,
        0,
        LOGO_PX,
        LOGO_PX,
        LOGO_BGRA.as_ptr().cast(),
        &bmi,
        DIB_RGB_COLORS,
        SRCCOPY,
    );
}
