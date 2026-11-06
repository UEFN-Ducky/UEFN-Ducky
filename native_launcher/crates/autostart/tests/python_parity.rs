//! The constants must equal `ducky_app/frontend/autostart.py`; the crate must hold no
//! registry write call.

use std::path::PathBuf;

fn read(rel: &str) -> String {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(rel);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

#[test]
fn constants_equal_python() {
    let src = read("../../../ducky_app/frontend/autostart.py");
    assert!(src.contains(&format!("BACKGROUND_FLAG = \"{}\"", autostart::BACKGROUND_FLAG)));
    assert!(src.contains(&format!("START_HIDDEN_ENV = \"{}\"", autostart::START_HIDDEN_ENV)));
    assert!(src.contains(&format!("VALUE_NAME = \"{}\"", autostart::VALUE_NAME)));
    assert!(src.contains(&format!("RUN_KEY = r\"{}\"", autostart::RUN_KEY)));
}

#[test]
fn source_has_no_registry_writes() {
    let src = read("src/lib.rs");
    for banned in ["RegSetValue", "RegDeleteValue", "RegCreateKey", "\"add\"", "\"delete\""] {
        assert!(!src.contains(banned), "autostart crate contains {banned}");
    }
}
