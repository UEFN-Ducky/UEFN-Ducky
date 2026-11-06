use std::path::{Path, PathBuf};
use std::process::Command;

use bounds::*;

fn temp_file(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("ducky-bounds-test-{}-{}", std::process::id(), name));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir.join("window_bounds.json")
}

const PRIMARY: Rect = Rect { left: 0, top: 0, right: 1920, bottom: 1040 };
const RIGHT: Rect = Rect { left: 1920, top: 0, right: 3840, bottom: 1040 };

#[test]
fn reads_the_file_python_writes() {
    // Byte for byte what Python's json.dumps(data) writes, focus key and float included.
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/window_bounds.json");
    assert_eq!(get_bounds(&path, MAIN_KEY), Some(Bounds { x: 120, y: 80, width: 1280, height: 800 }));
    assert_eq!(get_bounds(&path, "focus:chat-1"), Some(Bounds { x: -1500, y: 40, width: 900, height: 700 }));
    assert_eq!(get_bounds(&path, "focus:float"), Some(Bounds { x: 10, y: 20, width: 800, height: 600 }));
    assert_eq!(get_bounds(&path, "focus:partial"), None);
    assert_eq!(get_bounds(&path, "focus:strings"), None);
    assert_eq!(get_bounds(&path, "missing"), None);
}

#[test]
fn missing_or_broken_file_is_nothing() {
    let path = temp_file("broken");
    assert_eq!(get_bounds(&path, MAIN_KEY), None);
    std::fs::write(&path, "not json").unwrap();
    assert_eq!(get_bounds(&path, MAIN_KEY), None);
    std::fs::write(&path, "[1,2]").unwrap();
    assert_eq!(get_bounds(&path, MAIN_KEY), None);
}

