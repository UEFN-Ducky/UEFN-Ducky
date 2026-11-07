//! Restart policy and the crash-loop file.
//!
//! Restart policy: a crashed backend is restarted up to 2 times within 60 s; the third
//! crash in that window gives up (exit 22).
//!
//! `launcher/crash_loop.json` `{failures:[ts...], until: ts}` (ts = Unix seconds, like
//! Python's `time.time()`): each give-up adds a failure; 3 failures within 10 min set
//! `until = now + 24 h`, and while `now < until` the `auto` setting uses Python.

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const MAX_RESTARTS: usize = 2;
pub const RESTART_WINDOW: Duration = Duration::from_secs(60);
pub const LOOP_FAILURES: usize = 3;
pub const LOOP_WINDOW_S: f64 = 600.0;
pub const LOOP_BACKOFF_S: f64 = 24.0 * 3600.0;
pub const CRASH_LOOP_FILE: &str = "crash_loop.json";

/// When the hello wait gives up: `silence` after the last backend line, never past `cap`
/// after the spawn. A slow PC that keeps sending `stage` lines keeps the launcher.
pub fn hello_deadline(spawned: Instant, last_line: Instant, silence: Duration, cap: Duration) -> Instant {
    (last_line + silence).min(spawned + cap)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Decision {
    Restart,
    GiveUp,
}

/// Crash times within the window decide restart or give up.
#[derive(Debug, Clone, Default)]
pub struct RestartPolicy {
    crashes: Vec<Instant>,
}

impl RestartPolicy {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn on_crash(&mut self, now: Instant) -> Decision {
        self.crashes.retain(|t| now.saturating_duration_since(*t) < RESTART_WINDOW);
        self.crashes.push(now);
        if self.crashes.len() > MAX_RESTARTS {
            Decision::GiveUp
        } else {
            Decision::Restart
        }
    }

    pub fn recent_crashes(&self) -> usize {
        self.crashes.len()
    }
}

pub fn unix_now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct CrashLoop {
    pub failures: Vec<f64>,
    pub until: f64,
}

impl CrashLoop {
    pub fn path(app_data: &Path) -> PathBuf {
        app_data.join("launcher").join(CRASH_LOOP_FILE)
    }

    pub fn parse(text: &str) -> CrashLoop {
        let v: Value = serde_json::from_str(text).unwrap_or(Value::Null);
        let failures = v
            .get("failures")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_f64).collect())
            .unwrap_or_default();
        let until = v.get("until").and_then(Value::as_f64).unwrap_or(0.0);
        CrashLoop { failures, until }
    }

    pub fn to_json(&self) -> String {
        json!({"failures": self.failures, "until": self.until}).to_string()
    }

    pub fn load(app_data: &Path) -> CrashLoop {
        std::fs::read_to_string(Self::path(app_data)).map(|t| Self::parse(&t)).unwrap_or_default()
    }

    pub fn save(&self, app_data: &Path) -> std::io::Result<()> {
        let path = Self::path(app_data);
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let tmp = path.with_extension(format!("json.{}.tmp", std::process::id()));
        std::fs::write(&tmp, self.to_json())?;
        std::fs::rename(&tmp, &path).or_else(|_| {
            let r = std::fs::write(&path, self.to_json());
            let _ = std::fs::remove_file(&tmp);
            r
        })
    }

    pub fn active(&self, now: f64) -> bool {
        now < self.until
    }

    /// Add one failure at `now`; true when this starts (or keeps) the 24 h backoff.
    pub fn record_failure(&mut self, now: f64) -> bool {
        self.failures.retain(|t| now - *t < LOOP_WINDOW_S && *t <= now);
        self.failures.push(now);
        if self.failures.len() >= LOOP_FAILURES {
            self.until = now + LOOP_BACKOFF_S;
        }
        self.active(now)
    }
}

/// Load, add a failure now, save. Returns whether the backoff is active.
pub fn record_crash_loop_failure(app_data: &Path) -> bool {
    let mut cl = CrashLoop::load(app_data);
    let active = cl.record_failure(unix_now());
    let _ = cl.save(app_data);
    active
}
