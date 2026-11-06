//! Pure WM_NCHITTEST logic for the frameless window, the same regions as
//! `win_frameless.py`: an OS-sized resize band on every edge (SM_CYSIZEFRAME +
//! SM_CXPADDEDBORDER, 4 + 4 px at 96 DPI), no resize band while maximized, an
//! optional caption strip and an optional maximize button (Windows 11 snap layouts
//! open when the maximize button area answers HTMAXBUTTON).

pub const HTNOWHERE: u32 = 0;
pub const HTCLIENT: u32 = 1;
pub const HTCAPTION: u32 = 2;
pub const HTMAXBUTTON: u32 = 9;
pub const HTLEFT: u32 = 10;
pub const HTRIGHT: u32 = 11;
pub const HTTOP: u32 = 12;
pub const HTTOPLEFT: u32 = 13;
pub const HTTOPRIGHT: u32 = 14;
pub const HTBOTTOM: u32 = 15;
pub const HTBOTTOMLEFT: u32 = 16;
pub const HTBOTTOMRIGHT: u32 = 17;

/// Logical minimum window size (`window_layout.py` MAIN_MIN_WIDTH / MAIN_MIN_HEIGHT).
pub const MIN_WIDTH: i32 = 700;
pub const MIN_HEIGHT: i32 = 480;
/// SM_CYSIZEFRAME + SM_CXPADDEDBORDER at 96 DPI.
pub const RESIZE_BORDER_96: i32 = 8;

/// A rectangle in logical px, relative to the window's top-left.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Rect {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

impl Rect {
    fn scaled(&self, dpi: u32) -> Rect {
        Rect {
            left: scale(self.left, dpi),
            top: scale(self.top, dpi),
            right: scale(self.right, dpi),
            bottom: scale(self.bottom, dpi),
        }
    }

