//! The launcher's part of the crash record: `%LOCALAPPDATA%/UEFN-Ducky/crash/boot.json`,
//! which `frontend/boot_report.py` writes and reads.
//!
//! Additive only: the launcher sets one extra key, [`LAUNCHER_KEY`], and never touches
//! the keys the reader uses (`version`, `started`, `pid`, `os`, `python`, `state`,
//! `stages`, `error`, ...). It never creates boot.json either: a record holding only the
//! launcher's key would read as "closed while starting" and show a crash pop-up for a
//! launch that simply fell back to the Python window.

use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

/// The one key the launcher adds to boot.json.
pub const LAUNCHER_KEY: &str = "launcher";
/// The keys `boot_report.begin` writes and `build_report` reads; the launcher keeps them.
pub const READER_KEYS: [&str; 7] = ["version", "started", "pid", "os", "python", "state", "stages"];
/// Same cap as `boot_report.stage` (`del stages[:-60]`).
pub const MAX_STAGES: usize = 60;

/// The launcher's exit codes (the team contract).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Exit {
    Ok = 0,
    WebView2Missing = 20,
    ProtocolMismatch = 21,
    CrashLoop = 22,
    NoHello = 23,
    StartError = 24,
}

impl Exit {
    pub fn code(self) -> i32 {
        self as i32
    }

    pub fn from_code(code: i32) -> Option<Exit> {
        Some(match code {
            0 => Exit::Ok,
            20 => Exit::WebView2Missing,
            21 => Exit::ProtocolMismatch,
            22 => Exit::CrashLoop,
            23 => Exit::NoHello,
            24 => Exit::StartError,
            _ => return None,
        })
    }

    /// The word written next to the code (and readable in a crash report).
    pub fn reason(self) -> &'static str {
        match self {
            Exit::Ok => "ok",
            Exit::WebView2Missing => "webview2_missing",
            Exit::ProtocolMismatch => "protocol_mismatch",
            Exit::CrashLoop => "crash_loop",
            Exit::NoHello => "no_hello",
            Exit::StartError => "start_error",
        }
    }
}

/// `%LOCALAPPDATA%/UEFN-Ducky/crash/boot.json` (same fallbacks as `boot_report._dir`).
pub fn boot_json_path() -> Option<PathBuf> {
    let base = std::env::var_os("LOCALAPPDATA").or_else(|| std::env::var_os("APPDATA"))?;
    Some(PathBuf::from(base).join("UEFN-Ducky").join("crash").join("boot.json"))
}

fn now_s() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn round2(x: f64) -> f64 {
    (x * 100.0).round() / 100.0
}

/// One launcher run: its stages (seconds since it started, like `boot_report.stage`)
/// and, at the end, its exit code.
#[derive(Clone, Debug)]
pub struct LauncherRecord {
    pub pid: u32,
    pub version: String,
    pub started: f64,
    t0: Instant,
    pub stages: Vec<(String, f64)>,
    pub backend_pid: Option<u32>,
    pub exit: Option<i32>,
    pub error: String,
}

impl LauncherRecord {
    pub fn new(version: &str) -> LauncherRecord {
        LauncherRecord {
            pid: std::process::id(),
            version: version.to_string(),
            started: now_s(),
            t0: Instant::now(),
            stages: vec![("launch".to_string(), 0.0)],
            backend_pid: None,
            exit: None,
            error: String::new(),
        }
    }

    pub fn stage(&mut self, name: &str) {
        self.stage_at(name, self.t0.elapsed().as_secs_f64());
    }

    pub fn stage_at(&mut self, name: &str, seconds: f64) {
        self.stages.push((name.to_string(), round2(seconds)));
        let extra = self.stages.len().saturating_sub(MAX_STAGES);
        self.stages.drain(..extra);
    }

    pub fn finish(&mut self, exit: Exit, error: &str) {
        self.exit = Some(exit.code());
        self.error = error.chars().rev().take(2000).collect::<Vec<_>>().into_iter().rev().collect();
    }

    pub fn to_json(&self) -> Value {
        let mut v = json!({
            "pid": self.pid,
            "version": self.version,
            "started": self.started,
            "stages": self.stages.iter().map(|(n, s)| json!([n, s])).collect::<Vec<_>>(),
        });
        let obj = v.as_object_mut().expect("object");
        if let Some(pid) = self.backend_pid {
            obj.insert("backend_pid".into(), json!(pid));
        }
        if let Some(code) = self.exit {
            obj.insert("exit_code".into(), json!(code));
            let reason = Exit::from_code(code).map(Exit::reason).unwrap_or("unknown");
            obj.insert("exit_reason".into(), json!(reason));
        }
        if !self.error.is_empty() {
            obj.insert("error".into(), json!(self.error));
        }
        v
    }
}

