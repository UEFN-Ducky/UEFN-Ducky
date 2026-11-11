use splash::*;

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
