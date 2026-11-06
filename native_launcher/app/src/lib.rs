//! Ducky-Launcher: shows Ducky's window fast; all of Ducky's logic stays in the Python backend
//! (`UEFN-Ducky.exe --backend [same argv]`).

pub mod argv;
pub mod console;
pub mod exit;

use std::ffi::OsString;
use std::time::Duration;

use ducky_instance::{Config, InstanceLock, Launch, RealSystem};

/// How long a second launch waits for a launcher that is already starting Ducky.
pub const LOCK_WAIT: Duration = Duration::from_secs(20);

/// Ducky's app version (the Python `frontend.__version__` this launcher ships with).
pub const APP_VERSION: &str = env!("DUCKY_APP_VERSION");

/// Run a normal launch with the user's arguments (argv[0] removed, order kept).
/// Returns the process exit code (see [`exit`]).
pub fn run(args: Vec<OsString>) -> u8 {
    // Held on this thread while this launch starts Ducky; a second launch waits on it, then
    // hands off to the Ducky this one started.
    let _lock = InstanceLock::acquire(LOCK_WAIT);
    if let Some(cfg) = Config::from_env() {
        match ducky_instance::decide_launch(&args, &cfg, &RealSystem) {
            Launch::HandedOff | Launch::LeftRunning => return exit::OK,
            Launch::StartFresh => {}
        }
    }
    let _backend_argv = argv::backend_argv(&args);
    // The window, backend and protocol crates plug in here; until then a launch is a start
    // error so `native_switch.run_native` falls back to the Python window.
    exit::START_ERROR
}
