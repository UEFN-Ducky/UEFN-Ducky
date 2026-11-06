//! Where the living panel is: `%LOCALAPPDATA%/UEFN-Ducky/panel.pid` and its UI port
//! (`frontend/settings.py::PANEL_LISTENER_PORT` minus one).

use std::path::{Path, PathBuf};

pub const APP_DATA_DIR_NAME: &str = "UEFN-Ducky";
pub const PANEL_PID_FILE: &str = "panel.pid";
pub const PANEL_PORT_FILE: &str = "panel_port.json";
pub const PANEL_PORT_ENV: &str = "UEFN_DUCKY_PANEL_PORT";
pub const DEFAULT_PANEL_LISTENER_PORT: u16 = 4200;

#[derive(Debug, Clone)]
pub struct Config {
    /// `%LOCALAPPDATA%/UEFN-Ducky`.
    pub app_data: PathBuf,
    /// The panel UI HTTP port (listener port - 1).
    pub ui_port: u16,
    /// For `~` in argv paths (Python's `expanduser`).
    pub home: Option<PathBuf>,
}

impl Config {
    /// From this process's environment; None without LOCALAPPDATA / APPDATA.
    pub fn from_env() -> Option<Config> {
        let base = non_empty_env("LOCALAPPDATA").or_else(|| non_empty_env("APPDATA"))?;
        let app_data = PathBuf::from(base).join(APP_DATA_DIR_NAME);
        let listener = listener_port(non_empty_env(PANEL_PORT_ENV).as_deref(), &app_data);
        Some(Config {
            ui_port: listener.saturating_sub(1),
            app_data,
            home: non_empty_env("USERPROFILE").or_else(|| non_empty_env("HOME")).map(PathBuf::from),
        })
    }

    pub fn panel_pid_path(&self) -> PathBuf {
        self.app_data.join(PANEL_PID_FILE)
    }
}

fn non_empty_env(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.trim().is_empty())
}

/// `int(env or _saved_panel_port() or 4200)`: the env value, else the moved port Windows
/// reservations forced (`panel_port.json`, only `1025 < port < 65535`), else 4200.
pub fn listener_port(env_value: Option<&str>, app_data: &Path) -> u16 {
    if let Some(p) = env_value.and_then(|v| v.trim().parse::<u16>().ok()).filter(|p| *p > 1) {
        return p;
    }
    std::fs::read_to_string(app_data.join(PANEL_PORT_FILE))
        .ok()
        .and_then(|t| saved_port(&t))
        .unwrap_or(DEFAULT_PANEL_LISTENER_PORT)
}

/// `{"port": N}` -> N when `1025 < N < 65535`.
pub fn saved_port(text: &str) -> Option<u16> {
    let after_key = &text[text.find("\"port\"")? + 6..];
    let after_colon = after_key.trim_start().strip_prefix(':')?.trim_start();
    let digits: String = after_colon.chars().take_while(|c| c.is_ascii_digit()).collect();
    let port: u32 = digits.parse().ok()?;
    (1025 < port && port < 65535).then_some(port as u16)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn saved_port_reads_the_moved_pair() {
        assert_eq!(saved_port(r#"{"port": 4400}"#), Some(4400));
        assert_eq!(saved_port(r#"{"port":5300}"#), Some(5300));
        assert_eq!(saved_port(r#"{"port": 80}"#), None);
        assert_eq!(saved_port(r#"{"port": 99999}"#), None);
        assert_eq!(saved_port("not json"), None);
    }

    #[test]
    fn env_beats_file_beats_default() {
        let dir = std::env::temp_dir().join(format!("ducky-instance-port-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        assert_eq!(listener_port(None, &dir), 4200);
        std::fs::write(dir.join(PANEL_PORT_FILE), r#"{"port": 4400}"#).unwrap();
        assert_eq!(listener_port(None, &dir), 4400);
        assert_eq!(listener_port(Some("5555"), &dir), 5555);
        assert_eq!(listener_port(Some("junk"), &dir), 4400);
        let _ = std::fs::remove_dir_all(dir);
    }
}
