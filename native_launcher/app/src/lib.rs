//! Ducky-Launcher: shows Ducky's window fast; all of Ducky's logic stays in the Python backend
//! (`UEFN-Ducky.exe --backend [same argv]`).

pub mod argv;
pub mod console;
pub mod dialog;
pub mod exit;
pub mod start;
#[cfg(windows)]
mod ui;

use std::ffi::OsString;
use std::time::Duration;

/// How long a second launch waits for a launcher that is already starting Ducky.
pub const LOCK_WAIT: Duration = Duration::from_secs(20);

/// Ducky's app version (the Python `frontend.__version__` this launcher ships with).
pub const APP_VERSION: &str = env!("DUCKY_APP_VERSION");

/// Run a normal launch with the user's arguments (argv[0] removed, order kept).
/// Returns the process exit code (see [`exit`]).
#[cfg(windows)]
pub fn run(args: Vec<OsString>) -> u8 {
    let mut host = ui::UiHost::new(args);
    let plan = host.plan();
    start::launch(&mut host, &plan)
}

#[cfg(not(windows))]
pub fn run(_args: Vec<OsString>) -> u8 {
    exit::START_ERROR
}
