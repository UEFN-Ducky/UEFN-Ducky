//! Direct starts (a shortcut straight to the launcher, no `native_switch` waiting) record
//! themselves in `launcher/starts.json` the way `native_switch.record_start` does, so
//! `start_summary` counts them; and nobody reads their `ready-<pid>.json`, so they write
//! none. Leftover ready files from older builds are swept on start.

use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

/// `native_switch.STARTS_KEPT`.
pub const STARTS_KEPT: usize = 200;
/// Ready files older than this are leftovers (`run_native` waits 15 s at most).
pub const READY_MAX_AGE: Duration = Duration::from_secs(10 * 60);

pub fn launcher_dir(app_data: &Path) -> PathBuf {
    app_data.join("launcher")
}

/// `native_switch.EXIT_REASONS`: the fallback reason for a launcher exit code.
pub fn exit_reason(code: u8) -> &'static str {
    match code {
        20 => "webview2",
        21 => "protocol",
        22 => "crash_loop",
        23 => "no_hello",
        24 => "start_error",
        _ => "exit_other",
    }
}

/// The starts.json reason for a fallback: a crash loop names its slug
/// (`crash_loop.port_blocked`, like `native_switch`), everything else is [`exit_reason`].
pub fn fallback_reason(code: u8, error: &str) -> String {
    match exit_reason(code) {
        "crash_loop" => format!("crash_loop.{}", supervisor::crash_slug(error)),
        other => other.to_string(),
    }
}

/// The row `native_switch.record_start(mode, window_ms, painted_ms, reason)` builds.
pub fn row(native: bool, window_ms: Option<u64>, painted_ms: Option<u64>, reason: &str, ts: f64) -> Value {
    json!({
        "ts": ts,
        "mode": if native { "native" } else { "python" },
        "window_ms": window_ms,
        "painted_ms": painted_ms,
        "fallback": reason,
    })
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// Append one start to `launcher/starts.json`, keeping the last [`STARTS_KEPT`] (atomic
/// replace, like `native_switch._write_json`). A broken file starts over, as in Python.
pub fn record(app_data: &Path, native: bool, window_ms: Option<u64>, painted_ms: Option<u64>, reason: &str) -> std::io::Result<()> {
    let dir = launcher_dir(app_data);
    let path = dir.join("starts.json");
    let mut rows: Vec<Value> = std::fs::read(&path)
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .and_then(|v| v.get("starts").and_then(Value::as_array).cloned())
        .map(|a| a.into_iter().filter(Value::is_object).collect())
        .unwrap_or_default();
    rows.push(row(native, window_ms, painted_ms, reason, now()));
    let keep = rows.len().saturating_sub(STARTS_KEPT);
    rows.drain(..keep);
    std::fs::create_dir_all(&dir)?;
    let tmp = dir.join(format!("starts.json.{}.tmp", std::process::id()));
    std::fs::write(&tmp, json!({"starts": rows}).to_string())?;
    std::fs::rename(&tmp, &path)
}

/// Remove `ready-*.json` in `launcher/` older than `max_age`. Returns how many went.
pub fn sweep_ready(app_data: &Path, max_age: Duration) -> usize {
    let Ok(entries) = std::fs::read_dir(launcher_dir(app_data)) else {
        return 0;
    };
    let now = SystemTime::now();
    entries
        .flatten()
        .filter(|e| {
            let name = e.file_name();
            let name = name.to_string_lossy();
            name.starts_with("ready-") && name.ends_with(".json")
        })
        .filter(|e| {
            e.metadata()
                .and_then(|m| m.modified())
                .ok()
                .and_then(|t| now.duration_since(t).ok())
                .is_some_and(|age| age > max_age)
        })
        .filter(|e| std::fs::remove_file(e.path()).is_ok())
        .count()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("ducky-starts-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn rows(app_data: &Path) -> Vec<Value> {
        let v: Value = serde_json::from_slice(&std::fs::read(launcher_dir(app_data).join("starts.json")).unwrap()).unwrap();
        v["starts"].as_array().unwrap().clone()
    }

    /// test_native_switch.py: `record_start("native", 95.4, 640, "")` and
    /// `record_start("python", None, None, "no_hello")`.
    #[test]
    fn rows_match_native_switch_record_start() {
        let mut native = row(true, Some(95), Some(640), "", 1.5);
        // Python's record_start order (serde_json preserve_order keeps it).
        assert_eq!(native.as_object().unwrap().keys().collect::<Vec<_>>(), ["ts", "mode", "window_ms", "painted_ms", "fallback"]);
        native["ts"] = json!(0);
        assert_eq!(native, json!({"ts": 0, "mode": "native", "window_ms": 95, "painted_ms": 640, "fallback": ""}));
        let fb = row(false, None, None, exit_reason(23), 2.0);
        assert_eq!(fb, json!({"ts": 2.0, "mode": "python", "window_ms": null, "painted_ms": null, "fallback": "no_hello"}));
    }

    #[test]
    fn exit_reasons_match_native_switch() {
        let got: Vec<_> = [20, 21, 22, 23, 24, 3].map(exit_reason).to_vec();
        assert_eq!(got, ["webview2", "protocol", "crash_loop", "no_hello", "start_error", "exit_other"]);
    }

    #[test]
    fn crash_loop_reason_names_the_slug() {
        let e = "backend crashed 3 times in 60 s (backend ended before hello (exit 1): PermissionError: [WinError 10013] x)";
        assert_eq!(fallback_reason(22, e), "crash_loop.port_blocked");
        assert_eq!(fallback_reason(22, ""), "crash_loop.other");
        assert_eq!(fallback_reason(23, e), "no_hello");
    }

    #[test]
    fn record_appends_keeps_200_and_survives_a_broken_file() {
        let d = scratch("record");
        std::fs::create_dir_all(launcher_dir(&d)).unwrap();
        std::fs::write(launcher_dir(&d).join("starts.json"), "{broken").unwrap();
        record(&d, true, Some(40), Some(900), "").unwrap();
        assert_eq!(rows(&d).len(), 1);
        // Python's rows (and stray non-objects) are kept as they are.
        let old: Vec<Value> = (0..250).map(|i| json!({"ts": i, "mode": "python", "fallback": ""})).collect();
        let mut with_junk = old.clone();
        with_junk.push(json!("junk"));
        std::fs::write(launcher_dir(&d).join("starts.json"), json!({"starts": with_junk}).to_string()).unwrap();
        record(&d, false, None, None, "webview2").unwrap();
        let r = rows(&d);
        assert_eq!(r.len(), STARTS_KEPT);
        assert_eq!(r[0]["ts"], 51);
        assert_eq!(r.last().unwrap()["fallback"], "webview2");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn sweep_removes_only_old_ready_files() {
        let d = scratch("sweep");
        let dir = launcher_dir(&d);
        std::fs::create_dir_all(&dir).unwrap();
        for f in ["ready-1.json", "ready-2.json", "starts.json", "crash_loop.json"] {
            std::fs::write(dir.join(f), "{}").unwrap();
        }
        // Everything is fresh: nothing goes.
        assert_eq!(sweep_ready(&d, READY_MAX_AGE), 0);
        // Everything counts as old: only the ready files go.
        std::thread::sleep(Duration::from_millis(20));
        assert_eq!(sweep_ready(&d, Duration::from_millis(1)), 2);
        assert!(dir.join("starts.json").is_file() && dir.join("crash_loop.json").is_file());
        assert!(!dir.join("ready-1.json").exists());
        let _ = std::fs::remove_dir_all(&d);
    }
}
