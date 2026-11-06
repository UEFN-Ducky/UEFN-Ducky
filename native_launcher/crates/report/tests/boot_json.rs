//! boot.json on disk: the launcher adds its key and keeps what boot_report.py reads.

use std::path::PathBuf;

use report::{write, Exit, LauncherRecord, Written, LAUNCHER_KEY, READER_KEYS};
use serde_json::{json, Value};

fn temp_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("ducky-report-test-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn record(pid: u32) -> Value {
    json!({"version":"1.2.471","started":1.5,"pid":pid,"os":"Windows","python":"3.12",
           "state":"starting","stages":[["launch",0.0]]})
}

#[test]
fn merges_into_its_backends_record() {
    let dir = temp_dir("merge");
    let path = dir.join("boot.json");
    std::fs::write(&path, record(77).to_string()).unwrap();
    let mut rec = LauncherRecord::new("1.2.471");
    rec.backend_pid = Some(77);
    rec.stage("hello");
    rec.finish(Exit::ProtocolMismatch, "protocol 2");
    assert_eq!(write(&path, &rec).unwrap(), Written::Merged);
    let after: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    for k in READER_KEYS {
        assert_eq!(after[k], record(77)[k], "{k}");
    }
    assert_eq!(after[LAUNCHER_KEY]["exit_code"], 21);
    assert_eq!(after[LAUNCHER_KEY]["backend_pid"], 77);
    assert!(!dir.join("boot.launcher.tmp").exists());
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn never_creates_a_record() {
    let dir = temp_dir("missing");
    let path = dir.join("boot.json");
    let mut rec = LauncherRecord::new("1.2.471");
    rec.finish(Exit::WebView2Missing, "");
    assert_eq!(write(&path, &rec).unwrap(), Written::NoRecord);
    assert!(!path.exists());
    std::fs::write(&path, "not json").unwrap();
    assert_eq!(write(&path, &rec).unwrap(), Written::NoRecord);
    assert_eq!(std::fs::read_to_string(&path).unwrap(), "not json");
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn leaves_another_launch_alone() {
    let dir = temp_dir("other");
    let path = dir.join("boot.json");
    let text = record(10).to_string();
    std::fs::write(&path, &text).unwrap();
    let mut rec = LauncherRecord::new("1.2.471");
    rec.backend_pid = Some(11);
    assert_eq!(write(&path, &rec).unwrap(), Written::OtherLaunch);
    assert_eq!(std::fs::read_to_string(&path).unwrap(), text);
    std::fs::remove_dir_all(&dir).unwrap();
}