#[test]
fn save_keeps_other_keys_and_skips_tiny() {
    let path = temp_file("save");
    std::fs::write(&path, r#"{"focus:a": {"x": 1, "y": 2, "width": 300, "height": 200}}"#).unwrap();
    let b = Bounds { x: -5, y: 7, width: 1000, height: 700 };
    assert!(save_bounds(&path, MAIN_KEY, b).unwrap());
    assert_eq!(get_bounds(&path, MAIN_KEY), Some(b));
    assert_eq!(get_bounds(&path, "focus:a"), Some(Bounds { x: 1, y: 2, width: 300, height: 200 }));
    assert!(!save_bounds(&path, MAIN_KEY, Bounds { x: 0, y: 0, width: 199, height: 700 }).unwrap());
    assert!(!save_bounds(&path, MAIN_KEY, Bounds { x: 0, y: 0, width: 700, height: 149 }).unwrap());
    assert_eq!(get_bounds(&path, MAIN_KEY), Some(b));
    // Only Python's four fields per key, nothing new.
    let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    let mut keys: Vec<&String> = v["main"].as_object().unwrap().keys().collect();
    keys.sort();
    assert_eq!(keys, ["height", "width", "x", "y"]);
}

#[test]
fn on_screen_window_stays_put() {
    let b = Bounds { x: 100, y: 100, width: 1200, height: 800 };
    assert_eq!(clamp_to_screens(b, &[PRIMARY]), b);
    // Spanning two monitors is the person's choice.
    let span = Bounds { x: 1500, y: 100, width: 1000, height: 700 };
    assert_eq!(clamp_to_screens(span, &[PRIMARY, RIGHT]), span);
    // Hanging off the bottom/right but grabbable: untouched.
    let hang = Bounds { x: 1700, y: 900, width: 1000, height: 700 };
    assert_eq!(clamp_to_screens(hang, &[PRIMARY]), hang);
}

#[test]
fn unplugged_monitor_moves_window_back() {
    // Saved on the right monitor, now only the primary is there.
    let b = Bounds { x: 2200, y: 100, width: 1200, height: 800 };
    assert_eq!(clamp_to_screens(b, &[PRIMARY]), Bounds { x: 720, y: 100, width: 1200, height: 800 });
    // Title bar above the top of the screen.
    let up = Bounds { x: 100, y: -500, width: 1200, height: 800 };
    assert_eq!(clamp_to_screens(up, &[PRIMARY]), Bounds { x: 100, y: 0, width: 1200, height: 800 });
    // Bigger than the screen left: shrunk to fit.
    let big = Bounds { x: -4000, y: 0, width: 2500, height: 1400 };
    assert_eq!(clamp_to_screens(big, &[PRIMARY]), Bounds { x: 0, y: 0, width: 1920, height: 1040 });
    // Only a sliver of the top edge is visible: moved.
    let sliver = Bounds { x: 1880, y: 10, width: 900, height: 600 };
    assert_eq!(clamp_to_screens(sliver, &[PRIMARY]), Bounds { x: 1020, y: 10, width: 900, height: 600 });
}

#[test]
fn no_monitor_info_leaves_bounds_alone() {
    let b = Bounds { x: 9000, y: 9000, width: 800, height: 600 };
    assert_eq!(clamp_to_screens(b, &[]), b);
}

#[test]
fn initial_bounds_default_and_minimum() {
    assert_eq!(initial_bounds(None, &[PRIMARY]), Bounds { x: 500, y: 200, width: 920, height: 640 });
    let small = Bounds { x: 10, y: 10, width: 300, height: 200 };
    assert_eq!(initial_bounds(Some(small), &[PRIMARY]), Bounds { x: 10, y: 10, width: 700, height: 480 });
}

#[cfg(windows)]
#[test]
fn work_areas_has_a_primary() {
    let areas = work_areas();
    assert!(!areas.is_empty());
    assert!(areas[0].width() > 0 && areas[0].height() > 0);
}

/// A Python that really runs: `$PYTHON`, then `py -3`, `python3`, `python`. Each must pass
/// `--version` (the Windows Store stub doesn't). None works: the test fails, never skips.
fn find_python() -> Vec<String> {
    let mut candidates: Vec<Vec<String>> = Vec::new();
    if let Ok(p) = std::env::var("PYTHON") {
        if !p.trim().is_empty() {
            candidates.push(vec![p]);
        }
    }
    candidates.push(vec!["py".into(), "-3".into()]);
    candidates.push(vec!["python3".into()]);
    candidates.push(vec!["python".into()]);
    for c in &candidates {
        let ok = Command::new(&c[0]).args(&c[1..]).arg("--version").output().map(|o| o.status.success()).unwrap_or(false);
        if ok {
            return c.clone();
        }
    }
    panic!("no working Python: tried {candidates:?} (each must run `--version`); set $PYTHON or install the py launcher");
}

/// Rust writes, Python's own `window_bounds.get_bounds` reads it back.
#[test]
fn python_reads_back_what_rust_writes() {
    let app = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../ducky_app");
    assert!(app.join("frontend/ui_web/window_bounds.py").is_file(), "no ducky_app next to native_launcher at {}", app.display());
    let path = temp_file("python");
    let localappdata = path.parent().unwrap().join("lad");
    let file = localappdata.join("UEFN-Ducky").join("window_bounds.json");
    let b = Bounds { x: -1234, y: 56, width: 1111, height: 777 };
    save_bounds(&file, MAIN_KEY, b).unwrap();
    save_bounds(&file, "focus:x", Bounds { x: 1, y: 2, width: 300, height: 200 }).unwrap();
    let script = "import sys, json\n\
sys.path.insert(0, sys.argv[1])\n\
from frontend.ui_web import window_bounds as wb\n\
wb._use_db = lambda: False\n\
print(json.dumps([wb.get_bounds('main'), wb.get_bounds('focus:x')]))\n\
wb.save_bounds('focus:py', 3, 4, 500, 400)\n";
    let py = find_python();
    let out = Command::new(&py[0])
        .args(&py[1..])
        .arg("-c")
        .arg(script)
        .arg(&app)
        .env("LOCALAPPDATA", &localappdata)
        .output()
        .unwrap_or_else(|e| panic!("could not run {py:?}: {e}"));
    assert!(out.status.success(), "python failed: {}", String::from_utf8_lossy(&out.stderr));
    let got: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(
        got,
        serde_json::json!([
            {"x": -1234, "y": 56, "width": 1111, "height": 777},
            {"x": 1, "y": 2, "width": 300, "height": 200}
        ])
    );
    // And what Python wrote next to it reads back here.
    assert_eq!(get_bounds(&file, "focus:py"), Some(Bounds { x: 3, y: 4, width: 500, height: 400 }));
    assert_eq!(get_bounds(&file, MAIN_KEY), Some(b));
}
