//! Start with Windows. The Run value (`frontend/autostart.py`) keeps naming
//! `UEFN-Ducky.exe --background`; when that start hands over to the launcher, the
//! launcher starts hidden: no window, no splash, the backend runs and the tray shows.
//!
//! The launcher never writes or removes the Run value: only the installed Python app
//! manages it. [`RunKey`] has a read method only, so no launcher code path can write it.

/// `autostart.BACKGROUND_FLAG`.
pub const BACKGROUND_FLAG: &str = "--background";
/// `autostart.START_HIDDEN_ENV`: the backend learns it started in the background.
pub const START_HIDDEN_ENV: &str = "UEFN_DUCKY_START_HIDDEN";
/// `autostart.RUN_KEY` (under HKCU) and `autostart.VALUE_NAME`.
pub const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
pub const VALUE_NAME: &str = "UEFN Ducky";

/// What this start shows.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StartPlan {
    pub background: bool,
    pub show_window: bool,
    pub show_splash: bool,
    pub start_backend: bool,
    pub show_tray: bool,
    /// The arguments with `--background` taken out (what the backend gets).
    pub argv: Vec<String>,
    /// Extra environment for the backend.
    pub backend_env: Vec<(String, String)>,
}

/// Remove `--background` from `argv` (past argv[0]); true when it was there.
/// Same as `autostart.take_background_flag`.
pub fn take_background_flag(argv: &mut Vec<String>) -> bool {
    let found = argv.iter().skip(1).any(|a| a == BACKGROUND_FLAG);
    if found {
        let first = argv.first().cloned();
        let rest: Vec<String> = argv.iter().skip(1).filter(|a| *a != BACKGROUND_FLAG).cloned().collect();
        argv.clear();
        argv.extend(first);
        argv.extend(rest);
    }
    found
}

/// The decision: `--background` (or the hidden-start env already set by the Python
/// front) means hidden to tray; anything else is a normal start.
pub fn plan(argv: &[String], hidden_env: Option<&str>) -> StartPlan {
    let mut args = argv.to_vec();
    let background = take_background_flag(&mut args) || hidden_env == Some("1");
    StartPlan {
        background,
        show_window: !background,
        show_splash: !background,
        start_backend: true,
        show_tray: true,
        argv: args,
        backend_env: if background {
            vec![(START_HIDDEN_ENV.to_string(), "1".to_string())]
        } else {
            Vec::new()
        },
    }
}

/// The Run key, read only.
pub trait RunKey {
    /// The value's data, None when it isn't there.
    fn read(&self, name: &str) -> Option<String>;
}

/// Whether Start with Windows is on (the Run value is there and asks for `--background`).
/// For status only; it changes nothing.
pub fn enabled(reg: &dyn RunKey) -> bool {
    reg.read(VALUE_NAME).is_some_and(|cmd| cmd.contains(BACKGROUND_FLAG))
}

/// The real HKCU Run key.
#[cfg(windows)]
pub struct WindowsRunKey;

#[cfg(windows)]
impl RunKey for WindowsRunKey {
    fn read(&self, name: &str) -> Option<String> {
        // `reg query` keeps this crate free of registry bindings; the launcher reads it only
        // for status, never on the window's path.
        let out = std::process::Command::new("reg")
            .args(["query", &format!(r"HKCU\{RUN_KEY}"), "/v", name])
            .output()
            .ok()?;
        if !out.status.success() {
            return None;
        }
        parse_reg_query(&String::from_utf8_lossy(&out.stdout), name)
    }
}

/// The data of `name` in `reg query` output (`    UEFN Ducky    REG_SZ    "C:\..." --background`).
pub fn parse_reg_query(text: &str, name: &str) -> Option<String> {
    text.lines().find_map(|line| {
        let rest = line.trim_start().strip_prefix(name)?;
        let rest = rest.trim_start();
        let (kind, data) = rest.split_once(char::is_whitespace)?;
        kind.starts_with("REG_").then(|| data.trim().to_string())
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::HashMap;

    /// A registry that records every access; writes would land in `writes`.
    #[derive(Default)]
    struct MockRegistry {
        values: HashMap<String, String>,
        reads: RefCell<Vec<String>>,
        writes: RefCell<Vec<(String, String)>>,
    }

    impl MockRegistry {
        #[allow(dead_code)]
        fn set(&self, name: &str, data: &str) {
            self.writes.borrow_mut().push((name.into(), data.into()));
        }
    }

    impl RunKey for MockRegistry {
        fn read(&self, name: &str) -> Option<String> {
            self.reads.borrow_mut().push(name.into());
            self.values.get(name).cloned()
        }
    }

    fn args(a: &[&str]) -> Vec<String> {
        a.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn background_starts_hidden_to_tray() {
        let p = plan(&args(&["Ducky-Launcher.exe", "--background", "C:/a.verse"]), None);
        assert!(p.background && !p.show_window && !p.show_splash && p.start_backend && p.show_tray);
        assert_eq!(p.argv, args(&["Ducky-Launcher.exe", "C:/a.verse"]));
        assert_eq!(p.backend_env, vec![(START_HIDDEN_ENV.to_string(), "1".to_string())]);
    }

    #[test]
    fn normal_start_shows_window() {
        let p = plan(&args(&["Ducky-Launcher.exe", "C:/a.verse"]), None);
        assert!(!p.background && p.show_window && p.show_splash && p.start_backend && p.show_tray);
        assert!(p.backend_env.is_empty());
        // argv[0] named --background is not the flag.
        assert!(!plan(&args(&["--background"]), None).background);
    }

    #[test]
    fn hidden_env_from_python_front_counts() {
        assert!(plan(&args(&["x"]), Some("1")).background);
        assert!(!plan(&args(&["x"]), Some("0")).background);
    }

    #[test]
    fn take_flag_matches_python() {
        let mut a = args(&["UEFN-Ducky.exe", "--background", "C:/a.verse", "--background"]);
        assert!(take_background_flag(&mut a));
        assert_eq!(a, args(&["UEFN-Ducky.exe", "C:/a.verse"]));
        assert!(!take_background_flag(&mut a));
    }

    #[test]
    fn start_never_writes_the_run_key() {
        let mut reg = MockRegistry::default();
        reg.values.insert(VALUE_NAME.into(), r#""C:\Apps\UEFN-Ducky.exe" --background"#.into());
        let p = plan(&args(&["Ducky-Launcher.exe", "--background"]), None);
        assert!(p.background);
        assert!(enabled(&reg));
        assert!(!enabled(&MockRegistry::default()));
        assert_eq!(reg.reads.borrow().as_slice(), [VALUE_NAME.to_string()]);
        assert!(reg.writes.borrow().is_empty(), "the launcher wrote the Run key");
    }

    #[test]
    fn parses_reg_query_output() {
        let out = "\r\nHKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\r\n    UEFN Ducky    REG_SZ    \"C:\\Apps\\UEFN-Ducky.exe\" --background\r\n\r\n";
        assert_eq!(parse_reg_query(out, VALUE_NAME).as_deref(), Some(r#""C:\Apps\UEFN-Ducky.exe" --background"#));
        assert_eq!(parse_reg_query(out, "Other"), None);
    }
}
