//! Ducky-Launcher: shows Ducky's window fast; all of Ducky's logic stays in the Python backend
//! (`UEFN-Ducky.exe --backend [same argv]`).

pub mod argv;
pub mod console;
pub mod dialog;
pub mod exit;
pub mod page;
pub mod repoint;
pub mod start;
#[cfg(windows)]
mod ui;

use std::ffi::OsString;
use std::time::Duration;

/// How long a second launch waits for a launcher that is already starting Ducky.
pub const LOCK_WAIT: Duration = Duration::from_secs(20);

/// Ducky's app version (the Python `frontend.__version__` this launcher ships with).
pub const APP_VERSION: &str = env!("DUCKY_APP_VERSION");

fn env_is(name: &str, value: &str) -> bool {
    std::env::var(name).is_ok_and(|v| v.trim() == value)
}

/// Run a normal launch with the user's arguments (argv[0] removed, order kept).
/// Returns the process exit code (see [`exit`]).
#[cfg(windows)]
pub fn run(args: Vec<OsString>) -> u8 {
    let exe = std::env::current_exe().unwrap_or_default();
    let app_data = ducky_instance::Config::from_env().map(|c| c.app_data).unwrap_or_default();
    let native_child = env_is(repoint::NATIVE_CHILD_ENV, "1");
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
            spawn.start().or_else(|_| repoint::Spawn::python(repoint::sibling_stub(&exe), &args).start());
        return if started.is_ok() { exit::OK } else { exit::START_ERROR };
    }
    let mut host = ui::UiHost::new(args);
    let plan = host.plan();
    start::launch(&mut host, &plan, !native_child)
}

#[cfg(not(windows))]
pub fn run(_args: Vec<OsString>) -> u8 {
    exit::START_ERROR
}
