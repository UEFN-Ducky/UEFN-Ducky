//! Window bounds, shared with Python's `frontend/ui_web/window_bounds.py`.
//!
//! Same file (`%LOCALAPPDATA%/UEFN-Ducky/window_bounds.json`), same shape
//! (`{"<key>": {"x", "y", "width", "height"}}`), same keys (`main`, `focus:<id>`).
//! No new keys: whatever Python wrote reads back here, and the other way round.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

/// The main window's key, as `webview_app.py` uses it.
pub const MAIN_KEY: &str = "main";
/// Python's `webview.create_window` defaults when nothing is saved.
pub const DEFAULT_WIDTH: i32 = 920;
pub const DEFAULT_HEIGHT: i32 = 640;
/// Python's `min_size=(700, 480)`.
pub const MIN_WIDTH: i32 = 700;
pub const MIN_HEIGHT: i32 = 480;
/// Python's `save_bounds` ignores anything smaller than this.
pub const SAVE_MIN_WIDTH: i32 = 200;
pub const SAVE_MIN_HEIGHT: i32 = 150;
/// How much of the top edge must be on a screen so the window can still be dragged.
pub const GRAB_WIDTH: i32 = 100;
pub const GRAB_HEIGHT: i32 = 32;

const FIELDS: [&str; 4] = ["x", "y", "width", "height"];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Bounds {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}

/// A monitor work area in screen pixels (right/bottom exclusive).
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
    fn overlap(&self, o: &Rect) -> (i32, i32) {
        let w = self.right.min(o.right) - self.left.max(o.left);
        let h = self.bottom.min(o.bottom) - self.top.max(o.top);
        (w.max(0), h.max(0))
    }
}

impl Bounds {
    pub fn rect(&self) -> Rect {
        Rect { left: self.x, top: self.y, right: self.x + self.width, bottom: self.y + self.height }
    }
}

/// `%LOCALAPPDATA%/UEFN-Ducky/window_bounds.json`, resolved per call like Python's `_path()`.
#[cfg(windows)]
pub fn default_path() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA").unwrap_or_default();
    PathBuf::from(base).join("UEFN-Ducky").join("window_bounds.json")
}

/// `$XDG_DATA_HOME/UEFN-Ducky/window_bounds.json` (default `~/.local/share`), the same app
/// data folder as `ducky_instance::Config`.
#[cfg(not(windows))]
pub fn default_path() -> PathBuf {
    let env = |k| std::env::var_os(k).filter(|v| !v.is_empty()).map(PathBuf::from);
    let base = env("XDG_DATA_HOME")
        .filter(|p| p.is_absolute())
        .or_else(|| env("HOME").map(|h| h.join(".local").join("share")))
        .unwrap_or_default();
    base.join("UEFN-Ducky").join("window_bounds.json")
}

/// One monitor as the Linux window host sees it: its work area in physical pixels and its
/// scale factor (1.0, 1.25, 1.5, 2.0 ...).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Monitor {
    pub area: Rect,
    pub scale: f64,
}

/// Where and how big to open on Linux. `position` is None on Wayland, where a client can't
/// place its own window (the compositor picks the monitor).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Placement {
    pub position: Option<(i32, i32)>,
    pub width: i32,
    pub height: i32,
}

/// Wayland session, and GTK not forced onto XWayland.
pub fn is_wayland(wayland_display: Option<&str>, gdk_backend: Option<&str>) -> bool {
    wayland_display.is_some_and(|d| !d.is_empty()) && !gdk_backend.is_some_and(|b| b.starts_with("x11"))
}

fn scaled(v: i32, scale: f64) -> i32 {
    (v as f64 * scale.max(1.0)).round() as i32
}

