//! Single instance: a launch goes to the living Ducky in `panel.pid` (its argv forwarded over
//! the same loopback hand-off Python uses) instead of starting another one.
//!
//! Mirrors `frontend/ui_web/webview_app.py::_hand_off_to_living_panel` and
//! `frontend/open_files.py::try_handoff_to_running`. The launcher never closes another
//! process: a stale or stuck panel is left to the Python backend's own reclaim.

pub mod config;
pub mod http;
pub mod lock;
pub mod payload;
pub mod pidfile;
pub mod process;

use std::ffi::OsString;
use std::time::{Duration, Instant};

pub use config::Config;
pub use lock::InstanceLock;

/// The autostart flag (`frontend/autostart.py::BACKGROUND_FLAG`).
pub const BACKGROUND_FLAG: &str = "--background";
/// The living panel's hand-off endpoint (`open_files.try_handoff_to_running`).
pub const HANDOFF_PATH: &str = "/__panel_open_files";

/// First burst: short, so a stale panel.pid never stalls a cold start.
pub const FIRST_TRIES: u32 = 4;
pub const FIRST_DELAY: Duration = Duration::from_millis(150);
pub const FIRST_TIMEOUT: Duration = Duration::from_millis(350);
/// A Ducky that is alive but not answering is starting or busy: wait for it this long.
pub const LIVING_PANEL_WAIT: Duration = Duration::from_secs(45);
pub const RETRY_TIMEOUT: Duration = Duration::from_secs(2);
/// Pause between waiting tries (a refused connect returns at once).
pub const RETRY_PAUSE: Duration = Duration::from_millis(250);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Launch {
    /// The living Ducky took the launch: exit 0.
    HandedOff,
    /// A sign-in (`--background`) start found Ducky already open: leave it as it is, exit 0.
    LeftRunning,
    /// No living Ducky took it: start one.
    StartFresh,
}

/// What the hand-off needs from the machine; tests swap in a fake.
pub trait System {
    /// `pid` is a running process (an exited one still held open is not).
    fn pid_alive(&self, pid: u32) -> bool;
    /// `pid` is a running Ducky (`UEFN-Ducky*`); an unknown name counts as Ducky.
    fn is_ducky(&self, pid: u32) -> bool;
    /// POST `body` to the panel's hand-off endpoint; true on a 2xx answer.
    fn post(&self, port: u16, body: &[u8], timeout: Duration) -> bool;
    fn sleep(&self, d: Duration);
    fn now(&self) -> Instant;
}

/// The real machine.
pub struct RealSystem;

impl System for RealSystem {
    fn pid_alive(&self, pid: u32) -> bool {
        process::pid_alive(pid)
    }
    fn is_ducky(&self, pid: u32) -> bool {
        process::is_ducky(pid)
    }
    fn post(&self, port: u16, body: &[u8], timeout: Duration) -> bool {
        http::post_json(port, HANDOFF_PATH, body, timeout)
    }
    fn sleep(&self, d: Duration) {
        std::thread::sleep(d)
    }
    fn now(&self) -> Instant {
        Instant::now()
    }
}

/// Decide this launch: hand it to the living Ducky, leave a running one alone, or start fresh.
/// `args` are the user's arguments (argv[0] removed).
pub fn decide_launch(args: &[OsString], cfg: &Config, sys: &dyn System) -> Launch {
    let Some(pid) = pidfile::read_panel_pid(&cfg.panel_pid_path()) else {
        return Launch::StartFresh;
    };
    if !sys.pid_alive(pid) {
        return Launch::StartFresh;
    }
    if args.iter().any(|a| a == BACKGROUND_FLAG) && sys.is_ducky(pid) {
        return Launch::LeftRunning;
    }
    let body = payload::handoff_body(args, cfg.home.as_deref());
    if hand_off(pid, cfg.ui_port, &body, sys) {
        Launch::HandedOff
    } else {
        Launch::StartFresh
    }
}

