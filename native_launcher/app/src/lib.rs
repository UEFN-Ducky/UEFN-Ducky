//! Ducky-Launcher: shows Ducky's window fast; all of Ducky's logic stays in the Python backend
//! (`UEFN-Ducky.exe --backend [same argv]`).

pub mod argv;
pub mod console;
pub mod exit;

use std::ffi::OsString;

/// Ducky's app version (the Python `frontend.__version__` this launcher ships with).
pub const APP_VERSION: &str = env!("DUCKY_APP_VERSION");

/// Run a normal launch with the user's arguments (argv[0] removed, order kept).
/// Returns the process exit code (see [`exit`]).
pub fn run(args: Vec<OsString>) -> u8 {
    let _backend_argv = argv::backend_argv(&args);
    // The window, backend and protocol crates plug in here; until then a launch is a start
    // error so `native_switch.run_native` falls back to the Python window.
    exit::START_ERROR
}