/// Linux restore. Saved bounds are physical pixels, as Windows saves them (the same file).
/// The minimum and default sizes are logical, so they grow with the monitor's scale; a
/// window whose top edge is off every screen moves onto the nearest one ([`clamp_to_screens`]).
/// `monitors` primary first.
pub fn place(saved: Option<Bounds>, monitors: &[Monitor], can_position: bool) -> Placement {
    let monitors: Vec<Monitor> = monitors.iter().copied().filter(|m| m.area.width() > 0 && m.area.height() > 0).collect();
    let areas: Vec<Rect> = monitors.iter().map(|m| m.area).collect();
    let primary = monitors.first().copied().unwrap_or(Monitor { area: Rect { left: 0, top: 0, right: 1920, bottom: 1080 }, scale: 1.0 });
    let on = |b: &Bounds| monitors.iter().copied().find(|m| m.area == best_area(b, &areas)).unwrap_or(primary);
    let b = match saved {
        Some(b) => {
            let s = if areas.is_empty() { primary.scale } else { on(&b).scale };
            let b = Bounds { width: b.width.max(scaled(MIN_WIDTH, s)), height: b.height.max(scaled(MIN_HEIGHT, s)), ..b };
            clamp_to_screens(b, &areas)
        }
        None => {
            let a = primary.area;
            let width = scaled(DEFAULT_WIDTH, primary.scale).min(a.width());
            let height = scaled(DEFAULT_HEIGHT, primary.scale).min(a.height());
            Bounds { x: a.left + (a.width() - width) / 2, y: a.top + (a.height() - height) / 2, width, height }
        }
    };
    if can_position {
        return Placement { position: Some((b.x, b.y)), width: b.width, height: b.height };
    }
    // Wayland: the size only, never bigger than the largest screen it could land on.
    let (max_w, max_h) = areas.iter().fold((0, 0), |(w, h), a| (w.max(a.width()), h.max(a.height())));
    let (max_w, max_h) = if areas.is_empty() { (b.width, b.height) } else { (max_w, max_h) };
    Placement { position: None, width: b.width.min(max_w), height: b.height.min(max_h) }
}

fn load(path: &Path) -> Map<String, Value> {
    fs::read_to_string(path)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| match v {
            Value::Object(m) => Some(m),
            _ => None,
        })
        .unwrap_or_default()
}

fn as_int(v: Option<&Value>) -> Option<i32> {
    // Python accepts int or float and truncates with int().
    match v? {
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i32::try_from(i).ok()
            } else {
                let f = n.as_f64()?;
                if f.is_finite() && f.abs() < i32::MAX as f64 {
                    Some(f.trunc() as i32)
                } else {
                    None
                }
            }
        }
        _ => None,
    }
}

/// Python's `get_bounds(key)`: all four numeric fields or nothing.
pub fn get_bounds(path: &Path, key: &str) -> Option<Bounds> {
    let data = load(path);
    let b = data.get(key)?.as_object()?;
    let mut f = [0i32; 4];
    for (i, k) in FIELDS.iter().enumerate() {
        f[i] = as_int(b.get(*k))?;
    }
    Some(Bounds { x: f[0], y: f[1], width: f[2], height: f[3] })
}

/// Python's `save_bounds(key, ...)`: keeps every other key, skips tiny windows.
/// Returns `Ok(false)` when skipped.
pub fn save_bounds(path: &Path, key: &str, b: Bounds) -> io::Result<bool> {
    if b.width < SAVE_MIN_WIDTH || b.height < SAVE_MIN_HEIGHT {
        return Ok(false);
    }
    let mut data = load(path);
    let mut value = Map::new();
    value.insert("x".into(), b.x.into());
    value.insert("y".into(), b.y.into());
    value.insert("width".into(), b.width.into());
    value.insert("height".into(), b.height.into());
    data.insert(key.to_string(), Value::Object(value));
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    let text = serde_json::to_string(&Value::Object(data)).map_err(io::Error::other)?;
    let tmp = path.with_extension("json.launcher-tmp");
    fs::write(&tmp, text)?;
    fs::rename(&tmp, path).or_else(|_| {
        let r = fs::copy(&tmp, path).map(|_| ());
        let _ = fs::remove_file(&tmp);
        r
    })?;
    Ok(true)
}

fn grab_visible(b: &Bounds, areas: &[Rect]) -> bool {
    let strip = Rect { left: b.x, top: b.y, right: b.x + b.width, bottom: b.y + GRAB_HEIGHT.min(b.height) };
    let need_w = GRAB_WIDTH.min(b.width);
    let need_h = GRAB_HEIGHT.min(b.height);
    areas.iter().any(|a| {
        let (w, h) = strip.overlap(a);
        w >= need_w && h >= need_h
    })
}