    fn contains(&self, x: i32, y: i32) -> bool {
        x >= self.left && x < self.right && y >= self.top && y < self.bottom
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct HitConfig {
    /// The window's DPI (96 = 100%, 144 = 150%).
    pub dpi: u32,
    pub maximized: bool,
    /// Logical height of a native drag strip at the top; 0 = the page owns dragging
    /// (pywebview's DOM drag region), which is how Ducky ships.
    pub caption_height: i32,
    /// Logical maximize button rect; answers HTMAXBUTTON for snap layouts.
    pub max_button: Option<Rect>,
}

impl Default for HitConfig {
    fn default() -> Self {
        HitConfig { dpi: 96, maximized: false, caption_height: 0, max_button: None }
    }
}

fn dpi_or_96(dpi: u32) -> u32 {
    // `_hwnd_scale` never goes below 1.0.
    dpi.max(96)
}

/// Logical px to physical px at `dpi`, rounded like MulDiv.
pub fn scale(v: i32, dpi: u32) -> i32 {
    let d = dpi_or_96(dpi) as i64;
    let v = v as i64 * d;
    (if v >= 0 { (v + 48) / 96 } else { (v - 48) / 96 }) as i32
}

/// Physical px of the resize band on each edge (`_resize_border_px`).
pub fn resize_border_px(dpi: u32) -> i32 {
    scale(RESIZE_BORDER_96, dpi).max(1)
}

/// Physical minimum track size for WM_GETMINMAXINFO.
pub fn min_track_size(dpi: u32) -> (i32, i32) {
    (scale(MIN_WIDTH, dpi), scale(MIN_HEIGHT, dpi))
}

/// Hit-test a point at (`x`, `y`) physical px from the window's top-left corner, in a
/// window `width` x `height` physical px.
pub fn hit_test(x: i32, y: i32, width: i32, height: i32, cfg: &HitConfig) -> u32 {
    if x < 0 || y < 0 || x >= width || y >= height {
        return HTNOWHERE;
    }
    if !cfg.maximized {
        let b = resize_border_px(cfg.dpi);
        let (left, right) = (x < b, x >= width - b);
        if y < b {
            return if left { HTTOPLEFT } else if right { HTTOPRIGHT } else { HTTOP };
        }
        if y >= height - b {
            return if left { HTBOTTOMLEFT } else if right { HTBOTTOMRIGHT } else { HTBOTTOM };
        }
        if left {
            return HTLEFT;
        }
        if right {
            return HTRIGHT;
        }
    }
    if let Some(r) = cfg.max_button {
        if r.scaled(cfg.dpi).contains(x, y) {
            return HTMAXBUTTON;
        }
    }
    if y < scale(cfg.caption_height.max(0), cfg.dpi) {
        return HTCAPTION;
    }
    HTCLIENT
}

#[cfg(test)]
mod tests {
    use super::*;

    const W: i32 = 1200;
    const H: i32 = 800;

    fn cfg(dpi: u32) -> HitConfig {
        HitConfig { dpi, caption_height: 32, ..Default::default() }
    }

    #[test]
    fn border_and_min_size_scale_with_dpi() {
        assert_eq!(resize_border_px(96), 8);
        assert_eq!(resize_border_px(144), 12);
        assert_eq!(resize_border_px(72), 8); // never below 100%
        assert_eq!(min_track_size(96), (700, 480));
        assert_eq!(min_track_size(144), (1050, 720));
    }

    #[test]
    fn edges_and_corners_at_100() {
        let c = cfg(96);
        assert_eq!(hit_test(0, 0, W, H, &c), HTTOPLEFT);
        assert_eq!(hit_test(W - 1, 0, W, H, &c), HTTOPRIGHT);
        assert_eq!(hit_test(0, H - 1, W, H, &c), HTBOTTOMLEFT);
        assert_eq!(hit_test(W - 1, H - 1, W, H, &c), HTBOTTOMRIGHT);
        assert_eq!(hit_test(600, 7, W, H, &c), HTTOP);
        assert_eq!(hit_test(600, H - 8, W, H, &c), HTBOTTOM);
        assert_eq!(hit_test(7, 400, W, H, &c), HTLEFT);
        assert_eq!(hit_test(W - 8, 400, W, H, &c), HTRIGHT);
        // Just inside the band.
        assert_eq!(hit_test(600, 8, W, H, &c), HTCAPTION);
        assert_eq!(hit_test(8, 400, W, H, &c), HTCLIENT);
    }

    #[test]
    fn edges_and_caption_at_150() {
        let c = cfg(144);
        assert_eq!(hit_test(600, 11, W, H, &c), HTTOP);
        assert_eq!(hit_test(600, 12, W, H, &c), HTCAPTION);
        assert_eq!(hit_test(11, 400, W, H, &c), HTLEFT);
        assert_eq!(hit_test(12, 400, W, H, &c), HTCLIENT);
        assert_eq!(hit_test(W - 12, 400, W, H, &c), HTRIGHT);
        assert_eq!(hit_test(600, 47, W, H, &c), HTCAPTION); // 32 * 1.5 = 48
        assert_eq!(hit_test(600, 48, W, H, &c), HTCLIENT);
    }

    #[test]
    fn maximized_has_no_resize_band() {
        let c = HitConfig { maximized: true, ..cfg(144) };
        assert_eq!(hit_test(0, 0, W, H, &c), HTCAPTION);
        assert_eq!(hit_test(0, 400, W, H, &c), HTCLIENT);
        assert_eq!(hit_test(W - 1, H - 1, W, H, &c), HTCLIENT);
    }

    #[test]
    fn page_owned_drag_is_client_and_outside_is_nowhere() {
        let c = HitConfig { caption_height: 0, ..cfg(96) };
        assert_eq!(hit_test(600, 20, W, H, &c), HTCLIENT);
        assert_eq!(hit_test(-1, 20, W, H, &c), HTNOWHERE);
        assert_eq!(hit_test(600, H, W, H, &c), HTNOWHERE);
    }

    #[test]
    fn max_button_answers_snap_layouts() {
        let btn = Rect { left: 1100, top: 0, right: 1150, bottom: 32 };
        let c = HitConfig { max_button: Some(btn), ..cfg(96) };
        assert_eq!(hit_test(1120, 16, 1300, H, &c), HTMAXBUTTON);
        assert_eq!(hit_test(1120, 4, 1300, H, &c), HTTOP); // resize band wins
        let c150 = HitConfig { dpi: 144, ..c };
        assert_eq!(hit_test(1700, 30, 1900, H, &c150), HTMAXBUTTON); // 1650..1725 x 0..48
        assert_eq!(hit_test(1120, 30, 1900, H, &c150), HTCAPTION);
    }
}
