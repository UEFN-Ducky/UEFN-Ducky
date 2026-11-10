//! Port choice, the same rule as `frontend.settings.pick_panel_port`.
//!
//! The panel uses a pair: the UI http port (`listener - 1`) and the listener port.
//! Default listener 4200. `UEFN_DUCKY_PANEL_PORT` wins (test sessions) unless Windows reserved
//! that pair: then the next free pair 10 up is used for this run (env updated, nothing saved).
//! Otherwise the
//! saved `panel_port.json` `{"port": N}` (1025 < N < 65535). When Windows reserved either
//! port of the pair (bind answers WSAEACCES 10013), the first free pair of 4300..8300 step
//! 100 is used and saved, so every process reads the same moved port.

use std::net::TcpListener;
use std::path::Path;

pub const DEFAULT_PANEL_LISTENER_PORT: u16 = 4200;
pub const PANEL_PORT_FILE: &str = "panel_port.json";
pub const PORT_ENV: &str = "UEFN_DUCKY_PANEL_PORT";
const WSAEACCES: i32 = 10013;

/// `{"port": N}` from `panel_port.json` text; 0 when missing, bad or out of range.
pub fn parse_saved_port(text: &str) -> u16 {
    let value: serde_json::Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(_) => return 0,
    };
    let port = match value.get("port") {
        Some(serde_json::Value::Number(n)) => n.as_i64().unwrap_or(0),
        Some(serde_json::Value::String(s)) => s.trim().parse::<i64>().unwrap_or(0),
        _ => 0,
    };
    if port > 1025 && port < 65535 {
        port as u16
    } else {
        0
    }
}

/// Saved port under the app data folder (`%LOCALAPPDATA%/UEFN-Ducky`), 0 when none.
pub fn saved_port(app_data: &Path) -> u16 {
    std::fs::read_to_string(app_data.join(PANEL_PORT_FILE))
        .map(|t| parse_saved_port(&t))
        .unwrap_or(0)
}

/// True when Windows refuses this loopback port outright (10013); in use (10048) is not reserved.
pub fn port_reserved(port: u16) -> bool {
    match TcpListener::bind(("127.0.0.1", port)) {
        Ok(_) => false,
        Err(e) => e.raw_os_error() == Some(WSAEACCES) || e.raw_os_error() == Some(13),
    }
}

/// Result of [`choose_listener_port`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PortChoice {
    /// The listener port; the UI port is `listener - 1`.
    pub listener: u16,
    /// True when the pair moved off a reserved range and should be saved.
    pub moved: bool,
}

impl PortChoice {
    pub fn ui_port(&self) -> u16 {
        self.listener - 1
    }
}

/// Pure port rule. `env` is `UEFN_DUCKY_PANEL_PORT`, `saved` the saved port (0 = none).
pub fn choose_listener_port(env: Option<&str>, saved: u16, reserved: impl Fn(u16) -> bool) -> PortChoice {
    if let Some(p) = env.and_then(|s| s.trim().parse::<u16>().ok()).filter(|p| *p > 1) {
        if !(reserved(p - 1) || reserved(p)) {
            return PortChoice { listener: p, moved: false };
        }
        // ponytail: 40 pairs 10 apart clear any one exclusion block; still blocked keeps the pin.
        let found = (1..=40u16)
            .filter_map(|i| p.checked_add(i * 10))
            .find(|q| !(reserved(q - 1) || reserved(*q)))
            .unwrap_or(p);
        return PortChoice { listener: found, moved: found != p };
    }
    let start = if saved != 0 { saved } else { DEFAULT_PANEL_LISTENER_PORT };
    if !(reserved(start - 1) || reserved(start)) {
        return PortChoice { listener: start, moved: false };
    }
    let found = (4300u16..8300)
        .step_by(100)
        .find(|p| !(reserved(p - 1) || reserved(*p)))
        .unwrap_or(start);
    PortChoice { listener: found, moved: found != start }
}

/// Live pick: reads env + `panel_port.json`, probes reservations and saves a moved port.
pub fn pick_panel_port(app_data: &Path) -> PortChoice {
    let env = std::env::var(PORT_ENV).ok().filter(|s| s.trim().parse::<u16>().is_ok_and(|p| p > 1));
    let choice = choose_listener_port(env.as_deref(), saved_port(app_data), port_reserved);
    if choice.moved && env.is_some() {
        // The backend child inherits the moved pin, so both sides agree on the port.
        std::env::set_var(PORT_ENV, choice.listener.to_string());
    } else if choice.moved {
        let _ = std::fs::create_dir_all(app_data);
        let _ = std::fs::write(app_data.join(PANEL_PORT_FILE), format!("{{\"port\": {}}}", choice.listener));
    }
    choice
}