fn best_area(b: &Bounds, areas: &[Rect]) -> Rect {
    let r = b.rect();
    let by_overlap = areas
        .iter()
        .map(|a| {
            let (w, h) = r.overlap(a);
            (w as i64 * h as i64, *a)
        })
        .max_by_key(|(area, _)| *area);
    if let Some((area, a)) = by_overlap {
        if area > 0 {
            return a;
        }
    }
    // Nothing overlaps: the monitor nearest the window's centre.
    let (cx, cy) = (b.x as i64 + b.width as i64 / 2, b.y as i64 + b.height as i64 / 2);
    *areas
        .iter()
        .min_by_key(|a| {
            let dx = cx - cx.clamp(a.left as i64, a.right as i64);
            let dy = cy - cy.clamp(a.top as i64, a.bottom as i64);
            dx * dx + dy * dy
        })
        .expect("areas is not empty")
}

/// Keep a saved window reachable: a window whose top edge is on a screen stays exactly
/// where it was (spanning monitors is fine); one that is off every screen (a monitor was
/// unplugged, the resolution dropped) moves into the nearest work area, shrunk to fit.
pub fn clamp_to_screens(b: Bounds, areas: &[Rect]) -> Bounds {
    let areas: Vec<Rect> = areas.iter().copied().filter(|a| a.width() > 0 && a.height() > 0).collect();
    if areas.is_empty() || grab_visible(&b, &areas) {
        return b;
    }
    let a = best_area(&b, &areas);
    let width = b.width.min(a.width());
    let height = b.height.min(a.height());
    let x = b.x.clamp(a.left, a.right - width);
    let y = b.y.clamp(a.top, a.bottom - height);
    Bounds { x, y, width, height }
}

/// Bounds to open with: saved (raised to the minimum size, clamped on screen) or the
/// Python default size centred on the first (primary) work area.
pub fn initial_bounds(saved: Option<Bounds>, areas: &[Rect]) -> Bounds {
    match saved {
        Some(b) => clamp_to_screens(
            Bounds { width: b.width.max(MIN_WIDTH), height: b.height.max(MIN_HEIGHT), ..b },
            areas,
        ),
        None => {
            let a = areas.first().copied().unwrap_or(Rect { left: 0, top: 0, right: 1920, bottom: 1080 });
            let width = DEFAULT_WIDTH.min(a.width().max(1));
            let height = DEFAULT_HEIGHT.min(a.height().max(1));
            Bounds { x: a.left + (a.width() - width) / 2, y: a.top + (a.height() - height) / 2, width, height }
        }
    }
}

/// Work areas of every monitor, primary first.
#[cfg(windows)]
pub fn work_areas() -> Vec<Rect> {
    use windows_sys::Win32::Foundation::{BOOL, LPARAM, RECT};
    use windows_sys::Win32::Graphics::Gdi::{EnumDisplayMonitors, GetMonitorInfoW, HDC, HMONITOR, MONITORINFO};

    const MONITORINFOF_PRIMARY: u32 = 1;

    unsafe extern "system" fn each(mon: HMONITOR, _dc: HDC, _r: *mut RECT, data: LPARAM) -> BOOL {
        let out = &mut *(data as *mut Vec<(bool, Rect)>);
        let mut info: MONITORINFO = std::mem::zeroed();
        info.cbSize = std::mem::size_of::<MONITORINFO>() as u32;
        if GetMonitorInfoW(mon, &mut info) != 0 {
            let w = info.rcWork;
            out.push((
                info.dwFlags & MONITORINFOF_PRIMARY != 0,
                Rect { left: w.left, top: w.top, right: w.right, bottom: w.bottom },
            ));
        }
        1
    }

    let mut found: Vec<(bool, Rect)> = Vec::new();
    unsafe {
        EnumDisplayMonitors(std::ptr::null_mut(), std::ptr::null(), Some(each), &mut found as *mut _ as LPARAM);
    }
    found.sort_by_key(|(primary, _)| !*primary);
    found.into_iter().map(|(_, r)| r).collect()
}

#[cfg(not(windows))]
pub fn work_areas() -> Vec<Rect> {
    Vec::new()
}
