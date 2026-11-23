//! `place`: the Linux restore, on fake monitor layouts.

use bounds::{is_wayland, place, Bounds, Monitor, Placement, Rect};

fn mon(left: i32, top: i32, w: i32, h: i32, scale: f64) -> Monitor {
    Monitor { area: Rect { left, top, right: left + w, bottom: top + h }, scale }
}

/// A 1080p primary at 100% and a 4K second monitor at 150% to its right.
fn two() -> Vec<Monitor> {
    vec![mon(0, 0, 1920, 1080, 1.0), mon(1920, 0, 3840, 2160, 1.5)]
}

#[test]
fn reopens_on_the_second_monitor_at_150_percent() {
    let saved = Bounds { x: 2500, y: 200, width: 1800, height: 1200 };
    assert_eq!(place(Some(saved), &two(), true), Placement { position: Some((2500, 200)), width: 1800, height: 1200 });
}

#[test]
fn minimum_size_grows_with_fractional_scale() {
    let saved = Bounds { x: 2500, y: 200, width: 700, height: 480 };
    // 700x480 logical at 1.5 is 1050x720 physical.
    assert_eq!(place(Some(saved), &two(), true), Placement { position: Some((2500, 200)), width: 1050, height: 720 });
    let at_125 = [mon(0, 0, 2560, 1440, 1.25)];
    let p = place(Some(Bounds { x: 10, y: 10, width: 300, height: 300 }), &at_125, true);
    assert_eq!((p.width, p.height), (875, 600));
}

#[test]
fn an_unplugged_monitor_brings_the_window_back_to_the_primary() {
    let saved = Bounds { x: 2500, y: 200, width: 1800, height: 1200 };
    let p = place(Some(saved), &two()[..1], true);
    assert_eq!(p, Placement { position: Some((120, 0)), width: 1800, height: 1080 });
}

#[test]
fn wayland_restores_the_size_only() {
    let saved = Bounds { x: 2500, y: 200, width: 1800, height: 1200 };
    assert_eq!(place(Some(saved), &two(), false), Placement { position: None, width: 1800, height: 1200 });
}

#[test]
fn nothing_saved_opens_the_scaled_default_centred_on_the_primary() {
    let hidpi = [mon(0, 0, 2880, 1800, 1.5)];
    assert_eq!(place(None, &hidpi, true), Placement { position: Some((750, 420)), width: 1380, height: 960 });
    assert_eq!(place(None, &[], true), Placement { position: Some((500, 220)), width: 920, height: 640 });
}

#[test]
fn wayland_detection() {
    assert!(is_wayland(Some("wayland-0"), None));
    assert!(!is_wayland(Some("wayland-0"), Some("x11")));
    assert!(!is_wayland(None, None));
    assert!(!is_wayland(Some(""), None));
}
