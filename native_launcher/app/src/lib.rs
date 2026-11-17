//! Ducky-Launcher: shows Ducky's window fast; all of Ducky's logic stays in the Python backend
//! (`UEFN-Ducky.exe --backend [same argv]`).

pub mod argv;
pub mod console;
pub mod dialog;
pub mod exit;
pub mod page;
pub mod repoint;
pub mod start;
pub mod starts;
pub mod trim;
#[cfg(windows)]
mod ui;

use std::ffi::OsString;
use std::time::Duration;

/// How long a second launch waits for a launcher that is already starting Ducky: as long as
/// that launcher itself waits for its backend's hello, plus a margin. A dead owner's lock is
/// abandoned and taken at once, so only a living, still-starting Ducky is waited for.
/// Was 5 s: the first start after an update took ~40 s (Nov 10 2026), so a second click
/// opened a second Ducky beside the one still "loading".
pub const LOCK_WAIT: Duration =
    Duration::from_secs(supervisor::HELLO_CAP.as_secs() + 10);

/// Ducky's app version (the Python `frontend.__version__` this launcher ships with).
pub const APP_VERSION: &str = env!("DUCKY_APP_VERSION");

fn env_is(name: &str, value: &str) -> bool {
    std::env::var(name).is_ok_and(|v| v.trim() == value)
}

const BROWSER_ARGS_ENV: &str = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS";

/// The running Ducky notes how full the graphics card is every 30 s (`launcher/gpu.json`,
/// `frontend/busy_pc.py`: same 90% and 10 minutes there). True when the note is that fresh
/// and says the card was full: this start draws its window without the card, because a
/// window drawn on a full card froze 127 times in an hour (Nov 17 2026).
pub fn gpu_was_full(note: &str, now: f64) -> bool {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(note) else {
        return false;
    };
    let (Some(share), Some(at)) = (v["share"].as_f64(), v["at"].as_f64()) else {
        return false;
    };
    share >= 0.90 && (-60.0..600.0).contains(&(now - at))
}

/// Run a normal launch with the user's arguments (argv[0] removed, order kept).
/// Returns the process exit code (see [`exit`]).
#[cfg(windows)]
pub fn run(args: Vec<OsString>) -> u8 {
    let exe = std::env::current_exe().unwrap_or_default();
    let app_data = ducky_instance::Config::from_env().map(|c| c.app_data).unwrap_or_default();
    let native_child = env_is(repoint::NATIVE_CHILD_ENV, "1");
    // ponytail: a read_dir of the small launcher/ folder on the start path; move it to a
    // thread if it ever shows in the timings.
    starts::sweep_ready(&app_data, starts::READY_MAX_AGE);
    let instead = repoint::before_start(
        &exe,
        &args,
        env_is(repoint::HOP_ENV, "1"),
        native_child,
        &app_data,
        APP_VERSION,
        || repoint::native_setting(&app_data),
        || supervisor::policy::CrashLoop::load(&app_data).active(supervisor::policy::unix_now()),
    );
    if let Some(spawn) = instead {
        // A hop that can't start still gets the user a window: the Python one beside us.
        let started =
            spawn.start().or_else(|_| repoint::Spawn::python(repoint::sibling_stub(&exe), &args, "hop_failed").start());
        return if started.is_ok() { exit::OK } else { exit::START_ERROR };
    }
    // Before any WebView2 starts, and inherited by the backend (its browser panes).
    let note = std::fs::read_to_string(app_data.join("launcher").join("gpu.json")).unwrap_or_default();
    if std::env::var_os(BROWSER_ARGS_ENV).is_none() && gpu_was_full(&note, supervisor::policy::unix_now()) {
        std::env::set_var(BROWSER_ARGS_ENV, format!("{} --disable-gpu", window::config::DEFAULT_ENV_BROWSER_ARGS));
    }
    let mut host = ui::UiHost::new(args);
    let plan = host.plan();
    start::launch(&mut host, &plan, !native_child)
}

#[cfg(not(windows))]
pub fn run(_args: Vec<OsString>) -> u8 {
    exit::START_ERROR
}

#[cfg(test)]
mod tests {
    use super::gpu_was_full;

    #[test]
    fn a_fresh_note_of_a_full_card_draws_without_it() {
        assert!(gpu_was_full(r#"{"share": 0.96, "at": 1000.0}"#, 1030.0));
        assert!(!gpu_was_full(r#"{"share": 0.61, "at": 1000.0}"#, 1030.0)); // room on the card
        assert!(!gpu_was_full(r#"{"share": 0.96, "at": 1000.0}"#, 1700.0)); // an old note
        assert!(!gpu_was_full(r#"{"share": 0.96, "at": 9000.0}"#, 1030.0)); // from the future
        assert!(!gpu_was_full("", 1030.0));
        assert!(!gpu_was_full(r#"{"share": "x"}"#, 1030.0));
    }
}