/// `record` with the launcher's key set; every other key exactly as it was. None when
/// `record` is not a JSON object (the launcher leaves it alone).
pub fn merge(record: &Value, launcher: &LauncherRecord) -> Option<Value> {
    let mut obj: Map<String, Value> = record.as_object()?.clone();
    obj.insert(LAUNCHER_KEY.to_string(), launcher.to_json());
    Some(Value::Object(obj))
}

/// What [`write`] did.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Written {
    /// The launcher key was added to the record.
    Merged,
    /// No boot.json, or it isn't an object: nothing written.
    NoRecord,
    /// boot.json belongs to another process than this launcher's backend: nothing written.
    OtherLaunch,
}

/// Add the launcher's key to boot.json at `path`, atomically (tmp + rename, like
/// `boot_report._write_state`). With a backend pid, only that backend's record is changed;
/// without one (the launcher ended before a backend said hello) the latest record is.
pub fn write(path: &Path, launcher: &LauncherRecord) -> std::io::Result<Written> {
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Written::NoRecord),
        Err(e) => return Err(e),
    };
    let Ok(record) = serde_json::from_str::<Value>(&text) else {
        return Ok(Written::NoRecord);
    };
    if let Some(backend) = launcher.backend_pid {
        if record.get("pid").and_then(Value::as_u64) != Some(u64::from(backend)) {
            return Ok(Written::OtherLaunch);
        }
    }
    let Some(merged) = merge(&record, launcher) else {
        return Ok(Written::NoRecord);
    };
    let tmp = path.with_extension("launcher.tmp");
    std::fs::write(&tmp, serde_json::to_vec(&merged).map_err(std::io::Error::other)?)?;
    std::fs::rename(&tmp, path)?;
    Ok(Written::Merged)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn python_record() -> Value {
        // What boot_report.begin() + stage() + mark("ok") leave behind.
        json!({
            "version": "1.2.471", "started": 1793900000.5, "pid": 4242,
            "os": "Windows-11-10.0.26300-SP0", "python": "3.12.7", "state": "ok",
            "stages": [["launch", 0.0], ["window", 1.2]],
            "heartbeat": {"at": 1793900030.0}, "error": "boom"
        })
    }

    #[test]
    fn exit_codes_round_trip() {
        for code in [0, 20, 21, 22, 23, 24] {
            let exit = Exit::from_code(code).unwrap();
            assert_eq!(exit.code(), code);
        }
        assert_eq!(Exit::from_code(25), None);
        assert_eq!(Exit::WebView2Missing.reason(), "webview2_missing");
    }

    #[test]
    fn merge_keeps_reader_keys() {
        let before = python_record();
        let mut rec = LauncherRecord::new("1.2.471");
        rec.stage_at("window", 0.104);
        rec.finish(Exit::NoHello, "no hello within 15 s");
        let after = merge(&before, &rec).unwrap();
        for (k, v) in before.as_object().unwrap() {
            assert_eq!(after.get(k), Some(v), "key {k} changed");
        }
        for k in READER_KEYS {
            assert!(after.get(k).is_some(), "lost {k}");
        }
        let l = &after[LAUNCHER_KEY];
        assert_eq!(l["exit_code"], 23);
        assert_eq!(l["exit_reason"], "no_hello");
        assert_eq!(l["stages"][1], json!(["window", 0.1]));
        assert_eq!(after.as_object().unwrap().len(), before.as_object().unwrap().len() + 1);
        assert!(merge(&json!([1, 2]), &rec).is_none());
    }

    #[test]
    fn every_failure_code_is_recorded() {
        for exit in [Exit::WebView2Missing, Exit::ProtocolMismatch, Exit::CrashLoop, Exit::NoHello, Exit::StartError] {
            let mut rec = LauncherRecord::new("1.2.471");
            rec.finish(exit, "");
            let v = rec.to_json();
            assert_eq!(v["exit_code"], exit.code());
            assert_eq!(v["exit_reason"], exit.reason());
            assert!(v.get("error").is_none());
        }
    }

    #[test]
    fn stages_are_capped() {
        let mut rec = LauncherRecord::new("x");
        for i in 0..100 {
            rec.stage_at(&format!("s{i}"), i as f64);
        }
        assert_eq!(rec.stages.len(), MAX_STAGES);
        assert_eq!(rec.stages.last().unwrap().0, "s99");
    }
}
