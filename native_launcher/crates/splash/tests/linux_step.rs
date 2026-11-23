//! The waiting splash: shown first, closed once on ready (or the cap), never shown hidden.

use std::time::Duration;

use splash::{step, Splash, Step};

const CAP: Duration = Duration::from_secs(30);

#[test]
fn shows_then_closes_on_ready() {
    let mut s = Splash::new(true);
    assert!(s.visible());
    assert_eq!(step(&mut s, false, Duration::from_millis(100), CAP), Step::Wait);
    assert!(s.visible());
    assert_eq!(step(&mut s, true, Duration::from_secs(2), CAP), Step::Close);
    assert!(!s.visible());
    // Closed for good: a later poll never brings it back.
    assert_eq!(step(&mut s, false, Duration::from_secs(3), CAP), Step::Close);
}

#[test]
fn the_cap_closes_a_splash_whose_panel_never_says_ready() {
    let mut s = Splash::new(true);
    assert_eq!(step(&mut s, false, CAP, CAP), Step::Close);
    assert!(!s.visible());
}

#[test]
fn a_hidden_start_never_shows_it() {
    let mut s = Splash::new(splash::should_show(&["Ducky-Launcher", "--background"], None));
    assert!(!s.visible());
    assert_eq!(step(&mut s, false, Duration::ZERO, CAP), Step::Close);
}
