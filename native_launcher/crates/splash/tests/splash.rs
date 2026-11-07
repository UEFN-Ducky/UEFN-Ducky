use splash::*;

#[test]
fn logo_asset_is_220_square_bgra_on_theme() {
    assert_eq!(LOGO_BGRA.len(), (LOGO_PX * LOGO_PX * 4) as usize);
    // Top-left corner is outside the duck: the theme background, opaque.
    assert_eq!(&LOGO_BGRA[0..4], &[0x0A, 0x0A, 0x0A, 0xFF]);
    // And the duck is actually there: some pixel differs from the background.
    assert!(LOGO_BGRA.chunks(4).any(|p| p[0..3] != [0x0A, 0x0A, 0x0A]));
}

#[test]
fn logo_is_centred_at_96_dpi() {
    assert_eq!(logo_rect(920, 640, 96), Rect { left: 350, top: 210, right: 570, bottom: 430 });
}

#[test]
fn logo_scales_with_dpi() {
    let r = logo_rect(1840, 1280, 192);
    assert_eq!((r.width(), r.height()), (440, 440));
    assert_eq!((r.left, r.top), (700, 420));
    // dpi 0 means 96.
    assert_eq!(logo_rect(920, 640, 0), logo_rect(920, 640, 96));
}

#[test]
fn logo_shrinks_in_a_small_window_and_never_goes_negative() {
    let r = logo_rect(300, 200, 96);
    assert_eq!(r, Rect { left: 60, top: 10, right: 240, bottom: 190 });
    let tiny = logo_rect(10, 10, 96);
    assert_eq!((tiny.width(), tiny.height()), (0, 0));
    let neg = logo_rect(-5, -5, 96);
    assert_eq!((neg.width(), neg.height()), (0, 0));
}

#[test]
fn background_start_shows_no_splash() {
    assert!(should_show(&["Ducky-Launcher.exe"], None));
    assert!(should_show(&["Ducky-Launcher.exe", "uefn-ducky://open"], Some("0")));
    assert!(!should_show(&["Ducky-Launcher.exe", "--background"], None));
    assert!(!should_show(&["Ducky-Launcher.exe"], Some("1")));
    // argv[0] is the exe, never a flag.
    assert!(should_show(&["--background"], None));
    assert!(!Splash::new(should_show(&["x", "--background"], None)).visible());
}

#[test]
fn splash_goes_away_once_painted() {
    let mut s = Splash::new(true);
    assert!(s.visible());
    assert!(s.on_painted());
    assert!(!s.visible());
    assert!(!s.on_painted(), "second paint event is a no-op");
    let mut hidden = Splash::new(false);
    assert!(!hidden.on_painted());
}

#[cfg(windows)]
#[test]
fn paint_draws_into_a_memory_dc() {
    use windows_sys::Win32::Graphics::Gdi::*;
    unsafe {
        let screen = GetDC(std::ptr::null_mut());
        let dc = CreateCompatibleDC(screen);
        let bmp = CreateCompatibleBitmap(screen, 920, 640);
        let old = SelectObject(dc, bmp);
        paint(dc, 920, 640, 96);
        let corner = GetPixel(dc, 0, 0);
        assert_eq!(corner, 0x000A0A0A, "background is the theme colour");
        // No logo: the whole window, centre included, is the theme background.
        for y in (0..640).step_by(16) {
            for x in (0..920).step_by(16) {
                assert_eq!(GetPixel(dc, x, y), 0x000A0A0A, "no logo at {x},{y}");
            }
        }
        SelectObject(dc, old);
        DeleteObject(bmp);
        DeleteDC(dc);
        ReleaseDC(std::ptr::null_mut(), screen);
    }
}