fn hand_off(pid: u32, port: u16, body: &[u8], sys: &dyn System) -> bool {
    for attempt in 0..FIRST_TRIES {
        if sys.post(port, body, FIRST_TIMEOUT) {
            return true;
        }
        if attempt + 1 < FIRST_TRIES {
            sys.sleep(FIRST_DELAY);
        }
    }
    let deadline = sys.now() + LIVING_PANEL_WAIT;
    while sys.now() < deadline && sys.is_ducky(pid) {
        if sys.post(port, body, RETRY_TIMEOUT) {
            return true;
        }
        sys.sleep(RETRY_PAUSE);
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    use std::path::PathBuf;

    struct Fake {
        alive: bool,
        ducky: bool,
        answer_on: Option<u32>,
        posts: RefCell<Vec<(u16, Vec<u8>, Duration)>>,
        clock: Cell<Duration>,
        base: Instant,
    }

    impl Fake {
        fn new(alive: bool, answer_on: Option<u32>) -> Fake {
            Fake { alive, ducky: alive, answer_on, posts: RefCell::new(vec![]), clock: Cell::new(Duration::ZERO), base: Instant::now() }
        }
        fn tries(&self) -> usize {
            self.posts.borrow().len()
        }
    }

    impl System for Fake {
        fn pid_alive(&self, _pid: u32) -> bool {
            self.alive
        }
        fn is_ducky(&self, _pid: u32) -> bool {
            self.ducky
        }
        fn post(&self, port: u16, body: &[u8], timeout: Duration) -> bool {
            self.posts.borrow_mut().push((port, body.to_vec(), timeout));
            let n = self.tries() as u32;
            let ok = self.answer_on.is_some_and(|k| n >= k);
            if !ok {
                self.clock.set(self.clock.get() + timeout);
            }
            ok
        }
        fn sleep(&self, d: Duration) {
            self.clock.set(self.clock.get() + d);
        }
        fn now(&self) -> Instant {
            self.base + self.clock.get()
        }
    }

    fn cfg_with_pid(text: Option<&str>) -> (Config, PathBuf) {
        let dir = std::env::temp_dir().join(format!("ducky-instance-test-{}-{}", std::process::id(), rand_suffix()));
        std::fs::create_dir_all(&dir).unwrap();
        if let Some(t) = text {
            std::fs::write(dir.join("panel.pid"), t).unwrap();
        }
        (Config { app_data: dir.clone(), ui_port: 4199, home: None }, dir)
    }

    fn rand_suffix() -> u128 {
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()
    }

    #[test]
    fn no_panel_pid_starts_fresh_without_a_try() {
        let (cfg, dir) = cfg_with_pid(None);
        let sys = Fake::new(true, Some(1));
        assert_eq!(decide_launch(&[], &cfg, &sys), Launch::StartFresh);
        assert_eq!(sys.tries(), 0);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn old_format_panel_pid_hands_off_to_the_living_ducky() {
        let (cfg, dir) = cfg_with_pid(Some("4242\r\n"));
        let sys = Fake::new(true, Some(1));
        let args = vec![OsString::from("uefn-ducky://store/x")];
        assert_eq!(decide_launch(&args, &cfg, &sys), Launch::HandedOff);
        let posts = sys.posts.borrow();
        assert_eq!(posts.len(), 1);
        assert_eq!(posts[0].0, 4199);
        assert_eq!(posts[0].1, br#"{"paths": [], "links": ["uefn-ducky://store/x"]}"#.to_vec());
        assert_eq!(posts[0].2, FIRST_TIMEOUT);
        drop(posts);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn a_stale_panel_pid_is_not_tried() {
        let (cfg, dir) = cfg_with_pid(Some("4242"));
        let sys = Fake::new(false, Some(1));
        assert_eq!(decide_launch(&[], &cfg, &sys), Launch::StartFresh);
        assert_eq!(sys.tries(), 0);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn a_starting_ducky_is_waited_for_not_given_up_on() {
        let (cfg, dir) = cfg_with_pid(Some("4242"));
        let sys = Fake::new(true, Some(7));
        assert_eq!(decide_launch(&[], &cfg, &sys), Launch::HandedOff);
        assert_eq!(sys.tries(), 7);
        let posts = sys.posts.borrow();
        assert!(posts[..4].iter().all(|p| p.2 == FIRST_TIMEOUT));
        assert!(posts[4..].iter().all(|p| p.2 == RETRY_TIMEOUT));
        drop(posts);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn a_ducky_that_never_answers_is_left_after_the_wait() {
        let (cfg, dir) = cfg_with_pid(Some("4242"));
        let sys = Fake::new(true, None);
        assert_eq!(decide_launch(&[], &cfg, &sys), Launch::StartFresh);
        assert!(sys.clock.get() >= LIVING_PANEL_WAIT);
        assert!(sys.clock.get() < LIVING_PANEL_WAIT + Duration::from_secs(5));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn a_pid_that_is_not_ducky_gets_only_the_first_burst() {
        let (cfg, dir) = cfg_with_pid(Some("4242"));
        let mut sys = Fake::new(true, None);
        sys.ducky = false;
        assert_eq!(decide_launch(&[], &cfg, &sys), Launch::StartFresh);
        assert_eq!(sys.tries(), FIRST_TRIES as usize);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn a_background_start_leaves_a_running_ducky_alone() {
        let (cfg, dir) = cfg_with_pid(Some("4242"));
        let sys = Fake::new(true, Some(1));
        let args = vec![OsString::from("--background")];
        assert_eq!(decide_launch(&args, &cfg, &sys), Launch::LeftRunning);
        assert_eq!(sys.tries(), 0);
        let _ = std::fs::remove_dir_all(dir);
    }
}
